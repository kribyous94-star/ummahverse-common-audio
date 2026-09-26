"""Contrat CLI exact, tel qu'écrit dans les scripts et la documentation."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from ummahverse_audio.cli import main
from ummahverse_audio.metadata import PARAMS_VERSION, SCHEMA_VERSION

ROOT = Path(__file__).resolve().parent.parent


def test_documented_extract_and_subtract_commands(scene, tmp_path, capsys):
    """Les deux lignes de commande de la spécification doivent fonctionner telles quelles."""
    out_dir = tmp_path / "output"
    code = main(["extract", "--input-a", str(scene.a), "--input-b", str(scene.b),
                 "--out-dir", str(out_dir), "--quiet"])
    assert code == 0
    for name in ("common.wav", "a_only.wav", "b_only.wav", "common.json"):
        assert (out_dir / name).is_file(), name
    printed = capsys.readouterr().out
    assert "Décalage global" in printed

    code = main(["subtract", "--common", str(out_dir / "common.wav"),
                 "--input", str(scene.third), "--metadata", str(out_dir / "common.json"),
                 "--output", str(out_dir / "c_only.wav"), "--quiet"])
    assert code == 0
    assert (out_dir / "c_only.wav").is_file()
    assert (out_dir / "c_only.json").is_file()


def test_explicit_stream_selection_arguments(scene, tmp_path):
    out_dir = tmp_path / "output"
    assert main(["extract", "--input-a", str(scene.a), "--input-b", str(scene.b),
                 "--out-dir", str(out_dir), "--stream-a", "0", "--stream-b", "0",
                 "--quiet"]) == 0
    metadata = json.loads((out_dir / "common.json").read_text(encoding="utf-8"))
    assert metadata["inputs"]["a"]["audio_stream_index"] == 0
    assert metadata["schema_version"] == SCHEMA_VERSION
    assert metadata["params_version"] == PARAMS_VERSION


def test_probe_lists_streams(scene, capsys):
    assert main(["probe", str(scene.a)]) == 0
    printed = capsys.readouterr().out
    assert "flux audio" in printed
    assert "48000 Hz" in printed


def test_probe_json_is_machine_readable(scene, capsys):
    assert main(["probe", str(scene.a), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload[0]["channels"] == 2


def test_analyze_produces_no_files(scene, tmp_path, capsys):
    assert main(["analyze", "--input-a", str(scene.a), "--input-b", str(scene.b),
                 "--quiet"]) == 0
    printed = capsys.readouterr().out
    assert "Décalage global" in printed
    assert not list(tmp_path.glob("*.wav"))


def test_errors_return_non_zero_with_readable_message(tmp_path, capsys):
    code = main(["extract", "--input-a", str(tmp_path / "absent_a.wav"),
                 "--input-b", str(tmp_path / "absent_b.wav"),
                 "--out-dir", str(tmp_path / "out"), "--quiet"])
    assert code == 1
    assert "Erreur" in capsys.readouterr().err


def test_module_entry_point_runs(scene, tmp_path):
    """`python -m ummahverse_audio` doit fonctionner, c'est la forme documentée."""
    completed = subprocess.run(
        [sys.executable, "-m", "ummahverse_audio", "extract",
         "--input-a", str(scene.a), "--input-b", str(scene.b),
         "--out-dir", str(tmp_path / "out"), "--quiet"],
        capture_output=True, text=True, cwd=str(ROOT))
    assert completed.returncode == 0, completed.stderr
    assert (tmp_path / "out" / "common.wav").is_file()


def test_version_flag():
    with pytest.raises(SystemExit) as excinfo:
        main(["--version"])
    assert excinfo.value.code == 0


@pytest.mark.parametrize("script", ["extract.sh", "subtract.sh", "smoke.sh",
                                    "launch.sh", "bootstrap.sh"])
def test_shell_scripts_are_syntactically_valid(script):
    """Les scripts fournis doivent rester exécutables par bash."""
    completed = subprocess.run(["bash", "-n", str(ROOT / "scripts" / script)],
                               capture_output=True, text=True)
    assert completed.returncode == 0, completed.stderr


def test_cli_options_documented_in_readme():
    """Les commandes du README doivent exister réellement dans la CLI."""
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    for fragment in ("python -m ummahverse_audio extract",
                     "python -m ummahverse_audio subtract",
                     "python -m ummahverse_audio gui"):
        assert fragment in readme, fragment
    for option in ("--input-a", "--input-b", "--out-dir", "--common", "--metadata",
                   "--output", "--stream-a", "--stream-b"):
        assert option in readme, option
