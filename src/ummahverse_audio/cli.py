"""Interface en ligne de commande. Même moteur que l'interface graphique.

    python -m ummahverse_audio extract  --input-a A.wav --input-b B.wav --out-dir output
    python -m ummahverse_audio subtract --common output/common.wav --input C.wav \
                                        --metadata output/common.json --output output/c_only.wav
    python -m ummahverse_audio probe    fichier.mkv
    python -m ummahverse_audio gui
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__
from .errors import Cancelled, UmmahAudioError
from .progress import ConsoleProgress
from .separate import METHODS
from .transfer import ESTIMATORS


def _add_common_analysis_options(parser: argparse.ArgumentParser) -> None:
    group = parser.add_argument_group("analyse temps-fréquence")
    group.add_argument("--win-length", type=int, default=8192,
                       help="taille de fenêtre STFT en échantillons (défaut : 8192, soit 171 ms à 48 kHz)")
    group.add_argument("--hop", type=int, default=None,
                       help="pas de la STFT (défaut : win-length / 4)")
    group.add_argument("--smooth-frames", type=int, default=3,
                       help="demi-largeur du lissage temporel des spectres (défaut : 3)")
    group.add_argument("--smooth-bins", type=int, default=2,
                       help="demi-largeur du lissage fréquentiel des spectres (défaut : 2)")
    group.add_argument("--block-seconds", type=float, default=6.0,
                       help="durée des blocs de traitement (défaut : 6 s)")
    group.add_argument("--coherence-threshold", type=float, default=0.9,
                       help="cohérence minimale d'une case dite fiable (défaut : 0.9)")
    group.add_argument("--transfer-estimator", choices=ESTIMATORS, default="power",
                       help="estimateur de l'amplitude de H (défaut : power)")

    group = parser.add_argument_group("alignement")
    group.add_argument("--max-offset", type=float, default=30.0, dest="max_offset_s",
                       help="décalage global maximal recherché, en secondes (défaut : 30)")
    group.add_argument("--local-search", type=float, default=0.35, dest="local_search_s",
                       help="amplitude de recherche du décalage local, en secondes")
    group.add_argument("--confidence-threshold", type=float, default=0.12,
                       help="corrélation minimale pour juger un bloc comparable")

    group = parser.add_argument_group("sorties")
    group.add_argument("--wav-format", choices=("float32", "pcm24", "pcm16"),
                       default="float32",
                       help="format des WAV produits (défaut : float32, sans écrêtage)")
    group.add_argument("--workspace-dir", type=Path, default=None,
                       help="répertoire des fichiers temporaires (défaut : dossier système)")
    group.add_argument("--quiet", action="store_true", help="n'affiche pas la progression")
    group.add_argument("--json", action="store_true", dest="print_json",
                       help="affiche les métadonnées produites sur la sortie standard")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m ummahverse_audio",
        description="POC UmmahVerse : estimation de la bande commune entre deux versions "
                    "linguistiques d'un même montage, puis réutilisation sur une autre langue.",
    )
    parser.add_argument("--version", action="version", version=f"ummahverse_audio {__version__}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    extract = subparsers.add_parser(
        "extract", help="estime le commun de deux langues et les deux résidus",
        description="Produit common.wav, a_only.wav, b_only.wav et common.json.")
    extract.add_argument("--input-a", type=Path, required=True, help="première langue (A)")
    extract.add_argument("--input-b", type=Path, required=True, help="seconde langue (B)")
    extract.add_argument("--out-dir", type=Path, required=True, help="dossier de sortie")
    extract.add_argument("--stream-a", type=int, default=None,
                         help="index du flux audio de A (voir la commande probe)")
    extract.add_argument("--stream-b", type=int, default=None,
                         help="index du flux audio de B (voir la commande probe)")
    extract.add_argument("--sample-rate", type=int, default=None,
                         help="fréquence de travail imposée (défaut : la plus élevée des deux)")
    extract.add_argument("--method", choices=METHODS, default="auto",
                         help="estimateur du commun. « auto » (défaut) choisit wiener si "
                              "les deux versions partagent une relation de phase, et "
                              "magnitude sinon — cas des mixages différents")
    extract.add_argument("--magnitude-oversubtraction", type=float, default=1.0,
                         help="en mode magnitude, au-dessus de 1 on retire davantage de "
                              "fond, au risque d'entamer la voix (défaut : 1)")
    extract.add_argument("--transfer", choices=("band", "gain", "none"), default="band",
                         dest="transfer_mode",
                         help="transformation estimée entre versions (défaut : band)")
    extract.add_argument("--bias-correction", type=float, default=0.0,
                         help="poids de la correction de biais de l'inter-spectre "
                              "(défaut : 0 — mesurée comme défavorable, voir docs/results.md)")
    extract.add_argument("--magnitude-cap", type=float, default=1.5,
                         help="plafond du commun relatif au plus faible des deux mixages")
    extract.add_argument("--allow-unknown-layout", action="store_true",
                         help="accepte un agencement de canaux inconnu (appariement par ordre)")
    _add_common_analysis_options(extract)

    subtract = subparsers.add_parser(
        "subtract", help="retire le commun déjà estimé d'une nouvelle langue",
        description="Réutilise common.wav et common.json sans relire A ni B.")
    subtract.add_argument("--common", type=Path, required=True, help="common.wav")
    subtract.add_argument("--input", type=Path, required=True, dest="input_c",
                          help="piste de la nouvelle langue")
    subtract.add_argument("--output", type=Path, required=True, help="WAV résidu à écrire")
    subtract.add_argument("--metadata", type=Path, default=None,
                          help="common.json produit par extract (fortement recommandé)")
    subtract.add_argument("--report", type=Path, default=None,
                          help="rapport JSON (défaut : même nom que --output en .json)")
    subtract.add_argument("--stream", type=int, default=None,
                          help="index du flux audio de la nouvelle piste")
    subtract.add_argument("--adapt", choices=("global", "block-gain"), default="block-gain",
                          help="adaptation du commun (défaut : block-gain, borné)")
    subtract.add_argument("--block-gain-limit", type=float, default=1.3,
                          help="borne du gain par bloc (défaut : 1.3)")
    subtract.add_argument("--transfer", choices=("band", "gain", "none"), default="band",
                          dest="transfer_mode", help="forme de G(f) (défaut : band)")
    subtract.add_argument("--export-adapted-common", action="store_true",
                          help="écrit aussi le commun adapté, pour l'écoute de contrôle")
    _add_common_analysis_options(subtract)

    analyze = subparsers.add_parser(
        "analyze", help="contrôle la compatibilité et l'alignement sans rien produire",
        description="Même diagnostic que le bouton « Analyser / synchroniser » de "
                    "l'interface : aucun WAV n'est écrit.")
    analyze.add_argument("--input-a", type=Path, required=True)
    analyze.add_argument("--input-b", type=Path, required=True)
    analyze.add_argument("--stream-a", type=int, default=None)
    analyze.add_argument("--stream-b", type=int, default=None)
    analyze.add_argument("--sample-rate", type=int, default=None)
    analyze.add_argument("--transfer", choices=("band", "gain", "none"), default="band",
                         dest="transfer_mode")
    analyze.add_argument("--allow-unknown-layout", action="store_true")
    _add_common_analysis_options(analyze)

    scout = subparsers.add_parser(
        "scout", help="repère les zones d'un film où les deux versions ont une bande commune",
        description="Balaie les deux entrées par fenêtres et indique où la séparation "
                    "a des chances de fonctionner. N'écrit aucun fichier audio.")
    scout.add_argument("--input-a", type=Path, required=True)
    scout.add_argument("--input-b", type=Path, required=True)
    scout.add_argument("--stream-a", type=int, default=None)
    scout.add_argument("--stream-b", type=int, default=None)
    scout.add_argument("--window", type=float, default=20.0, dest="window_s",
                       help="durée des fenêtres de repérage, en secondes (défaut : 20)")
    scout.add_argument("--sample-rate", type=int, default=None)
    scout.add_argument("--allow-unknown-layout", action="store_true")
    scout.add_argument("--workspace-dir", type=Path, default=None)
    scout.add_argument("--report", type=Path, default=None,
                       help="écrit le relevé complet dans ce fichier JSON")
    scout.add_argument("--quiet", action="store_true")
    scout.add_argument("--json", action="store_true", dest="print_json")

    probe = subparsers.add_parser("probe", help="liste les flux audio d'un fichier")
    probe.add_argument("path", type=Path)
    probe.add_argument("--json", action="store_true", dest="print_json")

    subparsers.add_parser("gui", help="ouvre l'interface graphique de bureau")
    return parser


def _run_extract(args: argparse.Namespace) -> int:
    from .extract import ExtractOptions, run_extract

    options = ExtractOptions(
        input_a=args.input_a, input_b=args.input_b, out_dir=args.out_dir,
        stream_a=args.stream_a, stream_b=args.stream_b, sample_rate=args.sample_rate,
        method=args.method, transfer_mode=args.transfer_mode,
        magnitude_oversubtraction=args.magnitude_oversubtraction,
        win_length=args.win_length,
        hop=args.hop, smooth_frames=args.smooth_frames, smooth_bins=args.smooth_bins,
        bias_correction=args.bias_correction, magnitude_cap=args.magnitude_cap,
        block_seconds=args.block_seconds, max_offset_s=args.max_offset_s,
        local_search_s=args.local_search_s,
        confidence_threshold=args.confidence_threshold,
        coherence_threshold=args.coherence_threshold,
        transfer_estimator=args.transfer_estimator,
        allow_unknown_layout=args.allow_unknown_layout, wav_format=args.wav_format,
        workspace_dir=args.workspace_dir,
    )
    result = run_extract(options, ConsoleProgress(quiet=args.quiet))
    if args.print_json:
        json.dump(result.metadata, sys.stdout, indent=2, ensure_ascii=False)
        sys.stdout.write("\n")
    else:
        _report_extract(result)
    return 0


def _print_verdict(verdict) -> None:
    if verdict is None:
        return
    marker = {"absente": "[ÉCHEC]", "faible": "[FAIBLE]",
              "partielle": "[PARTIEL]", "bonne": "[OK]"}.get(verdict.level, "")
    print(f"\n{marker} {verdict.headline}")
    for line in (verdict.detail, verdict.advice):
        if line:
            print(f"  {line}")
    print()


def _report_extract(result) -> None:
    _print_verdict(result.verdict)
    measurements = result.metadata["outputs"]["measurements"]
    alignment = result.metadata["alignment"]
    diagnostics = result.metadata["separation"]["diagnostics"]
    print("Sorties :")
    for path in result.outputs:
        print(f"  {path}")
    print(f"Décalage global : {alignment['global_delay_s'] * 1000:.2f} ms "
          f"(confiance {alignment['global_confidence']:.3f})")
    print(f"Blocs comparables : {alignment['comparable_block_fraction'] * 100:.1f} %")
    print(f"Cohérence pondérée : {diagnostics['energy_weighted_coherence']:.3f} ; "
          f"part du commun dans l'énergie de A : "
          f"{diagnostics['common_share_of_a_energy'] * 100:.1f} %")
    print(f"Erreur de reconstruction max : A {measurements['a']['reconstruction_max_abs_error']:.1e} "
          f"; B {measurements['b']['reconstruction_max_abs_error']:.1e}")
    if result.warnings:
        print("Avertissements :")
        for warning in result.warnings:
            print(f"  - {warning}")


def _run_subtract(args: argparse.Namespace) -> int:
    from .subtract import SubtractOptions, run_subtract

    options = SubtractOptions(
        common=args.common, input_c=args.input_c, output=args.output,
        metadata=args.metadata, report=args.report, stream=args.stream,
        adapt=args.adapt, block_gain_limit=args.block_gain_limit,
        coherence_threshold=args.coherence_threshold, transfer_mode=args.transfer_mode,
        transfer_estimator=args.transfer_estimator,
        win_length=args.win_length, hop=args.hop, smooth_frames=args.smooth_frames,
        smooth_bins=args.smooth_bins, block_seconds=args.block_seconds,
        max_offset_s=args.max_offset_s, local_search_s=args.local_search_s,
        confidence_threshold=args.confidence_threshold, wav_format=args.wav_format,
        export_adapted_common=args.export_adapted_common,
        workspace_dir=args.workspace_dir,
    )
    result = run_subtract(options, ConsoleProgress(quiet=args.quiet))
    if args.print_json:
        json.dump(result.report, sys.stdout, indent=2, ensure_ascii=False)
        sys.stdout.write("\n")
    else:
        measurements = result.report["outputs"]["measurements"]
        print(f"Résidu écrit : {result.output_path}")
        print(f"Rapport : {result.report_path}")
        print(f"Gain médian appliqué au commun : {measurements['applied_gain_median']}")
        print(f"Zones non traitées : {measurements['untreated_seconds']} s")
        print("Erreur de reconstruction max : "
              f"{measurements['reconstruction_max_abs_error']:.1e}")
        for warning in result.warnings:
            print(f"  - {warning}")
    return 0


def _run_analyze(args: argparse.Namespace) -> int:
    from .analyze import run_analysis
    from .extract import ExtractOptions

    options = ExtractOptions(
        input_a=args.input_a, input_b=args.input_b, out_dir=Path("."),
        stream_a=args.stream_a, stream_b=args.stream_b, sample_rate=args.sample_rate,
        transfer_mode=args.transfer_mode, win_length=args.win_length, hop=args.hop,
        smooth_frames=args.smooth_frames, smooth_bins=args.smooth_bins,
        block_seconds=args.block_seconds, max_offset_s=args.max_offset_s,
        local_search_s=args.local_search_s,
        confidence_threshold=args.confidence_threshold,
        coherence_threshold=args.coherence_threshold,
        transfer_estimator=args.transfer_estimator,
        allow_unknown_layout=args.allow_unknown_layout,
        workspace_dir=args.workspace_dir,
    )
    report = run_analysis(options, ConsoleProgress(quiet=args.quiet))
    if args.print_json:
        json.dump(report.payload, sys.stdout, indent=2, ensure_ascii=False)
        sys.stdout.write("\n")
        return 0
    for line in report.summary_lines():
        print(line)
    _print_verdict(report.verdict)
    if report.warnings:
        print("Avertissements :")
        for warning in report.warnings:
            print(f"  - {warning}")
    else:
        print("Aucun avertissement de compatibilité ou d'alignement.")
    return 0


def _run_scout(args: argparse.Namespace) -> int:
    from .metadata import write_json
    from .scout import run_scout

    report = run_scout(
        input_a=args.input_a, input_b=args.input_b, stream_a=args.stream_a,
        stream_b=args.stream_b, window_s=args.window_s, sample_rate=args.sample_rate,
        allow_unknown_layout=args.allow_unknown_layout,
        workspace_dir=args.workspace_dir, progress=ConsoleProgress(quiet=args.quiet))
    if args.report:
        write_json(args.report, report.payload)
    if args.print_json:
        json.dump(report.payload, sys.stdout, indent=2, ensure_ascii=False)
        sys.stdout.write("\n")
        return 0
    for line in report.summary_lines():
        print(line)
    _print_verdict_dict(report.payload["common_bed_verdict"])
    for warning in report.warnings:
        print(f"  - {warning}")
    if args.report:
        print(f"Relevé complet : {args.report}")
    return 0


def _print_verdict_dict(payload: dict) -> None:
    marker = {"absente": "[ÉCHEC]", "faible": "[FAIBLE]",
              "partielle": "[PARTIEL]", "bonne": "[OK]"}.get(payload["level"], "")
    print(f"\n{marker} {payload['headline']}")
    for key in ("detail", "advice"):
        if payload.get(key):
            print(f"  {payload[key]}")
    print()


def _run_probe(args: argparse.Namespace) -> int:
    from dataclasses import asdict

    from .ffmpeg_tools import probe_audio_streams

    streams = probe_audio_streams(args.path)
    if args.print_json:
        json.dump([asdict(s) for s in streams], sys.stdout, indent=2, ensure_ascii=False)
        sys.stdout.write("\n")
        return 0
    print(f"{args.path} — {len(streams)} flux audio :")
    for stream in streams:
        print(f"  {stream.label()}")
    print("Utilisez l'index affiché (#N) avec --stream-a / --stream-b / --stream.")
    return 0


def _run_gui(_args: argparse.Namespace) -> int:
    from .gui import launch

    return launch()


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    handlers = {"extract": _run_extract, "subtract": _run_subtract,
                "analyze": _run_analyze, "scout": _run_scout,
                "probe": _run_probe, "gui": _run_gui}
    try:
        return handlers[args.command](args)
    except Cancelled as exc:
        print(str(exc), file=sys.stderr)
        return 130
    except UmmahAudioError as exc:
        print(f"Erreur : {exc.user_message()}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Interrompu.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
