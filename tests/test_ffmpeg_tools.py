"""FFmpeg : découverte, inspection des flux, décodage, écriture, erreurs explicites."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from ummahverse_audio.errors import (FfmpegFailedError, FfmpegMissingError,
                                     StreamSelectionError)
from ummahverse_audio.ffmpeg_tools import (ENV_FFMPEG, WavWriter, decode_to_raw,
                                           find_ffmpeg, find_ffprobe,
                                           probe_audio_streams, select_stream)

from .conftest import SAMPLE_RATE, write_wav


def test_binaries_are_found():
    assert find_ffmpeg().exists()
    assert find_ffprobe().exists()


def test_env_override_pointing_nowhere_is_explicit(monkeypatch, tmp_path):
    monkeypatch.setenv(ENV_FFMPEG, str(tmp_path / "absent"))
    with pytest.raises(FfmpegMissingError) as excinfo:
        find_ffmpeg()
    message = excinfo.value.user_message()
    assert ENV_FFMPEG in message
    assert "apt install ffmpeg" in message


def test_probe_reports_rate_channels_and_layout(scene):
    streams = probe_audio_streams(scene.a)
    assert len(streams) == 1
    stream = streams[0]
    assert stream.sample_rate == SAMPLE_RATE
    assert stream.channels == 2
    assert stream.lossless
    # Un WAV nu ne déclare pas d'agencement ; la stéréo est déduite sans ambiguïté.
    assert stream.effective_layout == "stereo"


def test_probe_missing_file_is_explicit(tmp_path):
    with pytest.raises(FfmpegFailedError):
        probe_audio_streams(tmp_path / "absent.wav")


def test_unicode_and_spaces_in_paths(tmp_path):
    directory = tmp_path / "dossier accentué éàü 日本"
    path = write_wav(directory / "piste de test ré.wav",
                     np.zeros((SAMPLE_RATE, 2), dtype=np.float32))
    streams = probe_audio_streams(path)
    assert streams[0].channels == 2


def test_select_stream_refuses_ambiguity(tmp_path, scene):
    """Deux flux audio sans choix explicite : refus, pas de choix silencieux."""
    import subprocess

    merged = tmp_path / "deux_flux.mka"
    subprocess.run([str(find_ffmpeg()), "-hide_banner", "-v", "error", "-y",
                    "-i", str(scene.a), "-i", str(scene.b),
                    "-map", "0:a", "-map", "1:a", "-c", "copy", str(merged)],
                   check=True)
    streams = probe_audio_streams(merged)
    assert len(streams) == 2
    with pytest.raises(StreamSelectionError) as excinfo:
        select_stream(merged, streams, None)
    assert "explicite" in str(excinfo.value)
    assert select_stream(merged, streams, 1).audio_index == 1
    with pytest.raises(StreamSelectionError):
        select_stream(merged, streams, 7)


def test_decode_preserves_samples_exactly(tmp_path, scene):
    """Le décodage d'un WAV float32 est sans perte : aucune altération de niveau."""
    from scipy.io import wavfile

    rate, original = wavfile.read(scene.a)
    stream = probe_audio_streams(scene.a)[0]
    raw = tmp_path / "decoded.f32"
    frames = decode_to_raw(scene.a, stream, rate, raw)
    assert frames == original.shape[0]
    decoded = np.memmap(raw, dtype=np.float32, mode="r", shape=(frames, 2))
    assert np.array_equal(np.asarray(decoded), original.astype(np.float32))


def test_wav_writer_round_trip_without_clipping(tmp_path):
    """Les échantillons hors ±1 passent intacts en WAV flottant."""
    from scipy.io import wavfile

    data = np.array([[1.7, -2.3], [0.5, 0.25]], dtype=np.float32)
    destination = tmp_path / "hors_plage.wav"
    with WavWriter(destination, SAMPLE_RATE, 2, "stereo") as writer:
        writer.write(data)
    _, read_back = wavfile.read(destination)
    assert np.allclose(read_back, data)
    assert float(np.abs(read_back).max()) > 1.0


def test_wav_writer_abort_removes_partial_file(tmp_path):
    destination = tmp_path / "partiel.wav"
    writer = WavWriter(destination, SAMPLE_RATE, 1, "mono")
    writer.write(np.zeros((128, 1), dtype=np.float32))
    writer.abort()
    assert not destination.exists()


def test_multichannel_layout_is_written_back(tmp_path):
    """Un 5.1 conserve son nombre de canaux et son agencement déclaré."""
    data = np.tile(np.linspace(-0.5, 0.5, 4800, dtype=np.float32)[:, None], (1, 6))
    destination = tmp_path / "surround.wav"
    with WavWriter(destination, SAMPLE_RATE, 6, "5.1") as writer:
        writer.write(data)
    stream = probe_audio_streams(destination)[0]
    assert stream.channels == 6
    assert stream.channel_layout.startswith("5.1")
