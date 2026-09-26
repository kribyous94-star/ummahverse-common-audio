"""Réutilisation du commun sur une troisième langue, sans relire A ni B."""

from __future__ import annotations

import json

import numpy as np
import pytest

from ummahverse_audio.errors import IncompatibleLayoutError, MetadataError
from ummahverse_audio.metrics import EvaluationWindow, decompose, read_audio, si_sdr
from ummahverse_audio.subtract import SubtractOptions, run_subtract

from .conftest import SAMPLE_RATE, write_wav


def window_for(scene) -> slice:
    return EvaluationWindow(sample_rate=scene.sample_rate).slice_for(scene.common.shape[0])


@pytest.fixture(scope="module")
def subtracted(scene, extracted, tmp_path_factory):
    out = tmp_path_factory.mktemp("subtract")
    return run_subtract(SubtractOptions(
        common=extracted.common_path, input_c=scene.third,
        output=out / "third_only.wav", metadata=extracted.metadata_path,
        export_adapted_common=True))


def test_third_language_residual_keeps_its_voice(scene, subtracted):
    residual, rate = read_audio(subtracted.output_path)
    assert rate == scene.sample_rate
    span = window_for(scene)
    parts = decompose(residual, {"commun": scene.common_in_third(),
                                 "voix_C": scene.voice_c, "voix_A": scene.voice_a}, span)
    assert parts["voix_C_db"] > -1.5, "la voix de la troisième langue a été abîmée"
    assert parts["commun_db"] < -13.0, "la bande commune reste trop présente"
    assert si_sdr(residual, scene.voice_c, span) > 8.0


def test_residual_matches_input_timebase_and_length(scene, subtracted):
    residual, _ = read_audio(subtracted.output_path)
    original, _ = read_audio(scene.third)
    assert residual.shape == original.shape
    assert np.isfinite(residual).all()


def test_reconstruction_from_adapted_common_is_exact(scene, subtracted):
    """commun adapté + résidu doit redonner la troisième piste."""
    adapted, _ = read_audio(subtracted.adapted_common_path)
    residual, _ = read_audio(subtracted.output_path)
    original, _ = read_audio(scene.third)
    length = min(adapted.shape[0], residual.shape[0], original.shape[0])
    error = float(np.abs(adapted[:length] + residual[:length] - original[:length]).max())
    assert error < 1e-6, error
    reported = subtracted.report["outputs"]["measurements"]["reconstruction_max_abs_error"]
    assert reported < 1e-6


def test_report_documents_alignment_and_applied_transform(scene, subtracted):
    report = subtracted.report
    assert report["operation"] == "subtract"
    alignment = report["alignment"]
    expected = (scene.offset_c - scene.offset_b) / scene.sample_rate
    # Le commun est dans la base de A ; la troisième piste est en retard de offset_c.
    assert abs(alignment["global_delay_s"] - scene.offset_c / scene.sample_rate) < 0.01, (
        alignment["global_delay_s"], expected)
    transform = report["applied_transform"]
    assert transform["adapt_mode"] == "block-gain"
    assert transform["block_gain_limit"] == pytest.approx(1.3)
    gains = [entry["applied_gain"] for entry in report["timeline"]["entries"]
             if entry["comparable"]]
    assert gains, "aucun bloc comparable dans le rapport"
    assert all(1 / 1.3 - 1e-6 <= gain <= 1.3 + 1e-6 for gain in gains), gains
    # G(f) doit retrouver le rapport de gain entre le commun (base A) et la 3e langue.
    for gain in transform["broadband_gain_per_channel"]:
        assert abs(gain - scene.gain_c) < 0.06 * scene.gain_c, transform


def test_subtract_never_reads_the_original_inputs(scene, extracted, tmp_path):
    """Le contrat : `subtract` n'a besoin que du commun, des métadonnées et de C."""
    import shutil

    workdir = tmp_path / "isole"
    workdir.mkdir()
    for source in (extracted.common_path, extracted.metadata_path, scene.third):
        shutil.copy2(source, workdir / source.name)
    # A et B sont rendus inaccessibles : le chemin enregistré dans common.json ne doit
    # jamais être ouvert.
    moved = tmp_path / "deplaces"
    moved.mkdir()
    for source in (scene.a, scene.b):
        shutil.move(str(source), moved / source.name)
    try:
        result = run_subtract(SubtractOptions(
            common=workdir / "common.wav", input_c=workdir / "third.wav",
            output=workdir / "third_only.wav", metadata=workdir / "common.json"))
        assert result.output_path.is_file()
        assert result.report["inputs"]["source_inputs"], (
            "le rapport doit tracer les entrées d'origine, sans les lire")
    finally:
        for source in (scene.a, scene.b):
            shutil.move(str(moved / source.name), source)


def test_metadata_mismatch_is_reported(scene, extracted, tmp_path):
    """Un common.json qui annonce un autre agencement doit produire un avertissement."""
    altered = json.loads(extracted.metadata_path.read_text(encoding="utf-8"))
    altered["common_timebase"]["channels"] = 6
    path = tmp_path / "common_faux.json"
    path.write_text(json.dumps(altered), encoding="utf-8")
    result = run_subtract(SubtractOptions(
        common=extracted.common_path, input_c=scene.third,
        output=tmp_path / "out.wav", metadata=path))
    assert any("dépareillés" in warning for warning in result.warnings), result.warnings


def test_invalid_metadata_is_refused(scene, extracted, tmp_path):
    bad = tmp_path / "pas_des_metadonnees.json"
    bad.write_text('{"operation": "autre chose"}', encoding="utf-8")
    with pytest.raises(MetadataError):
        run_subtract(SubtractOptions(common=extracted.common_path, input_c=scene.third,
                                     output=tmp_path / "out.wav", metadata=bad))
    with pytest.raises(MetadataError):
        run_subtract(SubtractOptions(common=extracted.common_path, input_c=scene.third,
                                     output=tmp_path / "out.wav",
                                     metadata=tmp_path / "absent.json"))


def test_channel_count_mismatch_is_refused(extracted, tmp_path):
    mono = write_wav(tmp_path / "mono.wav", np.zeros((SAMPLE_RATE * 3, 1), dtype=np.float32))
    with pytest.raises(IncompatibleLayoutError) as excinfo:
        run_subtract(SubtractOptions(common=extracted.common_path, input_c=mono,
                                     output=tmp_path / "out.wav",
                                     metadata=extracted.metadata_path))
    assert "downmix" in excinfo.value.user_message()


def test_subtract_without_metadata_warns(scene, extracted, tmp_path):
    result = run_subtract(SubtractOptions(
        common=extracted.common_path, input_c=scene.third,
        output=tmp_path / "sans_meta.wav"))
    assert any("common.json" in warning for warning in result.warnings)
    assert result.report_path == (tmp_path / "sans_meta.json")


def test_global_adapt_mode_applies_no_block_gain(scene, extracted, tmp_path):
    result = run_subtract(SubtractOptions(
        common=extracted.common_path, input_c=scene.third,
        output=tmp_path / "global.wav", metadata=extracted.metadata_path,
        adapt="global"))
    gains = [entry["applied_gain"] for entry in result.report["timeline"]["entries"]
             if entry["comparable"]]
    assert gains and all(gain == 1.0 for gain in gains), gains
    residual, _ = read_audio(result.output_path)
    span = window_for(scene)
    assert si_sdr(residual, scene.voice_c, span) > 6.0
