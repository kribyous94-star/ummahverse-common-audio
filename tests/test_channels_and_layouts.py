"""Politique des canaux : jamais de downmix implicite, refus explicite si incompatible."""

from __future__ import annotations

import subprocess

import numpy as np
import pytest

from ummahverse_audio.audio_io import check_layout_compatibility
from ummahverse_audio.errors import IncompatibleLayoutError
from ummahverse_audio.extract import ExtractOptions, run_extract
from ummahverse_audio.ffmpeg_tools import (AudioStreamInfo, find_ffmpeg,
                                           probe_audio_streams)
from ummahverse_audio.metrics import read_audio, si_sdr

from .conftest import SAMPLE_RATE, build_scene, make_music, make_voice, shift, write_wav


def declare_layout(source, destination, layout: str):
    """Réécrit un WAV en déclarant explicitement un agencement de canaux."""
    subprocess.run([str(find_ffmpeg()), "-hide_banner", "-v", "error", "-y",
                    "-i", str(source), "-channel_layout", layout,
                    "-c:a", "pcm_f32le", str(destination)], check=True)
    return destination


def test_stereo_against_surround_is_refused(tmp_path):
    scene = build_scene(tmp_path / "stereo", seconds=3.0, channels=2)
    surround = build_scene(tmp_path / "surround", seconds=3.0, channels=6)
    with pytest.raises(IncompatibleLayoutError) as excinfo:
        run_extract(ExtractOptions(input_a=scene.a, input_b=surround.b,
                                   out_dir=tmp_path / "out"))
    message = excinfo.value.user_message()
    assert "downmix" in message
    assert "5.1" in message or "canaux" in message


def test_mono_against_stereo_is_refused(tmp_path):
    mono = build_scene(tmp_path / "mono", seconds=3.0, channels=1)
    stereo = build_scene(tmp_path / "stereo", seconds=3.0, channels=2)
    with pytest.raises(IncompatibleLayoutError):
        run_extract(ExtractOptions(input_a=mono.a, input_b=stereo.b,
                                   out_dir=tmp_path / "out"))


def test_unknown_multichannel_layout_requires_explicit_flag(tmp_path):
    """Un fichier à 6 canaux sans agencement déclaré : choix explicite exigé."""
    scene = build_scene(tmp_path / "six", seconds=3.0, channels=6)
    streams = probe_audio_streams(scene.a)
    assert streams[0].channels == 6
    assert streams[0].effective_layout == "inconnu", streams[0].channel_layout

    with pytest.raises(IncompatibleLayoutError) as excinfo:
        run_extract(ExtractOptions(input_a=scene.a, input_b=scene.b,
                                   out_dir=tmp_path / "refus"))
    assert "--allow-unknown-layout" in excinfo.value.user_message()

    result = run_extract(ExtractOptions(input_a=scene.a, input_b=scene.b,
                                       out_dir=tmp_path / "accepte",
                                       allow_unknown_layout=True))
    assert any("ordre du fichier" in warning for warning in result.warnings)
    common, _ = read_audio(result.common_path)
    assert common.shape[1] == 6


def test_declared_surround_pair_is_processed_channel_by_channel(tmp_path):
    """Un vrai 5.1 : 6 canaux en sortie, dans le même ordre, sans mélange."""
    scene = build_scene(tmp_path / "src", seconds=4.0, channels=6)
    a = declare_layout(scene.a, tmp_path / "a_51.wav", "5.1")
    b = declare_layout(scene.b, tmp_path / "b_51.wav", "5.1")
    result = run_extract(ExtractOptions(input_a=a, input_b=b, out_dir=tmp_path / "out"))

    common, _ = read_audio(result.common_path)
    assert common.shape[1] == 6
    assert probe_audio_streams(result.common_path)[0].channel_layout.startswith("5.1")
    # Les gains par canal du jeu (1.0 → 0.9) doivent être conservés, canal par canal.
    span = slice(SAMPLE_RATE, common.shape[0] - SAMPLE_RATE)
    levels = [float(np.sqrt(np.mean(common[span, channel] ** 2))) for channel in range(6)]
    expected = np.linspace(1.0, 0.9, 6)
    ratios = np.array(levels) / levels[0]
    assert np.allclose(ratios, expected / expected[0], atol=0.05), ratios


def stream_info(channels: int, layout: str, codec: str = "pcm_f32le",
                sample_rate: int = SAMPLE_RATE) -> AudioStreamInfo:
    return AudioStreamInfo(audio_index=0, stream_index=0, codec=codec,
                           sample_rate=sample_rate, channels=channels,
                           channel_layout=layout, sample_format="flt",
                           duration_s=10.0, language="", title="")


def test_same_channel_count_different_declared_layout_warns():
    """Même nombre de canaux, agencements déclarés différents : avertir, pas refuser."""
    warnings = check_layout_compatibility(stream_info(6, "5.1"), stream_info(6, "5.1(side)"))
    assert any("Agencements déclarés différents" in warning for warning in warnings)
    assert any("ordre du fichier" in warning for warning in warnings)


def test_layout_policy_matrix():
    """Résumé de la politique : ce qui passe, ce qui avertit, ce qui est refusé."""
    # Mono et stéréo sans agencement déclaré : convention non ambiguë, aucun avertissement.
    assert check_layout_compatibility(stream_info(2, "inconnu"),
                                      stream_info(2, "inconnu")) == []
    assert check_layout_compatibility(stream_info(1, "inconnu"),
                                      stream_info(1, "inconnu")) == []
    # Au-delà de la stéréo sans agencement : refus sauf autorisation explicite.
    with pytest.raises(IncompatibleLayoutError):
        check_layout_compatibility(stream_info(6, "inconnu"), stream_info(6, "inconnu"))
    assert check_layout_compatibility(stream_info(6, "inconnu"), stream_info(6, "inconnu"),
                                      allow_unknown_layout=True)
    # Nombres de canaux différents : toujours un refus, jamais un downmix.
    with pytest.raises(IncompatibleLayoutError):
        check_layout_compatibility(stream_info(6, "5.1"), stream_info(2, "stereo"))


def test_lossy_codec_is_flagged(tmp_path):
    """Un codec avec perte doit être signalé : ses artefacts diffèrent par langue."""
    scene = build_scene(tmp_path / "src", seconds=3.0, channels=2)
    lossy = tmp_path / "b.mp3"
    subprocess.run([str(find_ffmpeg()), "-hide_banner", "-v", "error", "-y",
                    "-i", str(scene.b), "-c:a", "libmp3lame", "-b:a", "192k", str(lossy)],
                   check=True)
    result = run_extract(ExtractOptions(input_a=scene.a, input_b=lossy,
                                       out_dir=tmp_path / "out"))
    assert any("avec perte" in warning for warning in result.warnings), result.warnings


def test_different_sample_rates_are_resampled_and_flagged(tmp_path):
    """Deux fréquences : conversion explicite vers la plus élevée, signalée."""
    scene = build_scene(tmp_path / "src", seconds=4.0, channels=2)
    downsampled = tmp_path / "b_44k.wav"
    subprocess.run([str(find_ffmpeg()), "-hide_banner", "-v", "error", "-y",
                    "-i", str(scene.b), "-af", "aresample=osr=44100:resampler=soxr",
                    "-c:a", "pcm_f32le", str(downsampled)], check=True)
    result = run_extract(ExtractOptions(input_a=scene.a, input_b=downsampled,
                                       out_dir=tmp_path / "out"))
    assert any("Fréquences d'échantillonnage différentes" in warning
               for warning in result.warnings)
    assert result.metadata["outputs"]["sample_rate"] == SAMPLE_RATE
    common, rate = read_audio(result.common_path)
    assert rate == SAMPLE_RATE
    span = slice(SAMPLE_RATE, common.shape[0] - SAMPLE_RATE)
    assert si_sdr(common, scene.common, span) > 6.0


def test_no_loudness_normalisation_before_comparison(tmp_path):
    """Un niveau global différent doit être mesuré comme un gain, pas normalisé à part."""
    rng = np.random.default_rng(53)
    n = 5 * SAMPLE_RATE
    music = make_music(n, rng)
    common = np.column_stack([music, 0.9 * music])
    a = write_wav(tmp_path / "a.wav", common + np.column_stack([make_voice(n, 143, 0.0)] * 2))
    quiet_gain = 0.4
    b = write_wav(tmp_path / "b.wav",
                  quiet_gain * shift(common, 480)
                  + np.column_stack([make_voice(n, 187, 0.16)] * 2))
    result = run_extract(ExtractOptions(input_a=a, input_b=b, out_dir=tmp_path / "out"))
    gains = result.metadata["transfer"]["broadband_gain_per_channel"]
    for gain in gains:
        assert abs(gain - quiet_gain) < 0.05, gains
    common_out, _ = read_audio(result.common_path)
    span = slice(SAMPLE_RATE, n - SAMPLE_RATE)
    # Le commun est rendu au niveau de A, pas à un niveau moyen entre A et B.
    assert si_sdr(common_out, common, span) > 8.0
