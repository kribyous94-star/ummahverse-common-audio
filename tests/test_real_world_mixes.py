"""Cas rencontrés sur du vrai matériel de film, et pas sur le jeu synthétique.

Chacun de ces tests correspond à un défaut constaté sur deux pistes 5.1 réelles d'un
même film en anglais et en français :

* la piste d'analyse sommait les six canaux, donc le canal central — le dialogue,
  c'est-à-dire précisément ce qui diffère — noyait le contenu commun et l'alignement
  échouait purement et simplement ;
* les surrounds avaient leur propre retard (+68 ms) : avec un décalage unique, ils ne
  s'annulaient pas du tout ;
* deux versions remixées ne se corrèlent plus en forme d'onde, mais leurs enveloppes
  d'énergie restent très proches — il faut pouvoir aligner quand même, pour distinguer
  « impossible d'aligner » de « aligné, mais rien en commun » ;
* et surtout : quand il n'y a pas de bande commune, l'outil doit le **dire**, pas
  rendre un fichier silencieux sans explication.
"""

from __future__ import annotations

import numpy as np
import pytest
from scipy.signal import butter, sosfilt

from ummahverse_audio.align import (DelayEstimate, build_offset_map,
                                    estimate_channel_offsets, estimate_global_delay,
                                    envelope_delay)
from ummahverse_audio.audio_io import Workspace, analysis_weights, load_input
from ummahverse_audio.blocks import BlockPlan
from ummahverse_audio.extract import ExtractOptions, run_extract
from ummahverse_audio.stft import StftConfig, debias_coherence
from ummahverse_audio.verdict import judge

from .conftest import SAMPLE_RATE, make_music, make_voice, shift, write_wav


def remixed_bed(n, seed, reference):
    """Un lit musical **réellement** indépendant, de caractère spectral comparable.

    `make_music` repose sur des partiels déterministes (220/330/440 Hz) : deux appels
    avec des graines différentes restent fortement corrélés et ne simulent donc pas
    un remixage. Il faut un contenu large bande indépendant.
    """
    rng = np.random.default_rng(seed)
    sos = butter(3, [120, 7500], btype="bandpass", fs=SAMPLE_RATE, output="sos")
    bed = sosfilt(sos, rng.normal(size=n))
    for freq in (196.0, 277.0, 415.0):   # partiels propres à cette version
        phase = rng.uniform(0, 2 * np.pi)
        bed += 0.8 * np.sin(2 * np.pi * freq * np.arange(n) / SAMPLE_RATE + phase)
    bed = 0.12 * bed / max(float(np.std(bed)), 1e-12)
    # Mêmes scènes, donc même dynamique : c'est ce qui permet à l'alignement par
    # enveloppe de réussir là où la forme d'onde échoue. Seul le contenu diffère.
    envelope = np.abs(reference)
    window = int(0.2 * SAMPLE_RATE)
    envelope = np.convolve(envelope, np.ones(window) / window, mode="same")
    envelope /= max(float(np.mean(envelope)), 1e-12)
    return bed * envelope


def surround_scene(directory, seconds=14.0, surround_offset=0, common_delay=0,
                   shared=True, seed=7):
    """Deux versions 5.1 : dialogue au centre, musique aux autres canaux."""
    rng = np.random.default_rng(seed)
    n = int(seconds * SAMPLE_RATE)
    music = make_music(n, rng)
    other = remixed_bed(n, seed + 100, music)
    voice_a, voice_b = make_voice(n, 143, 0.0), make_voice(n, 187, 0.16)

    def build(dialogue, bed, surround_shift):
        surround = shift(bed, surround_shift) if surround_shift else bed
        return np.column_stack([
            0.9 * bed,                      # FL
            0.85 * bed,                     # FR
            3.0 * dialogue,                 # FC : dialogue, fort, différent
            0.2 * bed,                      # LFE
            0.6 * surround,                 # SL
            0.6 * surround,                 # SR
        ])

    bed_b = music if shared else other
    a = write_wav(directory / "a.wav", build(voice_a, music, 0))
    b = write_wav(directory / "b.wav",
                  build(voice_b, shift(bed_b, common_delay) if common_delay else bed_b,
                        surround_offset))
    return a, b


def declare_51(source, destination):
    import subprocess

    from ummahverse_audio.ffmpeg_tools import find_ffmpeg

    subprocess.run([str(find_ffmpeg()), "-hide_banner", "-v", "error", "-y",
                    "-i", str(source), "-channel_layout", "5.1",
                    "-c:a", "pcm_f32le", str(destination)], check=True)
    return destination


# --------------------------------------------------------------------------- #
# Piste d'analyse : le canal central ne doit pas piloter l'alignement
# --------------------------------------------------------------------------- #
def test_analysis_weights_exclude_dialogue_and_lfe():
    weights = analysis_weights("5.1(side)", 6)
    assert weights[2] == 0.0, "le canal central porte le dialogue"
    assert weights[3] == 0.0, "le LFE est trop étroit en bande"
    assert weights[0] > 0 and weights[1] > 0
    assert weights.sum() == pytest.approx(1.0)
    # Stéréo et mono : tous les canaux comptent.
    assert analysis_weights("stereo", 2).tolist() == [0.5, 0.5]
    assert analysis_weights("mono", 1).tolist() == [1.0]


def test_loud_dialogue_centre_does_not_break_alignment(tmp_path):
    """Un centre trois fois plus fort que la musique ne doit pas fausser l'alignement."""
    delay = 1440  # 30 ms
    a, b = surround_scene(tmp_path / "src", common_delay=delay)
    a51 = declare_51(a, tmp_path / "a51.wav")
    b51 = declare_51(b, tmp_path / "b51.wav")
    with Workspace(tmp_path) as workspace:
        audio_a = load_input(a51, workspace, "a")
        audio_b = load_input(b51, workspace, "b")
        track_a, rate = audio_a.analysis_track()
        track_b, _ = audio_b.analysis_track()
        estimate = estimate_global_delay(track_a, track_b, rate)
        measured = estimate.delay * SAMPLE_RATE / rate
    assert abs(measured - delay) < 0.02 * SAMPLE_RATE, measured


# --------------------------------------------------------------------------- #
# Retard propre à chaque canal
# --------------------------------------------------------------------------- #
def test_per_channel_offset_is_measured(tmp_path):
    """Des surrounds décalés de 60 ms doivent être détectés canal par canal."""
    surround = 2880  # 60 ms
    a, b = surround_scene(tmp_path / "src", surround_offset=surround)
    a51 = declare_51(a, tmp_path / "a51.wav")
    b51 = declare_51(b, tmp_path / "b51.wav")
    config = StftConfig(sample_rate=SAMPLE_RATE)
    plan = BlockPlan.for_config(config, 3.0)
    with Workspace(tmp_path) as workspace:
        audio_a = load_input(a51, workspace, "a")
        audio_b = load_input(b51, workspace, "b")
        track_a, rate = audio_a.analysis_track()
        track_b, _ = audio_b.analysis_track()
        global_delay = estimate_global_delay(track_a, track_b, rate)
        scaled = DelayEstimate(global_delay.delay * SAMPLE_RATE / rate,
                               int(round(global_delay.integer_delay * SAMPLE_RATE / rate)),
                               global_delay.confidence, global_delay.peak_ratio,
                               method=global_delay.method)
        offset_map = build_offset_map(audio_a, audio_b, plan.advance, scaled,
                                      tracks=(track_a, track_b, rate))
        offsets, confidence = estimate_channel_offsets(audio_a, audio_b, offset_map, plan)
    assert abs(offsets[0]) < 0.004 * SAMPLE_RATE, offsets
    assert abs(offsets[4] - surround) < 0.006 * SAMPLE_RATE, offsets
    assert abs(offsets[5] - surround) < 0.006 * SAMPLE_RATE, offsets
    assert confidence[4] > 0.3 and confidence[5] > 0.3, confidence


def test_offset_surrounds_are_actually_cancelled(tmp_path):
    """Sans recalage par canal, les surrounds ressortaient entiers dans le résidu."""
    a, b = surround_scene(tmp_path / "src", surround_offset=2880)
    a51 = declare_51(a, tmp_path / "a51.wav")
    b51 = declare_51(b, tmp_path / "b51.wav")
    result = run_extract(ExtractOptions(input_a=a51, input_b=b51,
                                        out_dir=tmp_path / "out", block_seconds=3.0))
    from ummahverse_audio.metrics import read_audio

    original, _ = read_audio(a51)
    residual, _ = read_audio(result.a_only_path)
    span = slice(SAMPLE_RATE, original.shape[0] - SAMPLE_RATE)
    for channel in (4, 5):
        before = float(np.sqrt(np.mean(original[span, channel] ** 2)))
        after = float(np.sqrt(np.mean(residual[span, channel] ** 2)))
        attenuation = 20 * np.log10(max(after, 1e-12) / max(before, 1e-12))
        assert attenuation < -6.0, (channel, attenuation)


# --------------------------------------------------------------------------- #
# Alignement par enveloppe quand les formes d'onde diffèrent
# --------------------------------------------------------------------------- #
def test_envelope_aligns_two_different_mixes(tmp_path):
    """Deux mixages différents des mêmes scènes : l'enveloppe doit retrouver le décalage."""
    rng = np.random.default_rng(11)
    n = 30 * SAMPLE_RATE
    gate = (np.sin(2 * np.pi * 0.23 * np.arange(n) / SAMPLE_RATE) > -0.2).astype(float)
    gate = np.convolve(gate, np.ones(4801) / 4801, mode="same")
    sos = butter(3, [150, 7000], btype="bandpass", fs=SAMPLE_RATE, output="sos")
    # Contenus totalement indépendants, mais même dynamique : c'est un remixage.
    first = sosfilt(sos, rng.normal(size=n)) * gate
    second = sosfilt(sos, np.random.default_rng(12).normal(size=n)) * gate
    delay = 2400  # 50 ms
    estimate = envelope_delay(first, shift(second, delay), SAMPLE_RATE)
    assert estimate.method == "envelope"
    assert abs(estimate.delay - delay) < 0.03 * SAMPLE_RATE, estimate.delay
    assert estimate.confidence > 0.5, estimate.confidence


def test_remixed_versions_align_but_report_no_common_bed(tmp_path):
    """Le cas réel : même montage, mixages différents. Aligner, puis dire la vérité."""
    from ummahverse_audio.errors import AlignmentError

    a, b = surround_scene(tmp_path / "src", shared=False, common_delay=960)
    a51 = declare_51(a, tmp_path / "a51.wav")
    b51 = declare_51(b, tmp_path / "b51.wav")
    try:
        result = run_extract(ExtractOptions(input_a=a51, input_b=b51,
                                            out_dir=tmp_path / "out", block_seconds=3.0))
    except AlignmentError as exc:
        # Refus explicite : c'est l'autre réponse correcte, et elle doit rester lisible.
        assert "Aucun alignement exploitable" in exc.user_message()
        assert "montage" in exc.user_message()
        return
    verdict = result.verdict
    assert verdict is not None
    assert verdict.level in ("absente", "faible"), verdict.as_dict()
    assert not verdict.usable
    # Le verdict doit remonter dans les avertissements et dans les métadonnées.
    assert any(verdict.headline in warning for warning in result.warnings)
    assert result.metadata["common_bed_verdict"]["level"] == verdict.level


# --------------------------------------------------------------------------- #
# Cohérence : le plancher de biais ne doit pas passer pour du contenu commun
# --------------------------------------------------------------------------- #
def test_coherence_debias_removes_the_noise_floor():
    # Avec ~10 moyennes indépendantes, deux signaux sans rapport donnent déjà 0,1.
    assert debias_coherence(0.10, 10.0) == pytest.approx(0.0, abs=1e-9)
    assert debias_coherence(0.05, 10.0) == 0.0
    assert debias_coherence(1.0, 10.0) == pytest.approx(1.0)
    assert debias_coherence(0.73, 10.0) == pytest.approx(0.70, abs=0.01)


@pytest.mark.parametrize("coherence,expected", [
    (0.005, "absente"), (0.08, "faible"), (0.20, "partielle"), (0.70, "bonne"),
])
def test_verdict_levels_and_wording(coherence, expected):
    verdict = judge(coherence, 0.05, 0.5, "envelope", 0.8)
    assert verdict.level == expected
    assert verdict.headline and verdict.detail
    assert verdict.usable == (expected in ("partielle", "bonne"))


def test_verdict_separates_misalignment_from_missing_common_bed():
    aligned = judge(0.001, 0.0, 0.0, "envelope", 0.90)
    assert "synchrones" in aligned.detail
    assert "remix" in aligned.detail.lower()
    unaligned = judge(0.001, 0.0, 0.0, "envelope", 0.05)
    assert "alignement" in unaligned.headline.lower()
    assert "flux audio" in unaligned.advice


# --------------------------------------------------------------------------- #
# Repérage des zones exploitables (`scout`)
# --------------------------------------------------------------------------- #
def test_scout_finds_the_shared_region_and_ignores_the_rest(tmp_path):
    """Un film dont seule une partie partage un master : scout doit la désigner."""
    from ummahverse_audio.scout import run_scout

    rng = np.random.default_rng(3)
    n = 120 * SAMPLE_RATE
    shared = make_music(n, rng)
    remixed = remixed_bed(n, 500, shared)
    voice_a, voice_b = make_voice(n, 143, 0.0), make_voice(n, 187, 0.16)

    # Les 40 premières secondes partagent le lit musical, le reste est remixé.
    cut = 40 * SAMPLE_RATE
    bed_b = np.concatenate([shared[:cut], remixed[cut:]])
    a = write_wav(tmp_path / "a.wav", np.column_stack([shared + voice_a] * 2))
    b = write_wav(tmp_path / "b.wav", np.column_stack([bed_b + voice_b] * 2))

    report = run_scout(a, b, window_s=10.0)
    assert report.windows, "aucune fenêtre analysée"
    early = [w.coherence for w in report.windows if w.end_s <= 40]
    late = [w.coherence for w in report.windows if w.start_s >= 50]
    assert early and late
    assert np.median(early) > 0.5, np.median(early)
    assert np.median(late) < 0.2, np.median(late)
    # La plage partagée doit ressortir dans le relevé.
    ranges = report.ranges()
    assert ranges, "aucune plage exploitable repérée"
    assert ranges[0][0] < 5 and ranges[0][1] >= 30, ranges
    assert report.payload["operation"] == "scout"
    assert report.payload["usable_ranges"]


def test_scout_writes_no_audio_and_leaves_inputs_untouched(scene, tmp_path):
    import hashlib

    from ummahverse_audio.scout import run_scout

    before = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in (scene.a, scene.b)}
    inventory = sorted(item.name for item in scene.a.parent.iterdir())
    run_scout(scene.a, scene.b, window_s=2.0)
    for path, digest in before.items():
        assert hashlib.sha256(path.read_bytes()).hexdigest() == digest
    assert sorted(item.name for item in scene.a.parent.iterdir()) == inventory
