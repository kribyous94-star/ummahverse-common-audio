"""Contenu et exhaustivité de `common.json` : c'est le contrat entre les deux commandes."""

from __future__ import annotations

import json

import pytest

from ummahverse_audio.metadata import SCHEMA_VERSION, load_common_metadata


def test_metadata_is_valid_json_and_loadable(extracted):
    payload = json.loads(extracted.metadata_path.read_text(encoding="utf-8"))
    assert payload == extracted.metadata
    assert load_common_metadata(extracted.metadata_path)["schema_version"] == SCHEMA_VERSION


def test_metadata_records_inputs_and_selected_streams(extracted, scene):
    inputs = extracted.metadata["inputs"]
    assert inputs["a"]["path"] == str(scene.a)
    assert inputs["b"]["path"] == str(scene.b)
    for side in ("a", "b"):
        entry = inputs[side]
        assert entry["audio_stream_index"] == 0
        assert entry["channels"] == 2
        assert entry["channel_layout"] == "stereo"
        assert entry["working_sample_rate"] == scene.sample_rate
        assert entry["resampled"] is False
        assert entry["lossless"] is True
        assert entry["frames"] > 0


def test_metadata_documents_timebase_and_origin(extracted, scene):
    reference = extracted.metadata["common_timebase"]
    assert reference["reference"] == "A"
    assert reference["origin_s"] == 0.0
    assert reference["sample_rate"] == scene.sample_rate
    assert reference["channels"] == 2
    assert "trame 0" in reference["note"]


def test_metadata_documents_alignment_with_sign_convention(extracted, scene):
    alignment = extracted.metadata["alignment"]
    assert "delay > 0" in alignment["sign_convention"]
    assert abs(alignment["global_delay_s"] - scene.offset_b / scene.sample_rate) < 0.005
    assert 0.0 <= alignment["global_confidence"] <= 1.0
    assert "corrélation" in alignment["confidence_definition"]
    assert alignment["comparable_block_fraction"] > 0.9
    assert isinstance(alignment["comparable_segments"], list)
    assert isinstance(alignment["non_comparable_segments"], list)
    assert "drift_ppm" in alignment


def test_metadata_documents_transfer_and_separation(extracted):
    transfer = extracted.metadata["transfer"]
    assert transfer["mode"] == "band"
    assert transfer["estimator"] == "power"
    assert "sqrt" in transfer["amplitude_rule"]
    assert len(transfer["broadband_gain_per_channel"]) == 2
    assert transfer["band_gain_db_per_channel"], "le détail par bande doit être présent"

    separation = extracted.metadata["separation"]
    assert separation["method"] == "wiener"
    stft = separation["stft"]
    assert stft["win_length"] == 8192
    assert stft["hop"] == 2048
    assert stft["smoothing_time_s"] > 0
    assert stft["smoothing_freq_hz"] > 0
    assert separation["blocks"]["block_s"] > 0
    assert separation["blocks"]["crossfade_s"] > 0


def test_metadata_confidences_are_defined_not_invented(extracted):
    diagnostics = extracted.metadata["separation"]["diagnostics"]
    assert 0.0 <= diagnostics["energy_weighted_coherence"] <= 1.0
    assert 0.0 <= diagnostics["common_share_of_a_energy"] <= 1.5
    assert diagnostics["blocks_processed"] >= 1
    timeline = extracted.metadata["timeline"]
    assert timeline["stride"] >= 1
    assert timeline["entries"], "la chronologie par bloc doit être présente"
    first = timeline["entries"][0]
    for key in ("t_s", "delay_s", "alignment_confidence", "comparable"):
        assert key in first, key


def test_metadata_reports_measurements_and_untreated_zones(extracted):
    measurements = extracted.metadata["outputs"]["measurements"]
    for side in ("a", "b"):
        entry = measurements[side]
        assert entry["reconstruction_max_abs_error"] < 1e-6
        assert entry["peak_common"] > 0
        assert isinstance(entry["untreated_ranges"], list)
        assert entry["untreated_seconds"] >= 0


def test_metadata_states_limitations_explicitly(extracted):
    limitations = extracted.metadata["limitations"]
    joined = " ".join(limitations)
    assert "résidus estimés" in joined
    assert "Cohérence" in joined or "cohérence" in joined
    assert "musique" in joined
    assert "n'est pas réalisé par ce POC" in joined


def test_metadata_records_environment_and_performance(extracted):
    environment = extracted.metadata["environment"]
    assert environment["ffmpeg"]
    assert environment["python"]
    performance = extracted.metadata["performance"]
    assert performance["elapsed_s"] > 0
    assert "decode" in performance["stages_s"]


def test_timeline_is_decimated_on_long_material(scene, tmp_path, monkeypatch):
    """Sur un film, la chronologie est décimée pour rester lisible, et le dit."""
    from ummahverse_audio import extract as extract_module

    monkeypatch.setattr(extract_module, "TIMELINE_LIMIT", 2)
    result = extract_module.run_extract(extract_module.ExtractOptions(
        input_a=scene.a, input_b=scene.b, out_dir=tmp_path / "out", block_seconds=1.5))
    timeline = result.metadata["timeline"]
    assert timeline["stride"] > 1
    assert len(timeline["entries"]) <= 2
    assert "décimée" in timeline["note"]


def test_load_common_metadata_rejects_wrong_schema(tmp_path):
    from ummahverse_audio.errors import MetadataError

    path = tmp_path / "vieux.json"
    path.write_text(json.dumps({"operation": "extract", "schema_version": "99.0"}),
                    encoding="utf-8")
    with pytest.raises(MetadataError) as excinfo:
        load_common_metadata(path)
    assert "incompatible" in str(excinfo.value)
