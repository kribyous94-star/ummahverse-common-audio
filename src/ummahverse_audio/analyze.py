"""`analyze` : contrôle de compatibilité, alignement et diagnostic, sans rien produire.

C'est l'étape « Analyser / synchroniser » de l'interface, également disponible en CLI.
Elle répond à une question : ces deux pistes sont-elles exploitables, et avec quels
avertissements ? Aucun WAV n'est écrit.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from .align import DelayEstimate, build_offset_map, estimate_global_delay
from .audio_io import Workspace, check_layout_compatibility, choose_common_rate, load_input
from .blocks import BlockPlan
from .extract import ExtractOptions
from .ffmpeg_tools import describe_environment, probe_audio_streams, select_stream
from .metadata import base_payload, limitations_notice, stream_descriptor
from .perf import Stopwatch
from .progress import Progress, SubProgress
from .transfer import estimate_transfer
from .verdict import CommonBedVerdict, judge


@dataclass
class AnalysisReport:
    """Résumé lisible, destiné à être affiché avant de lancer l'extraction."""

    payload: dict = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    verdict: CommonBedVerdict | None = None

    def summary_lines(self) -> list[str]:
        alignment = self.payload["alignment"]
        transfer = self.payload["transfer"]
        inputs = self.payload["inputs"]
        return [
            f"A : {inputs['a']['name']} — flux #{inputs['a']['audio_stream_index']}, "
            f"{inputs['a']['channels']} canaux ({inputs['a']['channel_layout']}), "
            f"{inputs['a']['duration_s']:.2f} s",
            f"B : {inputs['b']['name']} — flux #{inputs['b']['audio_stream_index']}, "
            f"{inputs['b']['channels']} canaux ({inputs['b']['channel_layout']}), "
            f"{inputs['b']['duration_s']:.2f} s",
            f"Fréquence de travail : {self.payload['working_sample_rate']} Hz",
            f"Décalage global : {alignment['global_delay_s'] * 1000:.2f} ms "
            f"(confiance {alignment['global_confidence']:.3f})",
            f"Dérive estimée : {alignment['drift_ppm']:.1f} ppm",
            f"Blocs comparables : {alignment['comparable_block_fraction'] * 100:.1f} % "
            f"({len(alignment['non_comparable_segments'])} segment(s) écarté(s))",
            f"Gain du commun de B par rapport à A : "
            + ", ".join(f"{g:.4f}" for g in transfer["broadband_gain_per_channel"]),
            f"Cases temps-fréquence fiables : "
            f"{transfer['reliable_cell_fraction'] * 100:.2f} %",
            f"Cohérence pondérée par l'énergie : "
            f"{transfer['energy_weighted_coherence'] * 100:.2f} % "
            f"(corrigée du biais : "
            f"{transfer['energy_weighted_coherence_debiased'] * 100:.2f} %)",
        ]


def run_analysis(options: ExtractOptions, progress: Progress | None = None) -> AnalysisReport:
    """Exécute uniquement les étapes de diagnostic de `extract`."""
    progress = progress or Progress()
    watch = Stopwatch()
    warnings: list[str] = []

    with Workspace(options.workspace_dir) as workspace:
        progress.stage("Inspection des flux")
        streams_a = probe_audio_streams(Path(options.input_a))
        streams_b = probe_audio_streams(Path(options.input_b))
        chosen_a = select_stream(Path(options.input_a), streams_a, options.stream_a)
        chosen_b = select_stream(Path(options.input_b), streams_b, options.stream_b)
        warnings += check_layout_compatibility(chosen_a, chosen_b, options.allow_unknown_layout)
        rate = choose_common_rate([chosen_a, chosen_b], options.sample_rate)

        progress.stage("Décodage")
        audio_a = load_input(options.input_a, workspace, "a", chosen_a.audio_index, rate)
        progress.step(0.2, "entrée A décodée")
        audio_b = load_input(options.input_b, workspace, "b", chosen_b.audio_index, rate)
        progress.step(0.35, "entrée B décodée")

        config = options.stft_config(rate)
        plan = BlockPlan.for_config(config, options.block_seconds)

        progress.stage("Alignement")
        track_a, analysis_rate = audio_a.analysis_track()
        track_b, _ = audio_b.analysis_track()
        global_delay = estimate_global_delay(track_a, track_b, analysis_rate,
                                             max_delay_s=options.max_offset_s)
        scaled = DelayEstimate(
            delay=global_delay.delay * rate / analysis_rate,
            integer_delay=int(round(global_delay.integer_delay * rate / analysis_rate)),
            confidence=global_delay.confidence, peak_ratio=global_delay.peak_ratio,
            method=global_delay.method)
        if scaled.confidence < 0.05:
            warnings.append(
                f"Corrélation globale très faible ({scaled.confidence:.3f}) : les deux "
                "entrées ne semblent pas issues du même montage."
            )
        offset_map = build_offset_map(
            audio_a, audio_b, plan.advance, scaled,
            confidence_threshold=options.confidence_threshold,
            local_search_s=options.local_search_s,
            tracks=(track_a, track_b, analysis_rate),
            progress=SubProgress(progress, 0.35, 0.8))
        warnings += offset_map.notes
        del track_a, track_b

        progress.stage("Diagnostic du gain et de l'égalisation")
        transfer = estimate_transfer(
            audio_a, audio_b, offset_map, config, plan, mode=options.transfer_mode,
            coherence_threshold=options.coherence_threshold,
            estimator=options.transfer_estimator,
            progress=SubProgress(progress, 0.8, 1.0))
        warnings += transfer.warnings

        verdict = judge(
            mean_coherence=transfer.mean_coherence_debiased,
            reliable_fraction=transfer.reliable_fraction,
            comparable_fraction=offset_map.comparable_ratio,
            alignment_method=scaled.method,
            alignment_confidence=scaled.confidence)

        payload = base_payload("analyze", describe_environment())
        payload.update({
            "inputs": {"a": stream_descriptor(audio_a), "b": stream_descriptor(audio_b)},
            "working_sample_rate": rate,
            "alignment": {
                "sign_convention": "delay > 0 : B est en retard sur A",
                "global_delay_samples": round(float(scaled.delay), 4),
                "global_delay_s": round(float(scaled.delay) / rate, 6),
                "global_confidence": round(float(scaled.confidence), 4),
                "method": scaled.method,
                "drift_ppm": round(offset_map.drift_ppm, 3),
                "comparable_block_fraction": round(offset_map.comparable_ratio, 4),
                "non_comparable_segments": offset_map.segments(rate, comparable=False),
            },
            "transfer": transfer.as_dict(),
            "stft": config.as_dict(),
            "blocks": plan.as_dict(rate),
            "performance": watch.as_dict(),
            "common_bed_verdict": verdict.as_dict(),
            "warnings": warnings,
            "limitations": limitations_notice(),
        })
    progress.step(1.0, "analyse terminée")
    return AnalysisReport(payload=payload, warnings=warnings, verdict=verdict)
