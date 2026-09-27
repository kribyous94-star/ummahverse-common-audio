"""`extract` : deux langues d'un même montage -> commun estimé + deux résidus.

Sorties : `common.wav` (base temporelle de A), `a_only.wav` (base de A),
`b_only.wav` (base de B) et `common.json`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from .align import (DelayEstimate, build_offset_map, estimate_channel_offsets,
                    estimate_global_delay)
from .audio_io import Workspace, check_layout_compatibility, choose_common_rate, load_input
from .blocks import BlockPlan, align_spectrum, delay_spectrum, read_extended, split_delay
from .errors import UmmahAudioError
from .ffmpeg_tools import describe_environment
from .metadata import (base_payload, limitations_notice, stream_descriptor, write_json)
from .paths import (ensure_distinct_outputs, ensure_inputs_untouched,
                    ensure_writable_directory)
from .perf import Stopwatch
from .progress import Progress, SubProgress
from .separate import (BlockDiagnostics, SeparationParams, SeparationSummary,
                       estimate_common_spectrum)
from .sinks import AccumulatorSink, StreamingCommonSink
from .stft import StftConfig
from .transfer import estimate_transfer
from .verdict import judge

#: Noms de fichiers produits, fixés par le contrat CLI.
COMMON_NAME = "common.wav"
A_ONLY_NAME = "a_only.wav"
B_ONLY_NAME = "b_only.wav"
METADATA_NAME = "common.json"
#: Au-delà, la chronologie détaillée est décimée pour garder `common.json` lisible.
TIMELINE_LIMIT = 4000


@dataclass
class ExtractOptions:
    """Tous les réglages de `extract`, avec leurs valeurs par défaut documentées."""

    input_a: Path
    input_b: Path
    out_dir: Path
    stream_a: int | None = None
    stream_b: int | None = None
    sample_rate: int | None = None
    method: str = "wiener"
    transfer_mode: str = "band"
    win_length: int = 8192
    hop: int | None = None
    smooth_frames: int = 3
    smooth_bins: int = 2
    bias_correction: float = 0.0
    magnitude_cap: float = 1.5
    block_seconds: float = 6.0
    max_offset_s: float = 30.0
    local_search_s: float = 0.35
    confidence_threshold: float = 0.12
    coherence_threshold: float = 0.9
    transfer_estimator: str = "power"
    allow_unknown_layout: bool = False
    wav_format: str = "float32"
    workspace_dir: Path | None = None

    def stft_config(self, sample_rate: int) -> StftConfig:
        return StftConfig(
            sample_rate=sample_rate, win_length=self.win_length,
            hop=self.hop or self.win_length // 4,
            smooth_frames=self.smooth_frames, smooth_bins=self.smooth_bins,
        )

    def separation_params(self) -> SeparationParams:
        return SeparationParams(method=self.method, bias_correction=self.bias_correction,
                                magnitude_cap=self.magnitude_cap)


@dataclass
class ExtractResult:
    common_path: Path
    a_only_path: Path
    b_only_path: Path
    metadata_path: Path
    metadata: dict = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    verdict: object = None

    @property
    def outputs(self) -> list[Path]:
        return [self.common_path, self.a_only_path, self.b_only_path, self.metadata_path]


def run_extract(options: ExtractOptions, progress: Progress | None = None) -> ExtractResult:
    """Exécute l'extraction complète. Toutes les erreurs prévues sont `UmmahAudioError`."""
    progress = progress or Progress()
    watch = Stopwatch()
    warnings: list[str] = []

    # Contrôle avant toute écriture : FFmpeg tronque un fichier de sortie dès qu'il
    # l'ouvre, donc vérifier après coup ne servirait à rien.
    out_dir = ensure_writable_directory(options.out_dir)
    produced = [out_dir / name
                for name in (COMMON_NAME, A_ONLY_NAME, B_ONLY_NAME, METADATA_NAME)]
    ensure_distinct_outputs(produced)
    ensure_inputs_untouched([options.input_a, options.input_b], produced)

    with Workspace(options.workspace_dir) as workspace:
        progress.stage("Décodage des entrées")
        from .ffmpeg_tools import probe_audio_streams, select_stream

        streams_a = probe_audio_streams(Path(options.input_a))
        streams_b = probe_audio_streams(Path(options.input_b))
        chosen_a = select_stream(Path(options.input_a), streams_a, options.stream_a)
        chosen_b = select_stream(Path(options.input_b), streams_b, options.stream_b)
        warnings += check_layout_compatibility(chosen_a, chosen_b, options.allow_unknown_layout)
        rate = choose_common_rate([chosen_a, chosen_b], options.sample_rate)

        audio_a = load_input(options.input_a, workspace, "a", chosen_a.audio_index, rate)
        progress.step(0.08, "entrée A décodée")
        audio_b = load_input(options.input_b, workspace, "b", chosen_b.audio_index, rate)
        progress.step(0.15, "entrée B décodée")
        watch.mark("decode")

        if audio_a.frames < 4 * options.win_length:
            raise UmmahAudioError(
                "L'entrée A est trop courte pour être analysée "
                f"({audio_a.duration_s:.2f} s).",
                f"Il faut au moins {4 * options.win_length / rate:.2f} s à cette fréquence.",
            )

        config = options.stft_config(rate)
        plan = BlockPlan.for_config(config, options.block_seconds)
        params = options.separation_params()

        progress.stage("Alignement global")
        track_a, analysis_rate = audio_a.analysis_track()
        track_b, _ = audio_b.analysis_track()
        global_delay = estimate_global_delay(track_a, track_b, analysis_rate,
                                            max_delay_s=options.max_offset_s)
        scaled = DelayEstimate(
            delay=global_delay.delay * rate / analysis_rate,
            integer_delay=int(round(global_delay.integer_delay * rate / analysis_rate)),
            confidence=global_delay.confidence, peak_ratio=global_delay.peak_ratio,
            method=global_delay.method,
        )
        progress.log(f"Décalage global : {scaled.delay / rate * 1000:.2f} ms "
                     f"(confiance {scaled.confidence:.3f})")
        if scaled.confidence < 0.05:
            warnings.append(
                f"Corrélation globale très faible ({scaled.confidence:.3f}) : les deux "
                "entrées ne semblent pas issues du même montage."
            )
        progress.step(0.20, "décalage global estimé")

        progress.stage("Alignement local et repérage des coupes")
        offset_map = build_offset_map(
            audio_a, audio_b, plan.advance, scaled,
            confidence_threshold=options.confidence_threshold,
            local_search_s=options.local_search_s,
            tracks=(track_a, track_b, analysis_rate),
            progress=SubProgress(progress, 0.20, 0.34),
        )
        del track_a, track_b
        warnings += offset_map.notes
        audio_a.release_all()
        audio_b.release_all()
        watch.mark("align")

        progress.stage("Décalage propre à chaque canal")
        channel_offsets, channel_confidence = estimate_channel_offsets(
            audio_a, audio_b, offset_map, plan,
            progress=SubProgress(progress, 0.34, 0.38))
        audio_a.release_all()
        audio_b.release_all()
        if np.any(np.abs(channel_offsets) > 1):
            detail = ", ".join(
                f"canal {i} : {o / rate * 1000:+.1f} ms"
                for i, o in enumerate(channel_offsets) if abs(o) > 1)
            progress.log("Décalages par canal : " + detail)
            if np.max(np.abs(channel_offsets)) > 0.002 * rate:
                warnings.append(
                    "Les canaux ne sont pas synchrones entre les deux versions ("
                    + detail + ") : chacun est recalé séparément.")
        watch.mark("channels")

        progress.stage("Estimation du gain et de l'égalisation entre versions")
        transfer = estimate_transfer(
            audio_a, audio_b, offset_map, config, plan, mode=options.transfer_mode,
            coherence_threshold=options.coherence_threshold,
            estimator=options.transfer_estimator, channel_offsets=channel_offsets,
            progress=SubProgress(progress, 0.38, 0.42),
        )
        warnings += transfer.warnings
        progress.log("Gain large bande par canal : "
                     + ", ".join(f"{g:.4f}" for g in transfer.broadband_gain))
        # Les sondes de gain sont réparties dans tout le film : mêmes précautions.
        audio_a.release_all()
        audio_b.release_all()
        watch.mark("transfer")

        progress.stage("Estimation du commun bloc par bloc")
        summary, timeline, measurements = _process_blocks(
            audio_a, audio_b, offset_map, config, plan, params, transfer, workspace,
            out_dir, options, channel_offsets, SubProgress(progress, 0.42, 0.98),
            warnings)
        watch.mark("separate")
        progress.step(1.0, "terminé")

        verdict = judge(
            mean_coherence=summary.diagnostics.debiased_coherence(config),
            reliable_fraction=transfer.reliable_fraction,
            comparable_fraction=offset_map.comparable_ratio,
            alignment_method=scaled.method,
            alignment_confidence=scaled.confidence)
        if not verdict.usable:
            warnings.insert(0, verdict.headline + " " + verdict.detail)

        metadata = _build_metadata(options, audio_a, audio_b, config, plan, params,
                                   offset_map, transfer, summary, timeline, measurements,
                                   warnings, watch, scaled)
        metadata["common_bed_verdict"] = verdict.as_dict()
        metadata_path = write_json(out_dir / METADATA_NAME, metadata)

    return ExtractResult(
        common_path=out_dir / COMMON_NAME, a_only_path=out_dir / A_ONLY_NAME,
        b_only_path=out_dir / B_ONLY_NAME, metadata_path=metadata_path,
        metadata=metadata, warnings=warnings, verdict=verdict,
    )


def _process_blocks(audio_a, audio_b, offset_map, config: StftConfig, plan: BlockPlan,
                    params: SeparationParams, transfer, workspace, out_dir: Path,
                    options: ExtractOptions, channel_offsets, progress: Progress,
                    warnings: list[str]) -> tuple[SeparationSummary, list[dict], dict]:
    """Boucle principale : estime le commun, l'écrit et en déduit les deux résidus."""
    sft = config.build()
    summary = SeparationSummary()
    timeline: list[dict] = []
    starts = offset_map.starts
    extended = plan.block_frames + 2 * plan.margin
    channel_offsets = np.asarray(channel_offsets, dtype=np.float64)
    channel_shift = np.rint(channel_offsets).astype(int)
    channel_span = int(max(np.abs(channel_shift).max(initial=0), 1)) + 1

    sink_a = StreamingCommonSink(audio_a, out_dir / COMMON_NAME, out_dir / A_ONLY_NAME,
                                options.wav_format)
    sink_b = AccumulatorSink(workspace, "common_b", audio_b.frames, audio_b.channels,
                             audio_b.sample_rate)
    try:
        for index, start in enumerate(starts):
            progress.check_cancelled()
            progress.step(index / max(starts.size, 1),
                          f"bloc {index + 1}/{starts.size}")
            start = int(start)
            delay = float(offset_map.delays[index])
            integer, fractional = split_delay(delay)
            weights = plan.weights(start, audio_a.frames)
            comparable = bool(offset_map.comparable[index])

            if not comparable:
                zeros = np.zeros((plan.block_frames, audio_a.channels), dtype=np.float64)
                sink_a.add(start, zeros, weights)
                sink_b.add(start + integer, zeros, weights)
                summary.blocks_skipped += 1
                timeline.append(_timeline_entry(start, audio_a.sample_rate, delay,
                                               offset_map, index, None))
                continue

            block_a = read_extended(audio_a, start, plan)
            # B est lu large : chaque canal y est ensuite découpé à son propre
            # décalage, sans quoi les surrounds d'un 5.1 remixé ne s'annulent pas.
            wide_b = audio_b.read(start - plan.margin + integer - channel_span,
                                  extended + 2 * channel_span)
            common_a = np.zeros((extended, audio_a.channels), dtype=np.float64)
            common_b = np.zeros((extended, audio_a.channels), dtype=np.float64)
            block_diagnostics = BlockDiagnostics()
            for channel in range(audio_a.channels):
                shift = int(channel_shift[channel])
                extra = float(channel_offsets[channel] - shift)
                lo = channel_span + shift
                spec_a = sft.stft(block_a[:, channel].astype(np.float64))
                spec_b = align_spectrum(
                    sft.stft(wide_b[lo:lo + extended, channel].astype(np.float64)),
                    fractional + extra, config)
                spec_c, diagnostics = estimate_common_spectrum(
                    spec_a, spec_b, transfer.response[channel], config, params)
                common_a[:, channel] = sft.istft(spec_c, k0=0, k1=extended)
                spec_c_b = delay_spectrum(spec_c * transfer.response[channel][:, None],
                                          fractional + extra, config)
                common_b[:, channel] = sft.istft(spec_c_b, k0=0, k1=extended)
                summary.diagnostics.merge(diagnostics)
                block_diagnostics.merge(diagnostics)

            useful = slice(plan.margin, plan.margin + plan.block_frames)
            sink_a.add(start, common_a[useful], weights)
            # Le commun rendu dans le repère de B : chaque canal est décalé de son
            # propre retard, tous écrits à la même position pour garder un seul
            # accumulateur de poids.
            rendered = np.empty((plan.block_frames, audio_b.channels), dtype=np.float64)
            for channel in range(audio_b.channels):
                shift = int(channel_shift[channel])
                lo = plan.margin - shift
                rendered[:, channel] = common_b[lo:lo + plan.block_frames, channel]
            sink_b.add(start + integer, rendered, weights)
            # Les entrées ne sont plus relues en arrière : on rend les pages au système
            # pour que le pic mémoire ne suive pas la durée du film.
            audio_a.release_until(start - 2 * plan.block_frames)
            audio_b.release_until(start + integer - 2 * plan.block_frames)
            sink_b.release_until(start + integer - 4 * plan.block_frames)
            summary.blocks_processed += 1
            timeline.append(_timeline_entry(start, audio_a.sample_rate, delay, offset_map,
                                           index, block_diagnostics))

        progress.step(0.95, "écriture de common.wav et a_only.wav")
        sink_a.finish()
        untreated_b, stats_b = sink_b.write_residual(
            audio_b, out_dir / B_ONLY_NAME, options.wav_format, progress)
    except BaseException:
        sink_a.abort()
        raise

    measurements = {
        "a": {
            "untreated_ranges": sink_a.untreated.as_list(minimum_frames=config.hop),
            "untreated_seconds": round(sink_a.untreated.total_frames / audio_a.sample_rate, 3),
            "peak_common": round(sink_a.peak_common, 6),
            "peak_residual": round(sink_a.peak_residual, 6),
            "reconstruction_max_abs_error": float(f"{sink_a.reconstruction_error:.3e}"),
        },
        "b": {
            "untreated_ranges": untreated_b.as_list(minimum_frames=config.hop),
            "untreated_seconds": round(untreated_b.total_frames / audio_b.sample_rate, 3),
            "peak_common": round(stats_b["peak_common"], 6),
            "peak_residual": round(stats_b["peak_residual"], 6),
            "reconstruction_max_abs_error": float(
                f"{stats_b['reconstruction_max_abs_error']:.3e}"),
        },
    }
    if max(sink_a.peak_common, sink_a.peak_residual,
           stats_b["peak_residual"]) > 1.0 and options.wav_format == "float32":
        warnings.append(
            "Des échantillons dépassent ±1.0 : les sorties sont en WAV flottant, donc "
            "sans écrêtage, mais une conversion en PCM entier écrêterait."
        )
    return summary, timeline, measurements


def _timeline_entry(start: int, sample_rate: int, delay: float, offset_map,
                    index: int, diagnostics) -> dict:
    entry = {
        "t_s": round(start / sample_rate, 3),
        "delay_s": round(delay / sample_rate, 6),
        "alignment_confidence": round(float(offset_map.confidences[index]), 4),
        "comparable": bool(offset_map.comparable[index]),
    }
    if diagnostics is not None:
        entry["coherence"] = round(diagnostics.mean_coherence, 4)
        entry["common_share"] = round(diagnostics.common_energy_ratio, 4)
    return entry


def _decimate_timeline(timeline: list[dict]) -> tuple[list[dict], int]:
    if len(timeline) <= TIMELINE_LIMIT:
        return timeline, 1
    stride = int(np.ceil(len(timeline) / TIMELINE_LIMIT))
    return timeline[::stride], stride


def _build_metadata(options: ExtractOptions, audio_a, audio_b, config: StftConfig,
                    plan: BlockPlan, params: SeparationParams, offset_map, transfer,
                    summary: SeparationSummary, timeline: list[dict], measurements: dict,
                    warnings: list[str], watch: Stopwatch, global_delay) -> dict:
    sample_rate = audio_a.sample_rate
    kept, stride = _decimate_timeline(timeline)
    payload = base_payload("extract", describe_environment())
    payload.update({
        "inputs": {"a": stream_descriptor(audio_a), "b": stream_descriptor(audio_b)},
        "outputs": {
            "common_wav": {
                "name": COMMON_NAME,
                "timebase": "A",
                "description": "bande commune estimée, rendue dans le repère temporel de A",
            },
            "a_only_wav": {
                "name": A_ONLY_NAME, "timebase": "A",
                "description": "résidu A = A − commun (exact par construction)",
            },
            "b_only_wav": {
                "name": B_ONLY_NAME, "timebase": "B",
                "description": "résidu B = B − H(f)·commun recalé dans le repère de B",
            },
            "sample_format": options.wav_format,
            "sample_rate": sample_rate,
            "channels": audio_a.channels,
            "channel_layout": audio_a.stream.effective_layout,
            "measurements": measurements,
        },
        "common_timebase": {
            "reference": "A",
            "sample_rate": sample_rate,
            "channels": audio_a.channels,
            "channel_layout": audio_a.stream.effective_layout,
            "frames": audio_a.frames,
            "origin_s": 0.0,
            "note": "La trame 0 de common.wav correspond à la trame 0 de l'entrée A.",
        },
        "alignment": {
            "sign_convention": "delay > 0 : B est en retard sur A ; trame m de A = trame m+delay de B",
            "global_delay_samples": round(float(global_delay.delay), 4),
            "global_delay_s": round(float(global_delay.delay) / sample_rate, 6),
            "global_confidence": round(float(global_delay.confidence), 4),
            "method": global_delay.method,
            "confidence_definition": "corrélation normalisée sur la piste d'analyse décimée",
            "local_block_s": round(plan.advance / sample_rate, 4),
            "local_search_s": options.local_search_s,
            "confidence_threshold": offset_map.threshold,
            "drift_ppm": round(offset_map.drift_ppm, 3),
            "comparable_block_fraction": round(offset_map.comparable_ratio, 4),
            "comparable_segments": offset_map.segments(sample_rate, comparable=True),
            "non_comparable_segments": offset_map.segments(sample_rate, comparable=False),
        },
        "transfer": transfer.as_dict(),
        "separation": {
            **params.as_dict(),
            "stft": config.as_dict(),
            "blocks": plan.as_dict(sample_rate),
            "diagnostics": summary.as_dict(),
        },
        "timeline": {
            "stride": stride,
            "note": ("une entrée par bloc traité ; décimée si le film est long "
                     "(stride > 1)"),
            "entries": kept,
        },
        "performance": watch.as_dict(),
        "warnings": warnings,
        "limitations": limitations_notice(),
    })
    return payload
