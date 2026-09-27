"""Les fichiers d'entrée ne sont jamais modifiés.

Deux façons de le vérifier, et les deux comptent :

* **constat direct** — empreinte du contenu, taille et date de modification relevées
  avant et après un traitement complet ;
* **refus des collisions** — une sortie qui désignerait une entrée est rejetée *avant*
  toute écriture. Ce point n'est pas théorique : FFmpeg écrit avec `-y` et tronque un
  fichier dès qu'il l'ouvre, donc un contrôle a posteriori arriverait trop tard.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
from pathlib import Path

import pytest

from ummahverse_audio.errors import OutputCollisionError
from ummahverse_audio.extract import ExtractOptions, run_extract
from ummahverse_audio.paths import (canonical, ensure_distinct_outputs,
                                    ensure_inputs_untouched, ensure_writable_directory,
                                    same_file)
from ummahverse_audio.subtract import SubtractOptions, run_subtract

from .conftest import SAMPLE_RATE, build_scene


def fingerprint(path: Path) -> tuple:
    """Contenu, taille et date de modification : tout changement se verrait."""
    info = path.stat()
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return digest, info.st_size, info.st_mtime_ns


# --------------------------------------------------------------------------- #
# Constat direct
# --------------------------------------------------------------------------- #
def test_extract_leaves_both_inputs_byte_identical(scene, tmp_path):
    before = {path: fingerprint(path) for path in (scene.a, scene.b)}
    run_extract(ExtractOptions(input_a=scene.a, input_b=scene.b,
                               out_dir=tmp_path / "sorties"))
    for path, reference in before.items():
        assert fingerprint(path) == reference, f"{path.name} a été modifié"


def test_subtract_leaves_common_metadata_and_input_untouched(scene, extracted, tmp_path):
    protected = (extracted.common_path, extracted.metadata_path, scene.third)
    before = {path: fingerprint(path) for path in protected}
    run_subtract(SubtractOptions(
        common=extracted.common_path, input_c=scene.third,
        output=tmp_path / "third_only.wav", metadata=extracted.metadata_path,
        export_adapted_common=True))
    for path, reference in before.items():
        assert fingerprint(path) == reference, f"{path.name} a été modifié"


def test_analysis_creates_no_file_at_all(tmp_path):
    """« Analyser / synchroniser » ne produit aucun fichier : que des nombres.

    L'étape mesure le décalage, la dérive et le gain ; elle n'écrit ni piste recalée,
    ni copie, ni rapport. Le seul fichier qu'elle touche est la copie temporaire de
    décodage, supprimée à la fin.
    """
    from ummahverse_audio.analyze import run_analysis

    scene = build_scene(tmp_path / "source", seconds=4.0)
    protected = {path: fingerprint(path) for path in (scene.a, scene.b, scene.third)}
    inventory = sorted(item.name for item in scene.a.parent.iterdir())

    report = run_analysis(ExtractOptions(input_a=scene.a, input_b=scene.b,
                                         out_dir=tmp_path / "jamais_creee"))

    # Les entrées sont intactes, date de modification comprise.
    for path, reference in protected.items():
        assert fingerprint(path) == reference, f"{path.name} a été modifié"
    # Aucun fichier n'est apparu à côté des entrées.
    assert sorted(item.name for item in scene.a.parent.iterdir()) == inventory
    # Le dossier de sortie n'est même pas créé : l'analyse n'écrit rien.
    assert not (tmp_path / "jamais_creee").exists()
    # Le résultat est un rapport en mémoire, pas un fichier.
    assert report.payload["operation"] == "analyze"
    assert report.payload["alignment"]["global_delay_s"] > 0


def test_analysis_works_on_a_fully_read_only_location(tmp_path):
    """Preuve par le système : rien n'est écrit là où l'écriture est impossible."""
    from ummahverse_audio.analyze import run_analysis

    scene = build_scene(tmp_path / "source", seconds=4.0)
    directory = scene.a.parent
    for path in directory.iterdir():
        path.chmod(stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)
    directory.chmod(stat.S_IRUSR | stat.S_IXUSR)
    try:
        report = run_analysis(ExtractOptions(input_a=scene.a, input_b=scene.b,
                                             out_dir=tmp_path / "ailleurs"))
        assert report.payload["alignment"]["comparable_block_fraction"] > 0.5
    finally:
        directory.chmod(stat.S_IRWXU)
        for path in directory.iterdir():
            path.chmod(stat.S_IRUSR | stat.S_IWUSR)


def test_alignment_is_stored_as_numbers_not_as_realigned_audio(scene, extracted):
    """La synchronisation produit une carte de décalages, pas une piste recalée.

    Rien dans les sorties n'est une copie « redressée » d'une entrée : le décalage est
    une donnée des métadonnées, appliquée en mémoire au moment de l'extraction.
    """
    alignment = extracted.metadata["alignment"]
    assert isinstance(alignment["global_delay_s"], float)
    assert alignment["comparable_segments"], "la carte de décalages doit être publiée"
    entries = extracted.metadata["timeline"]["entries"]
    assert entries and all("delay_s" in entry for entry in entries)
    # Les sorties sont dans les repères documentés, pas dans un repère « synchronisé ».
    outputs = extracted.metadata["outputs"]
    assert outputs["common_wav"]["timebase"] == "A"
    assert outputs["a_only_wav"]["timebase"] == "A"
    assert outputs["b_only_wav"]["timebase"] == "B"


def test_inputs_can_be_read_only_files(tmp_path):
    """Un traitement doit réussir même si les entrées sont en lecture seule.

    C'est la preuve la plus nette que rien n'est écrit dessus : si le moteur essayait,
    le système refuserait.
    """
    scene = build_scene(tmp_path / "source", seconds=4.0)
    for path in (scene.a, scene.b):
        path.chmod(stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)
    try:
        result = run_extract(ExtractOptions(input_a=scene.a, input_b=scene.b,
                                           out_dir=tmp_path / "sorties"))
        assert result.common_path.is_file()
        for path in (scene.a, scene.b):
            assert not os.access(path, os.W_OK), "l'entrée devait rester en lecture seule"
    finally:
        for path in (scene.a, scene.b):
            path.chmod(stat.S_IRUSR | stat.S_IWUSR)


def test_output_directory_may_contain_the_inputs(tmp_path):
    """Écrire à côté des entrées est permis ; seul l'écrasement d'un fichier est refusé."""
    scene = build_scene(tmp_path / "ensemble", seconds=4.0)
    before = {path: fingerprint(path) for path in (scene.a, scene.b)}
    result = run_extract(ExtractOptions(input_a=scene.a, input_b=scene.b,
                                       out_dir=scene.a.parent))
    assert result.common_path.parent == scene.a.parent
    for path, reference in before.items():
        assert fingerprint(path) == reference


# --------------------------------------------------------------------------- #
# Refus des collisions
# --------------------------------------------------------------------------- #
def test_subtract_refuses_to_write_over_its_input(scene, extracted, tmp_path):
    before = fingerprint(scene.third)
    with pytest.raises(OutputCollisionError) as excinfo:
        run_subtract(SubtractOptions(
            common=extracted.common_path, input_c=scene.third,
            output=scene.third, metadata=extracted.metadata_path))
    message = excinfo.value.user_message()
    assert "jamais modifiés" in message
    assert fingerprint(scene.third) == before, "l'entrée a été touchée malgré le refus"


def test_subtract_refuses_to_write_over_the_common(scene, extracted):
    before = fingerprint(extracted.common_path)
    with pytest.raises(OutputCollisionError):
        run_subtract(SubtractOptions(
            common=extracted.common_path, input_c=scene.third,
            output=extracted.common_path, metadata=extracted.metadata_path))
    assert fingerprint(extracted.common_path) == before


def test_subtract_refuses_to_write_the_report_over_the_metadata(scene, extracted, tmp_path):
    before = fingerprint(extracted.metadata_path)
    with pytest.raises(OutputCollisionError):
        run_subtract(SubtractOptions(
            common=extracted.common_path, input_c=scene.third,
            output=tmp_path / "sortie.wav", metadata=extracted.metadata_path,
            report=extracted.metadata_path))
    assert fingerprint(extracted.metadata_path) == before
    # Les métadonnées restent exploitables : rien n'a été tronqué.
    assert json.loads(extracted.metadata_path.read_text(encoding="utf-8"))["operation"] == "extract"


@pytest.mark.parametrize("name", ["common.wav", "a_only.wav", "b_only.wav", "common.json"])
def test_extract_refuses_when_an_output_name_collides_with_an_input(tmp_path, name):
    """Une entrée qui porte le nom d'une sortie, dans le dossier de sortie."""
    scene = build_scene(tmp_path / "source", seconds=4.0)
    collision = tmp_path / "sorties" / name
    collision.parent.mkdir(parents=True, exist_ok=True)
    collision.write_bytes(scene.a.read_bytes())
    before = fingerprint(collision)
    with pytest.raises(OutputCollisionError):
        run_extract(ExtractOptions(input_a=collision, input_b=scene.b,
                                   out_dir=tmp_path / "sorties"))
    assert fingerprint(collision) == before


def test_collision_is_detected_through_a_symlink(scene, extracted, tmp_path):
    alias = tmp_path / "alias.wav"
    try:
        alias.symlink_to(scene.third)
    except (OSError, NotImplementedError):
        pytest.skip("les liens symboliques ne sont pas disponibles ici")
    before = fingerprint(scene.third)
    with pytest.raises(OutputCollisionError):
        run_subtract(SubtractOptions(
            common=extracted.common_path, input_c=scene.third,
            output=alias, metadata=extracted.metadata_path))
    assert fingerprint(scene.third) == before


def test_collision_is_detected_through_a_hard_link(scene, extracted, tmp_path):
    alias = tmp_path / "dur.wav"
    try:
        os.link(scene.third, alias)
    except (OSError, NotImplementedError):
        pytest.skip("les liens durs ne sont pas disponibles ici")
    before = fingerprint(scene.third)
    with pytest.raises(OutputCollisionError):
        run_subtract(SubtractOptions(
            common=extracted.common_path, input_c=scene.third,
            output=alias, metadata=extracted.metadata_path))
    assert fingerprint(scene.third) == before


def test_collision_is_detected_through_a_relative_path(scene, extracted, tmp_path, monkeypatch):
    monkeypatch.chdir(scene.third.parent)
    before = fingerprint(scene.third)
    with pytest.raises(OutputCollisionError):
        run_subtract(SubtractOptions(
            common=extracted.common_path, input_c=scene.third,
            output=Path("./") / scene.third.name, metadata=extracted.metadata_path))
    assert fingerprint(scene.third) == before


# --------------------------------------------------------------------------- #
# Fonctions de comparaison de chemins
# --------------------------------------------------------------------------- #
def test_same_file_handles_indirection(tmp_path):
    original = tmp_path / "piste.wav"
    original.write_bytes(b"contenu")
    assert same_file(original, original)
    assert same_file(original, tmp_path / "sous" / ".." / "piste.wav")
    assert not same_file(original, tmp_path / "autre.wav")
    # Deux chemins inexistants mais identiques doivent être reconnus comme tels.
    assert same_file(tmp_path / "futur.wav", tmp_path / "." / "futur.wav")


def test_canonical_is_absolute_and_normalised(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert canonical("piste.wav") == canonical(tmp_path / "piste.wav")
    assert Path(canonical("piste.wav")).is_absolute()


def test_ensure_distinct_outputs_rejects_duplicates(tmp_path):
    ensure_distinct_outputs([tmp_path / "a.wav", tmp_path / "b.wav"])
    with pytest.raises(OutputCollisionError) as excinfo:
        ensure_distinct_outputs([tmp_path / "a.wav", tmp_path / "sous" / ".." / "a.wav"])
    assert "écraserait" in excinfo.value.user_message()


def test_ensure_inputs_untouched_ignores_none():
    ensure_inputs_untouched([None], [None])


def test_ensure_writable_directory_rejects_a_file(tmp_path):
    occupied = tmp_path / "pas_un_dossier"
    occupied.write_text("x", encoding="utf-8")
    with pytest.raises(OutputCollisionError) as excinfo:
        ensure_writable_directory(occupied)
    assert "pas un dossier" in excinfo.value.user_message().lower()


@pytest.mark.skipif(os.geteuid() == 0 if hasattr(os, "geteuid") else False,
                    reason="root écrit partout, le test n'aurait pas de sens")
def test_ensure_writable_directory_rejects_a_read_only_location(tmp_path):
    locked = tmp_path / "verrouille"
    locked.mkdir()
    locked.chmod(stat.S_IRUSR | stat.S_IXUSR)
    try:
        with pytest.raises(OutputCollisionError) as excinfo:
            ensure_writable_directory(locked / "sous_dossier")
        assert "droit d'écrire" in excinfo.value.user_message()
    finally:
        locked.chmod(stat.S_IRWXU)


def test_extract_reports_a_readable_error_for_an_unwritable_output(tmp_path):
    scene = build_scene(tmp_path / "source", seconds=4.0)
    locked = tmp_path / "verrouille"
    locked.mkdir()
    locked.chmod(stat.S_IRUSR | stat.S_IXUSR)
    try:
        with pytest.raises(OutputCollisionError) as excinfo:
            run_extract(ExtractOptions(input_a=scene.a, input_b=scene.b,
                                       out_dir=locked / "sorties"))
        assert "écrire" in excinfo.value.user_message()
    finally:
        locked.chmod(stat.S_IRWXU)
