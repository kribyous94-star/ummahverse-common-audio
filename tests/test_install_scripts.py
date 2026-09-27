"""Confinement de l'installation et de l'exécution dans le dossier du projet.

L'engagement tenu par `install.sh` / `install.ps1` et `run.sh` / `run.ps1` est simple :
supprimer le dossier du projet supprime tout. Ces tests vérifient la partie de cet
engagement qui dépend du code — l'emplacement des fichiers temporaires — et la
cohérence des scripts eux-mêmes.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import numpy as np
import pytest

from ummahverse_audio.audio_io import ENV_WORKSPACE, Workspace, default_workspace_root

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ("install.sh", "run.sh", "install.ps1", "run.ps1", "install.bat", "run.bat")


def test_workspace_follows_the_environment_variable(tmp_path, monkeypatch):
    """`UMMAHVERSE_WORKSPACE` déplace les fichiers temporaires dans le dossier."""
    contained = tmp_path / "projet" / ".cache" / "workspace"
    monkeypatch.setenv(ENV_WORKSPACE, str(contained))
    with Workspace() as workspace:
        assert contained in workspace.root.parents
        workspace.memmap("essai.acc", (128, 2))
        assert workspace.root.is_dir()
        retained = workspace.root
    # Le répertoire de travail est supprimé à la fermeture : rien ne s'accumule.
    assert not retained.exists()
    assert contained.is_dir()


def test_workspace_falls_back_to_system_temp(monkeypatch):
    monkeypatch.delenv(ENV_WORKSPACE, raising=False)
    assert default_workspace_root() is None
    with Workspace() as workspace:
        assert workspace.root.is_dir()


def test_blank_environment_variable_is_ignored(monkeypatch):
    monkeypatch.setenv(ENV_WORKSPACE, "   ")
    assert default_workspace_root() is None


def test_extract_writes_temporaries_inside_the_configured_workspace(scene, tmp_path,
                                                                    monkeypatch):
    """Un traitement complet ne doit rien écrire hors de l'emplacement configuré."""
    from ummahverse_audio.extract import ExtractOptions, run_extract

    contained = tmp_path / "projet" / ".cache" / "workspace"
    monkeypatch.setenv(ENV_WORKSPACE, str(contained))
    witness: list[Path] = []
    original = Workspace.__init__

    def spy(self, base=None):
        original(self, base)
        witness.append(self.root)

    monkeypatch.setattr(Workspace, "__init__", spy)
    run_extract(ExtractOptions(input_a=scene.a, input_b=scene.b,
                               out_dir=tmp_path / "sorties"))
    assert witness, "aucun espace de travail n'a été créé"
    for root in witness:
        assert contained in root.parents, root
        assert not root.exists(), "l'espace de travail doit être supprimé à la fin"
    assert list(contained.iterdir()) == []


@pytest.mark.parametrize("name", SCRIPTS)
def test_scripts_exist_and_are_not_empty(name):
    script = ROOT / name
    assert script.is_file(), f"{name} manquant à la racine du projet"
    assert script.stat().st_size > 0


@pytest.mark.parametrize("name", ["install.sh", "run.sh"])
def test_shell_scripts_are_valid_bash(name):
    completed = subprocess.run(["bash", "-n", str(ROOT / name)],
                               capture_output=True, text=True)
    assert completed.returncode == 0, completed.stderr


def test_install_help_documents_containment():
    completed = subprocess.run(["bash", str(ROOT / "install.sh"), "--help"],
                               capture_output=True, text=True, cwd=str(ROOT))
    assert completed.returncode == 0, completed.stderr
    text = completed.stdout
    for fragment in (".venv", ".cache", "tools", "--with-ffmpeg", "--no-gui", "--clean"):
        assert fragment in text, fragment
    assert "Supprimer le dossier" in text


def test_install_rejects_unknown_option():
    completed = subprocess.run(["bash", str(ROOT / "install.sh"), "--inconnue"],
                               capture_output=True, text=True, cwd=str(ROOT))
    assert completed.returncode != 0
    assert "Option inconnue" in completed.stderr


def test_run_sh_reports_a_missing_installation(tmp_path):
    """Sans .venv, run.sh doit renvoyer vers install.sh, pas échouer obscurément."""
    import shutil

    sandbox = tmp_path / "copie"
    sandbox.mkdir()
    shutil.copy2(ROOT / "run.sh", sandbox / "run.sh")
    completed = subprocess.run(["bash", str(sandbox / "run.sh")],
                               capture_output=True, text=True)
    assert completed.returncode != 0
    assert "install.sh" in completed.stderr


def executable_lines(script: Path):
    """Lignes réellement exécutées : commentaires de ligne et blocs `<# … #>` exclus."""
    in_block = False
    for number, line in enumerate(script.read_text(encoding="utf-8").splitlines(), 1):
        stripped = line.strip()
        if in_block:
            if "#>" in stripped:
                in_block = False
            continue
        if stripped.startswith("<#"):
            in_block = "#>" not in stripped
            continue
        if not stripped or stripped.startswith("#") or stripped.lower().startswith("rem "):
            continue
        yield number, line


def test_install_scripts_never_write_outside_the_project():
    """Aucun chemin absolu vers le profil utilisateur, sauf pour l'option de purge."""
    suspicious = re.compile(r'(\$HOME|~/|%APPDATA%|%LOCALAPPDATA%|%USERPROFILE%)',
                            re.IGNORECASE)
    for name in ("install.sh", "run.sh", "install.ps1", "run.ps1"):
        for number, line in executable_lines(ROOT / name):
            if not suspicious.search(line):
                continue
            # Seule la purge explicite du cache personnel a le droit d'y toucher.
            assert "purge" in line.lower() or "userCache" in line, (
                f"{name}:{number} touche au profil utilisateur : {line.strip()}")


def test_pip_cache_is_redirected_into_the_project():
    """Sans PIP_CACHE_DIR local, pip remplirait le cache du profil utilisateur."""
    for name in ("install.sh", "install.ps1"):
        text = (ROOT / name).read_text(encoding="utf-8")
        assert "PIP_CACHE_DIR" in text, name
        assert "PYTHONNOUSERSITE" in text, name


def test_batch_wrappers_delegate_to_powershell():
    for batch, target in (("install.bat", "install.ps1"), ("run.bat", "run.ps1")):
        text = (ROOT / batch).read_text(encoding="utf-8")
        assert target in text
        assert "-ExecutionPolicy Bypass" in text
        assert "%*" in text, "les arguments doivent être transmis"


def test_run_scripts_confine_the_workspace():
    for name in ("run.sh", "run.ps1"):
        text = (ROOT / name).read_text(encoding="utf-8")
        assert "UMMAHVERSE_WORKSPACE" in text, name
        assert ".cache" in text and "workspace" in text, name
