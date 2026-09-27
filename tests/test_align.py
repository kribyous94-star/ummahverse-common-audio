"""Alignement : signe, précision sous-échantillon, dérive, coupes, seuils de confiance."""

from __future__ import annotations

import numpy as np
import pytest

from ummahverse_audio.align import (build_offset_map, estimate_global_delay, gcc_phat)
from ummahverse_audio.audio_io import Workspace, load_input
from ummahverse_audio.blocks import BlockPlan
from ummahverse_audio.errors import AlignmentError
from ummahverse_audio.stft import StftConfig

from .conftest import SAMPLE_RATE, build_scene, make_music, make_voice, shift, write_wav


def test_delay_sign_convention():
    """`delay > 0` signifie que y est en retard sur x."""
    rng = np.random.default_rng(3)
    x = rng.normal(size=20_000)
    y = np.zeros_like(x)
    y[700:] = x[:-700]
    estimate = gcc_phat(x, y, max_shift=2000)
    assert estimate.integer_delay == 700
    assert estimate.confidence > 0.9
    # Symétrie : en inversant les rôles, le signe s'inverse.
    assert gcc_phat(y, x, max_shift=2000).integer_delay == -700


def test_subsample_delay_precision():
    """Un retard fractionnaire est retrouvé à mieux qu'un dixième d'échantillon."""
    n = 32_768
    rng = np.random.default_rng(11)
    spectrum = np.fft.rfft(rng.normal(size=n))
    k = np.arange(spectrum.size)
    for true_delay in (12.25, 40.5, 103.75):
        shifted = np.fft.irfft(spectrum * np.exp(-2j * np.pi * k * true_delay / n), n)
        estimate = gcc_phat(np.fft.irfft(spectrum, n), shifted, max_shift=500)
        assert abs(estimate.delay - true_delay) < 0.1, (true_delay, estimate.delay)


@pytest.mark.parametrize("case", ["silence", "bruits indépendants"])
def test_uncorrelated_signals_are_rejected(case):
    """Deux pistes sans contenu commun doivent être refusées, pas alignées au hasard."""
    rng = np.random.default_rng(5)
    left = np.zeros(40_000) if case == "silence" else rng.normal(size=40_000)
    with pytest.raises(AlignmentError):
        estimate_global_delay(left, rng.normal(size=40_000), 4_000)


def test_global_delay_on_real_scene(scene, tmp_path):
    with Workspace(tmp_path) as workspace:
        audio_a = load_input(scene.a, workspace, "a")
        audio_b = load_input(scene.b, workspace, "b")
        track_a, rate = audio_a.analysis_track()
        track_b, _ = audio_b.analysis_track()
        estimate = estimate_global_delay(track_a, track_b, rate)
        measured = estimate.delay * audio_a.sample_rate / rate
    assert abs(measured - scene.offset_b) < 0.02 * SAMPLE_RATE
    assert estimate.confidence > 0.2


def test_offset_map_flags_an_edit_difference(tmp_path):
    """Un plan présent dans A et absent de B doit être marqué non comparable."""
    rng = np.random.default_rng(17)
    seconds = 18.0
    n = int(seconds * SAMPLE_RATE)
    music = make_music(n, rng)
    voice_a = make_voice(n, 143, 0.0)
    voice_b = make_voice(n, 187, 0.16)
    # Dans B, une fenêtre de 4 s est remplacée par un contenu sans rien de commun
    # (un plan absent d'une version). Du bruit filtré, et non une autre musique de
    # synthèse : deux musiques du même générateur partagent leurs partiels et
    # resteraient corrélées.
    from scipy.signal import butter, sosfilt

    other = sosfilt(butter(3, [200, 6000], btype="bandpass", fs=SAMPLE_RATE, output="sos"),
                    np.random.default_rng(99).normal(size=n))
    other *= 0.1 / max(float(np.std(other)), 1e-12)
    common_b = music.copy()
    cut = slice(int(7 * SAMPLE_RATE), int(11 * SAMPLE_RATE))
    common_b[cut] = other[cut]
    directory = tmp_path / "coupe"
    write_wav(directory / "a.wav", np.column_stack([music + voice_a] * 2))
    write_wav(directory / "b.wav", np.column_stack([0.9 * (common_b + voice_b)] * 2))

    config = StftConfig(sample_rate=SAMPLE_RATE)
    plan = BlockPlan.for_config(config, 3.0)
    with Workspace(tmp_path) as workspace:
        audio_a = load_input(directory / "a.wav", workspace, "a")
        audio_b = load_input(directory / "b.wav", workspace, "b")
        track_a, rate = audio_a.analysis_track()
        track_b, _ = audio_b.analysis_track()
        from ummahverse_audio.align import DelayEstimate

        global_delay = estimate_global_delay(track_a, track_b, rate)
        scaled = DelayEstimate(global_delay.delay * SAMPLE_RATE / rate,
                               int(round(global_delay.integer_delay * SAMPLE_RATE / rate)),
                               global_delay.confidence, global_delay.peak_ratio)
        offset_map = build_offset_map(audio_a, audio_b, plan.advance, scaled,
                                      tracks=(track_a, track_b, rate))

    times = offset_map.starts / SAMPLE_RATE
    inside = (times >= 7.5) & (times <= 10.5)
    outside = (times < 6.0) | (times > 12.0)
    assert offset_map.confidences[inside].mean() < offset_map.confidences[outside].mean()
    assert not offset_map.comparable[inside].all(), "la zone remplacée doit être écartée"
    assert offset_map.comparable[outside].mean() > 0.8


def test_offset_map_reports_clock_drift(tmp_path):
    """Un rééchantillonnage léger de B doit apparaître comme une dérive en ppm."""
    rng = np.random.default_rng(23)
    seconds = 20.0
    n = int(seconds * SAMPLE_RATE)
    music = make_music(n, rng)
    # B lit la même musique 200 ppm plus lentement : le décalage croît avec le temps.
    drift = 200e-6
    positions = np.arange(n) * (1.0 - drift)
    stretched = np.interp(positions, np.arange(n), music)
    directory = tmp_path / "derive"
    write_wav(directory / "a.wav", np.column_stack([music + make_voice(n, 143, 0.0)] * 2))
    write_wav(directory / "b.wav",
              np.column_stack([stretched + make_voice(n, 187, 0.16)] * 2))

    config = StftConfig(sample_rate=SAMPLE_RATE)
    plan = BlockPlan.for_config(config, 3.0)
    with Workspace(tmp_path) as workspace:
        audio_a = load_input(directory / "a.wav", workspace, "a")
        audio_b = load_input(directory / "b.wav", workspace, "b")
        track_a, rate = audio_a.analysis_track()
        track_b, _ = audio_b.analysis_track()
        from ummahverse_audio.align import DelayEstimate

        global_delay = estimate_global_delay(track_a, track_b, rate)
        scaled = DelayEstimate(global_delay.delay * SAMPLE_RATE / rate,
                               int(round(global_delay.integer_delay * SAMPLE_RATE / rate)),
                               global_delay.confidence, global_delay.peak_ratio)
        offset_map = build_offset_map(audio_a, audio_b, plan.advance, scaled,
                                      tracks=(track_a, track_b, rate))
    # B lit la musique plus lentement : son retard **croît** avec le temps, la dérive
    # est donc positive. Un signe inversé ici passait inaperçu jusqu'à ce que
    # l'affinage local soit corrigé.
    assert 120 < offset_map.drift_ppm < 300, offset_map.drift_ppm
    assert any("érive" in note for note in offset_map.notes)


def test_offset_map_segments_are_consistent(scene, tmp_path):
    config = StftConfig(sample_rate=SAMPLE_RATE)
    plan = BlockPlan.for_config(config, 2.0)
    with Workspace(tmp_path) as workspace:
        audio_a = load_input(scene.a, workspace, "a")
        audio_b = load_input(scene.b, workspace, "b")
        track_a, rate = audio_a.analysis_track()
        track_b, _ = audio_b.analysis_track()
        from ummahverse_audio.align import DelayEstimate

        global_delay = estimate_global_delay(track_a, track_b, rate)
        scaled = DelayEstimate(global_delay.delay * SAMPLE_RATE / rate,
                               int(round(global_delay.integer_delay * SAMPLE_RATE / rate)),
                               global_delay.confidence, global_delay.peak_ratio)
        offset_map = build_offset_map(audio_a, audio_b, plan.advance, scaled,
                                      tracks=(track_a, track_b, rate))
    comparable = offset_map.segments(SAMPLE_RATE, comparable=True)
    assert comparable, "le jeu de référence doit être entièrement comparable"
    assert abs(comparable[0]["mean_delay_s"] - scene.offset_b / SAMPLE_RATE) < 0.005
    assert offset_map.comparable_ratio > 0.9
    # Les décalages locaux retrouvent le décalage vrai à mieux qu'un échantillon.
    errors = np.abs(offset_map.delays[offset_map.comparable] - scene.offset_b)
    assert float(np.median(errors)) < 1.0, float(np.median(errors))


def test_block_plan_weights_sum_to_one():
    """Le fondu entre blocs doit rendre une somme de poids exactement égale à 1."""
    config = StftConfig(sample_rate=SAMPLE_RATE)
    plan = BlockPlan.for_config(config, 3.0)
    total = int(11.3 * SAMPLE_RATE)
    accumulator = np.zeros(total + plan.block_frames)
    for start in plan.starts(total):
        weights = plan.weights(int(start), total)
        accumulator[int(start):int(start) + plan.block_frames] += weights
    assert np.allclose(accumulator[:total], 1.0), accumulator[:total].min()
