"""Séparation quand les deux versions n'ont plus de relation de phase.

C'est le cas courant sur du vrai matériel : deux versions linguistiques issues de
mixages différents, ou une piste ré-étirée pour être recalée. La cohérence tombe alors
à zéro et l'estimateur de Wiener ne trouve rien — alors que les spectrogrammes en
**magnitude** restent très semblables : mesuré 0,79 (0,88 en log) sur un film réel,
contre 0,83 à 0,96 pour un master réellement partagé.

La méthode magnitude exploite cela. Elle ne suppose aucune relation de phase : elle
compare des puissances, et s'appuie sur le fait que les dialogues de deux langues
n'occupent pas les mêmes cases temps-fréquence. Sa limite est connue et mesurée ici :
là où les deux versions parlent en même temps, une part du dialogue fuit dans le
commun.
"""

from __future__ import annotations

import numpy as np
import pytest
from scipy.signal import ShortTimeFFT, get_window

from ummahverse_audio.extract import (AUTO_COHERENCE_THRESHOLD, ExtractOptions,
                                      run_extract)
from ummahverse_audio.metrics import decompose, read_audio, si_sdr

from .conftest import SAMPLE_RATE, make_music, make_voice, write_wav


def scramble_phase(signal: np.ndarray, seed: int = 1) -> np.ndarray:
    """Conserve le spectrogramme en magnitude, détruit la phase.

    C'est le pire cas réaliste : un ré-étirement temporel ou un remixage laisse les
    magnitudes presque intactes et anéantit la relation de phase.
    """
    transform = ShortTimeFFT(get_window("hann", 4096, fftbins=True), hop=1024,
                             fs=SAMPLE_RATE, mfft=4096, fft_mode="onesided")
    spectrum = transform.stft(signal)
    rng = np.random.default_rng(seed)
    rotated = np.abs(spectrum) * np.exp(1j * rng.uniform(0, 2 * np.pi, spectrum.shape))
    return transform.istft(rotated, k0=0, k1=signal.size)


@pytest.fixture(scope="module")
def phase_broken_scene(tmp_path_factory):
    """Même fond, phase détruite côté B, deux dialogues différents."""
    directory = tmp_path_factory.mktemp("phase_broken")
    rng = np.random.default_rng(5)
    n = 20 * SAMPLE_RATE
    music = make_music(n, rng)
    voice_a, voice_b = make_voice(n, 143, 0.0), make_voice(n, 187, 0.16)
    a = write_wav(directory / "a.wav", np.column_stack([music + voice_a] * 2))
    b = write_wav(directory / "b.wav",
                  np.column_stack([0.9 * scramble_phase(music) + voice_b] * 2))
    return {"a": a, "b": b, "music": np.column_stack([music] * 2),
            "voice_a": np.column_stack([voice_a] * 2),
            "voice_b": np.column_stack([voice_b] * 2), "frames": n}


def sources_of(scene) -> dict:
    return {"commun": scene["music"], "voix_A": scene["voice_a"],
            "voix_B": scene["voice_b"]}


def test_phase_is_really_destroyed_but_magnitudes_survive(phase_broken_scene):
    """Le jeu doit bien reproduire la situation réelle, sinon il ne teste rien."""
    a, _ = read_audio(phase_broken_scene["a"])
    b, _ = read_audio(phase_broken_scene["b"])
    transform = ShortTimeFFT(get_window("hann", 4096, fftbins=True), hop=1024,
                             fs=SAMPLE_RATE, mfft=4096, fft_mode="onesided")
    spec_a = np.abs(transform.stft(a[:, 0].astype(np.float64)))
    spec_b = np.abs(transform.stft(b[:, 0].astype(np.float64)))
    frames = min(spec_a.shape[1], spec_b.shape[1])
    flat_a = spec_a[:, :frames].ravel()
    flat_b = spec_b[:, :frames].ravel()
    magnitude = float(np.corrcoef(flat_a, flat_b)[0, 1])
    waveform = float(np.corrcoef(a[:, 0], b[:len(a), 0])[0, 1])
    # Mesuré : 0,56 en magnitude contre 0,00 en forme d'onde. L'écart entre les deux
    # est ce qui rend la méthode magnitude possible là où Wiener échoue.
    assert magnitude > 0.45, magnitude
    assert abs(waveform) < 0.1, waveform
    assert magnitude > abs(waveform) * 5


def test_magnitude_method_recovers_the_common_bed(phase_broken_scene, tmp_path):
    result = run_extract(ExtractOptions(
        input_a=phase_broken_scene["a"], input_b=phase_broken_scene["b"],
        out_dir=tmp_path / "out", method="magnitude"))
    common, _ = read_audio(result.common_path)
    residual, _ = read_audio(result.a_only_path)
    span = slice(SAMPLE_RATE, phase_broken_scene["frames"] - SAMPLE_RATE)
    sources = sources_of(phase_broken_scene)

    in_common = decompose(common, sources, span)
    in_residual = decompose(residual, sources, span)
    # Le fond commun est retrouvé...
    assert in_common["commun_db"] > -4.0, in_common
    assert si_sdr(common, phase_broken_scene["music"], span) > 5.0
    # ...et retiré du résidu, alors qu'aucune phase n'est partagée.
    assert in_residual["commun_db"] < -8.0, in_residual
    # La voix survit dans le résidu.
    assert in_residual["voix_A_db"] > -6.0, in_residual
    # Limite assumée : une part du dialogue fuit dans le commun.
    assert in_common["voix_A_db"] < -5.0, in_common


def test_wiener_fails_where_magnitude_succeeds(phase_broken_scene, tmp_path):
    """Sans relation de phase, l'estimateur de Wiener ne peut rien retrouver."""
    sources = sources_of(phase_broken_scene)
    span = slice(SAMPLE_RATE, phase_broken_scene["frames"] - SAMPLE_RATE)
    scores = {}
    for method in ("wiener", "magnitude"):
        result = run_extract(ExtractOptions(
            input_a=phase_broken_scene["a"], input_b=phase_broken_scene["b"],
            out_dir=tmp_path / method, method=method))
        residual, _ = read_audio(result.a_only_path)
        scores[method] = decompose(residual, sources, span)["commun_db"]
    # La musique est bien mieux retirée du résidu en magnitude.
    assert scores["magnitude"] < scores["wiener"] - 5.0, scores


def test_auto_selects_magnitude_when_phase_is_lost(phase_broken_scene, tmp_path):
    result = run_extract(ExtractOptions(
        input_a=phase_broken_scene["a"], input_b=phase_broken_scene["b"],
        out_dir=tmp_path / "auto", method="auto"))
    assert result.metadata["separation"]["method"] == "magnitude"
    assert any("magnitude" in warning for warning in result.warnings)
    coherence = result.metadata["transfer"]["energy_weighted_coherence_debiased"]
    assert coherence < AUTO_COHERENCE_THRESHOLD, coherence


def test_auto_keeps_wiener_when_phase_is_shared(scene, tmp_path):
    """Sur un master réellement partagé, « auto » ne doit pas dégrader le résultat."""
    result = run_extract(ExtractOptions(input_a=scene.a, input_b=scene.b,
                                        out_dir=tmp_path / "out", method="auto"))
    assert result.metadata["separation"]["method"] == "wiener"
    common, _ = read_audio(result.common_path)
    span = slice(SAMPLE_RATE, scene.common.shape[0] - SAMPLE_RATE)
    assert si_sdr(common, scene.common, span) > 12.0


def test_oversubtraction_trades_background_against_voice(phase_broken_scene, tmp_path):
    """Le réglage doit faire ce qu'il annonce, dans les deux sens."""
    sources = sources_of(phase_broken_scene)
    span = slice(SAMPLE_RATE, phase_broken_scene["frames"] - SAMPLE_RATE)
    measured = {}
    for factor in (0.7, 1.4):
        result = run_extract(ExtractOptions(
            input_a=phase_broken_scene["a"], input_b=phase_broken_scene["b"],
            out_dir=tmp_path / f"o{factor}", method="magnitude",
            magnitude_oversubtraction=factor))
        residual, _ = read_audio(result.a_only_path)
        measured[factor] = decompose(residual, sources, span)
    # Retirer davantage : moins de fond dans le résidu...
    assert measured[1.4]["commun_db"] < measured[0.7]["commun_db"]
    # ...mais la voix en pâtit.
    assert measured[1.4]["voix_A_db"] < measured[0.7]["voix_A_db"]
