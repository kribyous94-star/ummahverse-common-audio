"""`scout` : où, dans un film, les deux versions partagent-elles une bande commune ?

Deux versions linguistiques d'un même film ne partagent pas forcément le même master
musique/bruitages, et quand elles le partagent, ce n'est pas nécessairement partout :
mesuré sur un film réel, seul le générique était commun, le corps du film ayant été
remixé. Lancer une extraction de deux heures pour découvrir cela est coûteux.

Cette commande balaie tout le signal par fenêtres, mesure dans chacune le décalage
local et la cohérence, et rend la carte des zones exploitables. Elle ne produit aucun
fichier audio et travaille à fréquence d'analyse réduite : quelques minutes pour un
film entier, contre une extraction complète.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from scipy.signal import coherence as welch_coherence

from .align import envelope_delay, gcc_phat
from .audio_io import Workspace, check_layout_compatibility, choose_common_rate, load_input
from .errors import UmmahAudioError
from .ffmpeg_tools import describe_environment, probe_audio_streams, select_stream
from .metadata import base_payload, stream_descriptor
from .perf import Stopwatch
from .progress import Progress
from .verdict import ABSENT, PARTIAL, WEAK, judge

#: Durée par défaut d'une fenêtre de repérage.
DEFAULT_WINDOW_S = 20.0
#: Amplitude de recherche du décalage local, en secondes.
LOCAL_SEARCH_S = 1.0


@dataclass
class ScoutWindow:
    start_s: float
    end_s: float
    delay_s: float
    coherence: float
    correlation: float

    @property
    def usable(self) -> bool:
        return self.coherence >= WEAK


@dataclass
class ScoutReport:
    windows: list[ScoutWindow] = field(default_factory=list)
    payload: dict = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    @property
    def usable_seconds(self) -> float:
        return sum(w.end_s - w.start_s for w in self.windows if w.usable)

    def ranges(self, minimum: float = WEAK) -> list[tuple[float, float, float]]:
        """Regroupe les fenêtres exploitables consécutives en plages continues."""
        out: list[tuple[float, float, float]] = []
        current: list[ScoutWindow] = []
        for window in self.windows:
            if window.coherence >= minimum:
                current.append(window)
            elif current:
                out.append((current[0].start_s, current[-1].end_s,
                            float(np.mean([w.coherence for w in current]))))
                current = []
        if current:
            out.append((current[0].start_s, current[-1].end_s,
                        float(np.mean([w.coherence for w in current]))))
        return out

    def summary_lines(self) -> list[str]:
        if not self.windows:
            return ["Aucune fenêtre analysable."]
        values = np.array([w.coherence for w in self.windows])
        total = self.windows[-1].end_s
        lines = [
            f"{len(self.windows)} fenêtres analysées sur {total / 60:.0f} min",
            f"Cohérence : médiane {np.median(values) * 100:.1f} %, "
            f"90e centile {np.percentile(values, 90) * 100:.1f} %, "
            f"maximum {values.max() * 100:.1f} %",
            f"Durée exploitable (cohérence ≥ {WEAK * 100:.0f} %) : "
            f"{self.usable_seconds / 60:.1f} min sur {total / 60:.0f} "
            f"({100 * self.usable_seconds / max(total, 1):.1f} %)",
        ]
        best = self.ranges(minimum=PARTIAL)
        if best:
            lines.append("Meilleures plages (cohérence ≥ "
                         f"{PARTIAL * 100:.0f} %) :")
            for start, end, value in best[:12]:
                lines.append(f"  {_clock(start)} → {_clock(end)}  "
                             f"cohérence {value * 100:.0f} %")
        elif self.ranges():
            lines.append("Plages passables uniquement :")
            for start, end, value in self.ranges()[:12]:
                lines.append(f"  {_clock(start)} → {_clock(end)}  "
                             f"cohérence {value * 100:.0f} %")
        else:
            lines.append("Aucune plage exploitable trouvée.")
        return lines


def _clock(seconds: float) -> str:
    total = int(seconds)
    return f"{total // 3600:d}:{(total % 3600) // 60:02d}:{total % 60:02d}"


def run_scout(input_a: Path, input_b: Path, stream_a: int | None = None,
              stream_b: int | None = None, window_s: float = DEFAULT_WINDOW_S,
              sample_rate: int | None = None, allow_unknown_layout: bool = False,
              workspace_dir: Path | None = None,
              progress: Progress | None = None) -> ScoutReport:
    """Balaie les deux entrées et rend la carte des zones à bande commune."""
    progress = progress or Progress()
    watch = Stopwatch()
    warnings: list[str] = []

    with Workspace(workspace_dir) as workspace:
        progress.stage("Décodage des entrées")
        streams_a = probe_audio_streams(Path(input_a))
        streams_b = probe_audio_streams(Path(input_b))
        chosen_a = select_stream(Path(input_a), streams_a, stream_a)
        chosen_b = select_stream(Path(input_b), streams_b, stream_b)
        warnings += check_layout_compatibility(chosen_a, chosen_b, allow_unknown_layout)
        rate = choose_common_rate([chosen_a, chosen_b], sample_rate)
        audio_a = load_input(input_a, workspace, "a", chosen_a.audio_index, rate)
        audio_b = load_input(input_b, workspace, "b", chosen_b.audio_index, rate)

        progress.stage("Repérage des zones à bande commune")
        track_a, analysis_rate = audio_a.analysis_track()
        track_b, _ = audio_b.analysis_track()
        if track_a.size < 4 * analysis_rate:
            raise UmmahAudioError(
                "Entrées trop courtes pour un repérage ; utilisez « analyze ».")

        window = max(int(window_s * analysis_rate), analysis_rate)
        search = max(int(LOCAL_SEARCH_S * analysis_rate), 8)
        usable = min(track_a.size, track_b.size)
        windows: list[ScoutWindow] = []
        for start in range(0, usable - window, window):
            progress.step(start / max(usable, 1), "repérage")
            piece_a = track_a[start:start + window]
            low = max(start - search, 0)
            high = min(start + window + search, track_b.size)
            estimate = gcc_phat(piece_a, track_b[low:high], max_shift=search,
                                refine=False)
            delay = estimate.integer_delay + (low - start)
            if delay >= 0:
                x, y = piece_a[:window - delay], track_b[start + delay:start + window]
            else:
                x, y = piece_a[-delay:], track_b[start:start + window + delay]
            length = min(x.size, y.size)
            if length < analysis_rate:
                continue
            x, y = x[:length], y[:length]
            freqs, spectrum = welch_coherence(x, y, fs=analysis_rate,
                                              nperseg=min(2048, length))
            band = (freqs > 80) & (freqs < analysis_rate * 0.45)
            value = float(np.mean(spectrum[band])) if band.any() else 0.0
            centred_x, centred_y = x - x.mean(), y - y.mean()
            norm = float(np.linalg.norm(centred_x) * np.linalg.norm(centred_y))
            correlation = float(np.dot(centred_x, centred_y) / norm) if norm else 0.0
            windows.append(ScoutWindow(
                start_s=start / analysis_rate,
                end_s=(start + window) / analysis_rate,
                delay_s=delay / analysis_rate,
                coherence=value, correlation=correlation))
            audio_a.release_until(int(start / analysis_rate * rate))
            audio_b.release_until(int(start / analysis_rate * rate))

        global_envelope = envelope_delay(track_a, track_b, analysis_rate)
        values = np.array([w.coherence for w in windows]) if windows else np.zeros(1)
        verdict = judge(mean_coherence=float(np.percentile(values, 90)),
                        reliable_fraction=float(np.mean(values >= PARTIAL)),
                        comparable_fraction=float(np.mean(values >= WEAK)),
                        alignment_method=global_envelope.method,
                        alignment_confidence=global_envelope.confidence)

        payload = base_payload("scout", describe_environment())
        payload.update({
            "inputs": {"a": stream_descriptor(audio_a), "b": stream_descriptor(audio_b)},
            "window_s": window_s,
            "analysis_rate": analysis_rate,
            "coherence_thresholds": {"absente": ABSENT, "faible": WEAK,
                                     "partielle": PARTIAL},
            "envelope_alignment": {
                "delay_s": round(global_envelope.delay / analysis_rate, 4),
                "correlation": round(global_envelope.confidence, 4),
                "note": ("corrélation des enveloppes d'énergie : élevée, elle confirme "
                         "que les deux versions couvrent les mêmes scènes, même quand "
                         "leurs formes d'onde diffèrent"),
            },
            "usable_seconds": round(sum(w.end_s - w.start_s
                                        for w in windows if w.usable), 1),
            "usable_ranges": [
                {"start": _clock(s), "end": _clock(e), "coherence": round(c, 4)}
                for s, e, c in _ranges(windows, WEAK)],
            "windows": [
                {"start_s": round(w.start_s, 1), "delay_s": round(w.delay_s, 4),
                 "coherence": round(w.coherence, 4),
                 "correlation": round(w.correlation, 4)} for w in windows],
            "common_bed_verdict": verdict.as_dict(),
            "performance": watch.as_dict(),
            "warnings": warnings,
        })
    progress.step(1.0, "repérage terminé")
    return ScoutReport(windows=windows, payload=payload, warnings=warnings)


def _ranges(windows: list[ScoutWindow], minimum: float):
    report = ScoutReport(windows=windows)
    return report.ranges(minimum)
