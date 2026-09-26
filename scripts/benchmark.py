#!/usr/bin/env python3
"""Compare les baselines et la méthode retenue sur le jeu synthétique à sources connues.

    python scripts/benchmark.py --out-dir benchmarks
    python scripts/benchmark.py --out-dir benchmarks --perf-seconds 300

Produit `benchmarks/results.json` et `benchmarks/results.md`. Les mesures sont définies
dans `ummahverse_audio.metrics` ; aucun chiffre n'est arrondi ou filtré pour
avantager une méthode.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from ummahverse_audio.extract import ExtractOptions, run_extract  # noqa: E402
from ummahverse_audio.metrics import (EvaluationWindow, decompose, delayed,  # noqa: E402
                                      read_audio, si_sdr)
from ummahverse_audio.subtract import SubtractOptions, run_subtract  # noqa: E402

#: Variantes comparées. La première est la méthode retenue.
VARIANTS: list[tuple[str, dict]] = [
    ("wiener (retenue)", {"method": "wiener", "transfer_mode": "band"}),
    ("wiener sans correction de biais", {"method": "wiener", "transfer_mode": "band",
                                         "bias_correction": 0.0}),
    ("wiener, gain large bande seul", {"method": "wiener", "transfer_mode": "gain"}),
    ("wiener, aucune transformation", {"method": "wiener", "transfer_mode": "none"}),
    ("baseline moyenne complexe", {"method": "average", "transfer_mode": "band"}),
    ("baseline min-magnitude", {"method": "min-magnitude", "transfer_mode": "band"}),
    ("baseline masque de cohérence", {"method": "coherence-mask", "transfer_mode": "band"}),
]


def generate_fixtures(target: Path) -> dict:
    target.mkdir(parents=True, exist_ok=True)
    subprocess.run([sys.executable, str(ROOT / "scripts" / "generate_fixtures.py"), str(target)],
                   check=True, stdout=subprocess.DEVNULL)
    return json.loads((target / "fixture.json").read_text(encoding="utf-8"))


def build_references(fixtures: Path, info: dict) -> dict:
    """Sources vraies ramenées dans chaque base temporelle."""
    common, rate = read_audio(fixtures / "common_truth.wav")
    voice_a, _ = read_audio(fixtures / "a_voice_truth.wav")
    voice_b, _ = read_audio(fixtures / "b_voice_truth.wav")
    voice_third, _ = read_audio(fixtures / "third_voice_truth.wav")
    offsets = info["common_offset_samples"]
    gains = info["common_gain"]
    return {
        "sample_rate": rate,
        "a": {"commun": gains["a"] * delayed(common, offsets["a"]),
              "voix_A": voice_a, "voix_B": voice_b},
        "b": {"commun": gains["b"] * delayed(common, offsets["b"]),
              "voix_B": voice_b, "voix_A": voice_a},
        "third": {"commun": gains["third"] * delayed(common, offsets["third"]),
                  "voix_C": voice_third, "voix_A": voice_a},
        "common_truth": common,
    }


def evaluate(result_dir: Path, references: dict, window: EvaluationWindow) -> dict:
    common, _ = read_audio(result_dir / "common.wav")
    a_only, _ = read_audio(result_dir / "a_only.wav")
    b_only, _ = read_audio(result_dir / "b_only.wav")
    truth = references["common_truth"]
    span = window.slice_for(min(common.shape[0], truth.shape[0]))
    return {
        "common": {
            "si_sdr_db": round(si_sdr(common, truth, span), 2),
            **decompose(common, references["a"], span),
        },
        "a_only": {
            "si_sdr_db": round(si_sdr(a_only, references["a"]["voix_A"], span), 2),
            **decompose(a_only, references["a"], span),
        },
        "b_only": {
            "si_sdr_db": round(si_sdr(b_only, references["b"]["voix_B"], span), 2),
            **decompose(b_only, references["b"], span),
        },
    }


def evaluate_third(output: Path, references: dict, window: EvaluationWindow) -> dict:
    third_only, _ = read_audio(output)
    span = window.slice_for(third_only.shape[0])
    return {
        "si_sdr_db": round(si_sdr(third_only, references["third"]["voix_C"], span), 2),
        **decompose(third_only, references["third"], span),
    }


def run_variant(name: str, overrides: dict, fixtures: Path, out_root: Path,
                references: dict, window: EvaluationWindow) -> dict:
    result_dir = out_root / name.replace(" ", "_").replace(",", "").replace("(", "")\
        .replace(")", "")
    started = time.monotonic()
    extract_result = run_extract(ExtractOptions(
        input_a=fixtures / "a.wav", input_b=fixtures / "b.wav", out_dir=result_dir,
        **overrides))
    extract_seconds = time.monotonic() - started

    started = time.monotonic()
    subtract_result = run_subtract(SubtractOptions(
        common=result_dir / "common.wav", input_c=fixtures / "third.wav",
        output=result_dir / "third_only.wav", metadata=result_dir / "common.json"))
    subtract_seconds = time.monotonic() - started

    payload = {
        "variant": name,
        "options": overrides,
        "extract_seconds": round(extract_seconds, 2),
        "subtract_seconds": round(subtract_seconds, 2),
        "extract_performance": extract_result.metadata["performance"],
        "subtract_performance": subtract_result.report["performance"],
        "transfer_gain": extract_result.metadata["transfer"]["broadband_gain_per_channel"],
        "reconstruction": {
            "a": extract_result.metadata["outputs"]["measurements"]["a"][
                "reconstruction_max_abs_error"],
            "b": extract_result.metadata["outputs"]["measurements"]["b"][
                "reconstruction_max_abs_error"],
            "third": subtract_result.report["outputs"]["measurements"][
                "reconstruction_max_abs_error"],
        },
        **evaluate(result_dir, references, window),
        "third_only": evaluate_third(result_dir / "third_only.wav", references, window),
    }
    return payload


def perf_fixture(fixtures: Path, seconds: float, destination: Path) -> tuple[Path, Path]:
    """Duplique le jeu court pour mesurer le coût sur un signal long.

    La qualité n'est pas évaluée sur ce signal (la répétition crée des raccords
    artificiels) : seuls le temps et la mémoire le sont.
    """
    from scipy.io import wavfile

    destination.mkdir(parents=True, exist_ok=True)
    outputs = []
    for name in ("a", "b"):
        data, rate = read_audio(fixtures / f"{name}.wav")
        repeats = int(np.ceil(seconds * rate / data.shape[0]))
        tiled = np.tile(data, (repeats, 1))[: int(seconds * rate)]
        path = destination / f"{name}_long.wav"
        wavfile.write(path, rate, tiled.astype(np.float32))
        outputs.append(path)
    return outputs[0], outputs[1]


def measure_performance(long_a: Path, long_b: Path, out_dir: Path) -> dict:
    started = time.monotonic()
    result = run_extract(ExtractOptions(input_a=long_a, input_b=long_b, out_dir=out_dir))
    elapsed = time.monotonic() - started
    data, rate = read_audio(long_a)
    duration = data.shape[0] / rate
    return {
        "audio_seconds": round(duration, 1),
        "extract_seconds": round(elapsed, 2),
        "realtime_factor": round(duration / elapsed, 2),
        "performance": result.metadata["performance"],
    }


def _fmt(value) -> str:
    if value is None:
        return "—"
    if isinstance(value, float):
        return f"{value:.2f}"
    return str(value)


def to_markdown(results: list[dict], performance: dict | None, window: EvaluationWindow,
                info: dict) -> str:
    lines = [
        "# Résultats mesurés sur le jeu synthétique",
        "",
        "Jeu généré par `scripts/generate_fixtures.py` : 12 s, stéréo, 48 kHz, voix et",
        "musique/bruitages **simultanés** sur la quasi-totalité du signal.",
        f"Décalages vrais : {info['common_offset_samples']} échantillons ; ",
        f"gains vrais du commun : {info['common_gain']}.",
        "",
        f"Fenêtre d'évaluation : bords exclus ({window.skip_start_s} s au début, "
        f"{window.skip_end_s} s à la fin).",
        "",
        "Lecture des colonnes : `commun` est le SI-SDR du commun estimé contre la vérité ;",
        "`voix dans commun` est le coefficient de moindres carrés de la voix A dans le",
        "commun (plus bas = moins de fuite de voix) ; `musique dans a_only` est le",
        "coefficient du commun vrai restant dans le résidu (plus bas = mieux annulé) ;",
        "`voix dans a_only` doit rester proche de 0 dB (voix préservée).",
        "",
        "| Variante | commun SI-SDR (dB) | voix A dans commun (dB) | musique dans a_only (dB) "
        "| voix A dans a_only (dB) | a_only SI-SDR (dB) | b_only SI-SDR (dB) "
        "| third_only SI-SDR (dB) | extract (s) |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for entry in results:
        lines.append("| {variant} | {common_sdr} | {voice_in_common} | {music_in_a} | "
                     "{voice_in_a} | {a_sdr} | {b_sdr} | {third_sdr} | {seconds} |".format(
                         variant=entry["variant"],
                         common_sdr=_fmt(entry["common"]["si_sdr_db"]),
                         voice_in_common=_fmt(entry["common"]["voix_A_db"]),
                         music_in_a=_fmt(entry["a_only"]["commun_db"]),
                         voice_in_a=_fmt(entry["a_only"]["voix_A_db"]),
                         a_sdr=_fmt(entry["a_only"]["si_sdr_db"]),
                         b_sdr=_fmt(entry["b_only"]["si_sdr_db"]),
                         third_sdr=_fmt(entry["third_only"]["si_sdr_db"]),
                         seconds=_fmt(entry["extract_seconds"])))

    lines += ["", "## Gain estimé entre versions", "",
              "| Variante | gain par canal | erreur de reconstruction A / B / troisième |",
              "|---|---|---|"]
    for entry in results:
        lines.append("| {} | {} | {:.1e} / {:.1e} / {:.1e} |".format(
            entry["variant"],
            ", ".join(f"{g:.4f}" for g in entry["transfer_gain"]),
            entry["reconstruction"]["a"], entry["reconstruction"]["b"],
            entry["reconstruction"]["third"]))

    if performance:
        lines += ["", "## Coût de traitement", "",
                  "| Durée audio (s) | extract (s) | facteur temps réel | pic RSS (Mio) |",
                  "|---|---|---|---|",
                  "| {} | {} | {} | {} |".format(
                      performance["audio_seconds"], performance["extract_seconds"],
                      performance["realtime_factor"],
                      performance["performance"].get("peak_rss_mb", "—"))]
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", type=Path, default=ROOT / "benchmarks")
    parser.add_argument("--fixtures", type=Path, default=None,
                        help="jeu existant (défaut : généré dans --out-dir/fixtures)")
    parser.add_argument("--perf-seconds", type=float, default=0.0,
                        help="mesure aussi le coût sur un signal long de cette durée")
    parser.add_argument("--only", action="append", default=None,
                        help="limite aux variantes dont le nom contient cette chaîne")
    args = parser.parse_args()

    out_dir = args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    fixtures = args.fixtures or (out_dir / "fixtures")
    info = (json.loads((fixtures / "fixture.json").read_text(encoding="utf-8"))
            if (fixtures / "fixture.json").is_file() else generate_fixtures(fixtures))
    references = build_references(fixtures, info)
    window = EvaluationWindow(sample_rate=references["sample_rate"])

    variants = VARIANTS
    if args.only:
        variants = [v for v in VARIANTS if any(token in v[0] for token in args.only)]

    results = []
    for name, overrides in variants:
        print(f"→ {name}", flush=True)
        results.append(run_variant(name, overrides, fixtures, out_dir / "runs",
                                   references, window))

    performance = None
    if args.perf_seconds > 0:
        print(f"→ mesure de coût sur {args.perf_seconds:.0f} s", flush=True)
        long_a, long_b = perf_fixture(fixtures, args.perf_seconds, out_dir / "long")
        performance = measure_performance(long_a, long_b, out_dir / "runs" / "long")

    payload = {"fixture": info, "evaluation_window": window.as_dict(),
               "variants": results, "performance": performance}
    (out_dir / "results.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    (out_dir / "results.md").write_text(
        to_markdown(results, performance, window, info), encoding="utf-8")
    print(f"Écrit : {out_dir / 'results.json'} et {out_dir / 'results.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
