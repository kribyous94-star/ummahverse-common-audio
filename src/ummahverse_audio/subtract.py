"""`subtract` : réutilise `common.wav` pour retirer la bande commune d'une autre langue.

Cette commande ne relit ni A ni B et ne recalcule pas `extract` : elle n'a besoin que
de `common.wav`, `common.json` et de la nouvelle piste.

L'adaptation est volontairement **prudente** : une réponse complexe `G(f)` globale
estimée sur les cases temps-fréquence fiables (cohérence locale élevée entre le commun
et la nouvelle piste), puis un gain scalaire par bloc, borné, estimé sur ces mêmes
cases. Aucun masque adaptatif par case n'est appliqué : ce serait le moyen le plus
simple d'effacer la voix de la nouvelle langue en croyant retirer la musique.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from .align import DelayEstimate, build_offset_map, estimate_global_delay
from .audio_io import Workspace, load_input
from .blocks import BlockPlan, align_spectrum, delay_spectrum, read_extended, split_delay
from .errors import IncompatibleLayoutError, UmmahAudioError
from .ffmpeg_tools import describe_environment
from .metadata import (base_payload, common_reference, limitations_notice,
                       load_common_metadata, stream_descriptor, write_json)
from .perf import Stopwatch
from .progress import Progress, SubProgress
from .sinks import AccumulatorSink
from .stft import (StftConfig, local_spectra, magnitude_squared_coherence)
from .transfer import estimate_transfer

#: Bornes du gain scalaire par bloc, autour de la réponse globale.
DEFAULT_BLOCK_GAIN_LIMIT = 1.3
#: Nombre minimal de cases fiables pour accepter un gain de bloc.
MIN_RELIABLE_CELLS = 200


@dataclass
class SubtractOptions:
    common: Path
    input_c: Path
    output: Path
    metadata: Path | None = None
    report: Path | None = None
    stream: int | None = None
    adapt: str = "block-gain"          # "global" ou "block-gain"
    block_gain_limit: float = DEFAULT_BLOCK_GAIN_LIMIT
    coherence_threshold: float = 0.9
    transfer_mode: str = "band"
    transfer_estimator: str = "power"
    win_length: int = 8192
    hop: int | None = None
    smooth_frames: int = 3
    smooth_bins: int = 2
    block_seconds: float = 6.0
    max_offset_s: float = 30.0
    local_search_s: float = 0.35
    confidence_threshold: float = 0.12
    wav_format: str = "float32"
    export_adapted_common: bool = False
    workspace_dir: Path | None = None

    def stft_config(self, sample_rate: int) -> StftConfig:
        return StftConfig(sample_rate=sample_rate, win_length=self.win_length,
                          hop=self.hop or self.win_length // 4,
                          smooth_frames=self.smooth_frames, smooth_bins=self.smooth_bins)


@dataclass
class SubtractResult:
    output_path: Path
    report_path: Path
    adapted_common_path: Path | None = None
    report: dict = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


def default_report_path(output: Path) -> Path:
    return Path(output).with_suffix(".json")


def run_subtract(options: SubtractOptions, progress: Progress | None = None) -> SubtractResult:
    progress = progress or Progress()
    watch = Stopwatch()
    warnings: list[str] = []
    output = Path(options.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    report_path = Path(options.report) if options.report else default_report_path(output)

    reference: dict = {}
    source_metadata: dict | None = None
    metadata_path = Path(options.metadata) if options.metadata else None
    if metadata_path is not None:
        source_metadata = load_common_metadata(metadata_path)
        reference = common_reference(source_metadata)
    else:
        warnings.append(
            "Aucun common.json fourni : la base temporelle et les paramètres d'origine du "
            "commun ne peuvent pas être vérifiés."
        )

    with Workspace(options.workspace_dir) as workspace:
        progress.stage("Décodage du commun et de la nouvelle piste")
        rate = int(reference.get("sample_rate") or 0) or None
        audio_common = load_input(options.common, workspace, "common", None, rate)
        rate = audio_common.sample_rate
        audio_c = load_input(options.input_c, workspace, "c", options.stream, rate)
        progress.step(0.15, "entrées décodées")
        watch.mark("decode")

        _check_common_consistency(audio_common, audio_c, reference, warnings)

        config = options.stft_config(rate)
        plan = BlockPlan.for_config(config, options.block_seconds)

        progress.stage("Alignement du commun sur la nouvelle piste")
        track_common, analysis_rate = audio_common.analysis_track()
        track_c, _ = audio_c.analysis_track()
        global_delay = estimate_global_delay(track_common, track_c, analysis_rate,
                                             max_delay_s=options.max_offset_s)
        scaled = DelayEstimate(
            delay=global_delay.delay * rate / analysis_rate,
            integer_delay=int(round(global_delay.integer_delay * rate / analysis_rate)),
            confidence=global_delay.confidence, peak_ratio=global_delay.peak_ratio)
        progress.log(f"Décalage estimé : {scaled.delay / rate * 1000:.2f} ms "
                     f"(confiance {scaled.confidence:.3f})")
        offset_map = build_offset_map(
            audio_common, audio_c, plan.advance, scaled,
            confidence_threshold=options.confidence_threshold,
            local_search_s=options.local_search_s,
            tracks=(track_common, track_c, analysis_rate),
            progress=SubProgress(progress, 0.15, 0.30))
        warnings += offset_map.notes
        del track_common, track_c
        watch.mark("align")

        progress.stage("Estimation de la transformation du commun")
        # `estimate_transfer(a=commun, b=C)` donne directement G tel que C ≈ G(f)·commun,
        # régressé sur le commun (dont le bruit propre est faible), et non l'inverse.
        transfer = estimate_transfer(
            audio_common, audio_c, offset_map, config, plan, mode=options.transfer_mode,
            coherence_threshold=options.coherence_threshold,
            estimator=options.transfer_estimator,
            progress=SubProgress(progress, 0.30, 0.40))
        warnings += transfer.warnings
        progress.log("Gain appliqué au commun par canal : "
                     + ", ".join(f"{g:.4f}" for g in transfer.broadband_gain))
        watch.mark("transfer")

        progress.stage("Soustraction bloc par bloc")
        timeline, measurements = _subtract_blocks(
            audio_common, audio_c, offset_map, config, plan, transfer, workspace,
            output, options, SubProgress(progress, 0.40, 0.98), warnings)
        watch.mark("subtract")
        progress.step(1.0, "terminé")

        report = _build_report(options, audio_common, audio_c, config, plan, offset_map,
                               transfer, timeline, measurements, warnings, watch, scaled,
                               source_metadata, metadata_path)
        write_json(report_path, report)

    adapted = output.with_name(output.stem + "_adapted_common.wav")
    return SubtractResult(
        output_path=output, report_path=report_path,
        adapted_common_path=adapted if options.export_adapted_common else None,
        report=report, warnings=warnings)


def _check_common_consistency(audio_common, audio_c, reference: dict,
                              warnings: list[str]) -> None:
    if audio_common.channels != audio_c.channels:
        raise IncompatibleLayoutError(
            f"Le commun a {audio_common.channels} canaux et la nouvelle piste "
            f"{audio_c.channels}.",
            "Aucun downmix implicite : fournissez une piste de même agencement que "
            "celui enregistré dans common.json.",
        )
    expected_channels = reference.get("channels")
    if expected_channels and int(expected_channels) != audio_common.channels:
        warnings.append(
            f"common.wav a {audio_common.channels} canaux alors que common.json en "
            f"annonce {expected_channels} : fichiers probablement dépareillés."
        )
    expected_layout = reference.get("channel_layout")
    layout_c = audio_c.stream.effective_layout
    if expected_layout and layout_c not in ("inconnu", expected_layout):
        warnings.append(
            f"Agencement de la nouvelle piste ({layout_c}) différent de celui du commun "
            f"({expected_layout}) : canaux appariés dans l'ordre du fichier."
        )
    if audio_c.frames == 0:
        raise UmmahAudioError("La nouvelle piste ne contient aucun échantillon.")


def _subtract_blocks(audio_common, audio_c, offset_map, config: StftConfig, plan: BlockPlan,
                     transfer, workspace, output: Path, options: SubtractOptions,
                     progress: Progress, warnings: list[str]) -> tuple[list[dict], dict]:
    sft = config.build()
    extended = plan.block_frames + 2 * plan.margin
    starts = offset_map.starts
    timeline: list[dict] = []
    sink = AccumulatorSink(workspace, "adapted_common", audio_c.frames, audio_c.channels,
                           audio_c.sample_rate)
    gains: list[float] = []
    reliable_fractions: list[float] = []

    for index, start in enumerate(starts):
        progress.check_cancelled()
        progress.step(index / max(starts.size, 1), f"bloc {index + 1}/{starts.size}")
        start = int(start)
        delay = float(offset_map.delays[index])
        integer, fractional = split_delay(delay)
        weights = plan.weights(start, audio_common.frames)
        comparable = bool(offset_map.comparable[index])

        if not comparable:
            # Politique explicite : rien n'est soustrait, le résidu reste la piste d'entrée.
            timeline.append({"t_s": round(start / audio_common.sample_rate, 3),
                             "delay_s": round(delay / audio_common.sample_rate, 6),
                             "alignment_confidence": round(
                                 float(offset_map.confidences[index]), 4),
                             "comparable": False, "applied_gain": 0.0,
                             "reliable_cell_fraction": 0.0})
            sink.add(start + integer,
                     np.zeros((plan.block_frames, audio_c.channels), dtype=np.float64),
                     weights)
            continue

        block_common = read_extended(audio_common, start, plan)
        block_c = read_extended(audio_c, start + integer, plan)
        adapted = np.zeros((extended, audio_c.channels), dtype=np.float64)
        block_gains: list[float] = []
        block_reliable: list[float] = []
        block_coherence: list[float] = []
        for channel in range(audio_c.channels):
            spec_common = sft.stft(block_common[:, channel].astype(np.float64))
            spec_c = align_spectrum(sft.stft(block_c[:, channel].astype(np.float64)),
                                    fractional, config)
            projected = spec_common * transfer.response[channel][:, None]
            saa, sbb, sab = local_spectra(projected, spec_c, config)
            gamma = magnitude_squared_coherence(saa, sbb, sab)
            reliable = gamma >= options.coherence_threshold
            block_reliable.append(float(reliable.mean()))
            weight_total = float(np.sum(saa))
            block_coherence.append(float(np.sum(gamma * saa) / weight_total)
                                   if weight_total > 0 else 0.0)

            gain = 1.0
            if options.adapt == "block-gain" and int(reliable.sum()) >= MIN_RELIABLE_CELLS:
                numerator = float(np.sum(np.where(
                    reliable, np.real(np.conj(projected) * spec_c), 0.0)))
                denominator = float(np.sum(np.where(reliable, np.abs(projected) ** 2, 0.0)))
                if denominator > 0:
                    limit = options.block_gain_limit
                    gain = float(np.clip(numerator / denominator, 1.0 / limit, limit))
            block_gains.append(gain)
            adapted[:, channel] = sft.istft(
                delay_spectrum(gain * projected, fractional, config), k0=0, k1=extended)

        useful = slice(plan.margin, plan.margin + plan.block_frames)
        sink.add(start + integer, adapted[useful], weights)
        mean_gain = float(np.mean(block_gains))
        gains.append(mean_gain)
        reliable_fractions.append(float(np.mean(block_reliable)))
        timeline.append({
            "t_s": round(start / audio_common.sample_rate, 3),
            "delay_s": round(delay / audio_common.sample_rate, 6),
            "alignment_confidence": round(float(offset_map.confidences[index]), 4),
            "comparable": True,
            "applied_gain": round(mean_gain, 5),
            "reliable_cell_fraction": round(float(np.mean(block_reliable)), 4),
            "coherence": round(float(np.mean(block_coherence)), 4),
        })

    untreated, stats = sink.write_residual(audio_c, output, options.wav_format, progress)
    if options.export_adapted_common:
        sink.write_common(output.with_name(output.stem + "_adapted_common.wav"),
                          audio_c.stream.effective_layout, options.wav_format)

    if untreated.total_frames:
        warnings.append(
            f"{untreated.total_frames / audio_c.sample_rate:.2f} s de la nouvelle piste "
            "n'ont reçu aucun commun adapté (hors zone comparable) : le résidu y est "
            "identique à l'entrée."
        )
    if gains and (min(gains) < 0.85 or max(gains) > 1.2):
        warnings.append(
            f"Gain d'adaptation très variable ({min(gains):.2f} à {max(gains):.2f}) : le "
            "commun ne correspond pas bien à cette nouvelle piste."
        )
    measurements = {
        "untreated_ranges": untreated.as_list(minimum_frames=config.hop),
        "untreated_seconds": round(untreated.total_frames / audio_c.sample_rate, 3),
        "peak_adapted_common": round(stats["peak_common"], 6),
        "peak_residual": round(stats["peak_residual"], 6),
        "reconstruction_max_abs_error": float(
            f"{stats['reconstruction_max_abs_error']:.3e}"),
        "applied_gain_median": round(float(np.median(gains)), 5) if gains else None,
        "applied_gain_min": round(min(gains), 5) if gains else None,
        "applied_gain_max": round(max(gains), 5) if gains else None,
        "mean_reliable_cell_fraction": (round(float(np.mean(reliable_fractions)), 4)
                                        if reliable_fractions else 0.0),
    }
    return timeline, measurements


def _build_report(options: SubtractOptions, audio_common, audio_c, config: StftConfig,
                  plan: BlockPlan, offset_map, transfer, timeline: list[dict],
                  measurements: dict, warnings: list[str], watch: Stopwatch,
                  global_delay, source_metadata: dict | None,
                  metadata_path: Path | None) -> dict:
    from .extract import _decimate_timeline

    rate = audio_common.sample_rate
    kept, stride = _decimate_timeline(timeline)
    payload = base_payload("subtract", describe_environment())
    payload.update({
        "inputs": {
            "common": stream_descriptor(audio_common),
            "new_language": stream_descriptor(audio_c),
            "metadata": str(metadata_path) if metadata_path else None,
            "source_params_version": (source_metadata or {}).get("params_version"),
            "source_inputs": {
                key: value.get("name")
                for key, value in ((source_metadata or {}).get("inputs") or {}).items()
            },
        },
        "outputs": {
            "residual_wav": {
                "name": Path(options.output).name,
                "timebase": "nouvelle piste (identique en durée et en base temporelle)",
                "description": "nouvelle piste − commun adapté",
            },
            "adapted_common_wav": (
                Path(options.output).stem + "_adapted_common.wav"
                if options.export_adapted_common else None),
            "sample_format": options.wav_format,
            "sample_rate": rate,
            "channels": audio_c.channels,
            "channel_layout": audio_c.stream.effective_layout,
            "frames": audio_c.frames,
            "measurements": measurements,
        },
        "alignment": {
            "sign_convention": ("delay > 0 : la nouvelle piste est en retard sur le commun ; "
                                "trame m du commun = trame m+delay de la nouvelle piste"),
            "global_delay_samples": round(float(global_delay.delay), 4),
            "global_delay_s": round(float(global_delay.delay) / rate, 6),
            "global_confidence": round(float(global_delay.confidence), 4),
            "drift_ppm": round(offset_map.drift_ppm, 3),
            "confidence_threshold": offset_map.threshold,
            "comparable_block_fraction": round(offset_map.comparable_ratio, 4),
            "comparable_segments": offset_map.segments(rate, comparable=True),
            "non_comparable_segments": offset_map.segments(rate, comparable=False),
        },
        "applied_transform": {
            "model": "nouvelle piste ≈ gain_bloc · G(f) · commun aligné",
            "adapt_mode": options.adapt,
            "block_gain_limit": options.block_gain_limit,
            "min_reliable_cells_per_block": MIN_RELIABLE_CELLS,
            **transfer.as_dict(),
        },
        "stft": config.as_dict(),
        "blocks": plan.as_dict(rate),
        "timeline": {"stride": stride,
                     "note": "gain et confiance locale par bloc",
                     "entries": kept},
        "performance": watch.as_dict(),
        "warnings": warnings,
        "limitations": limitations_notice() + [
            "Aucun masque adaptatif par case temps-fréquence n'est appliqué ici : ce serait "
            "le moyen le plus rapide d'effacer aussi la voix de la nouvelle langue.",
        ],
    })
    return payload
