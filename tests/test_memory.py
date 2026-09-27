"""Mémoire bornée : la libération de pages ne doit jamais perdre de données.

Le pipeline rend au système les pages des PCM intermédiaires déjà consommées, sans
quoi le pic de mémoire résidente suit la durée du film. Ces tests vérifient que
l'opération est **sans effet sur le contenu** — y compris sur une cartographie en
écriture — et qu'elle reste inoffensive là où l'avis n'existe pas (Windows).
"""

from __future__ import annotations

import numpy as np

from ummahverse_audio.audio_io import Workspace, load_input, release_pages

from .conftest import SAMPLE_RATE, build_scene


def test_release_pages_preserves_written_content():
    """Une cartographie partagée en écriture garde ses valeurs après libération."""
    with Workspace() as workspace:
        frames, channels = 300_000, 2
        array = workspace.memmap("essai.acc", (frames, channels))
        pattern = np.linspace(-2.0, 2.0, frames * channels,
                              dtype=np.float32).reshape(frames, channels)
        array[:] = pattern
        release_pages(array, 0, frames * channels * 4)
        assert np.array_equal(np.asarray(array), pattern)
        # Écrire encore après libération doit rester possible.
        array[10:20] = 0.5
        release_pages(array, 0, frames * channels * 4)
        assert np.allclose(np.asarray(array[10:20]), 0.5)
        assert np.array_equal(np.asarray(array[30:40]), pattern[30:40])


def test_release_pages_is_harmless_on_plain_arrays():
    """Sans `_mmap` (ou sans MADV_DONTNEED), l'appel ne fait simplement rien."""
    values = np.arange(1024, dtype=np.float32)
    release_pages(values, 0, values.nbytes)
    assert np.array_equal(values, np.arange(1024, dtype=np.float32))


def test_release_pages_ignores_out_of_range_requests():
    with Workspace() as workspace:
        array = workspace.memmap("court.acc", (1024, 1))
        array[:] = 1.0
        release_pages(array, 0, 10 * array.nbytes)       # au-delà de la fin
        release_pages(array, array.nbytes, array.nbytes)  # longueur nulle
        release_pages(array, -50, -10)                    # bornes négatives
        assert np.allclose(np.asarray(array), 1.0)


def test_decoded_audio_read_is_unaffected_by_release(tmp_path):
    """Relire une zone libérée doit redonner exactement les mêmes échantillons."""
    scene = build_scene(tmp_path / "src", seconds=4.0)
    with Workspace(tmp_path) as workspace:
        audio = load_input(scene.a, workspace, "a")
        before = audio.read(SAMPLE_RATE, SAMPLE_RATE).copy()
        audio.release_all()
        assert np.array_equal(audio.read(SAMPLE_RATE, SAMPLE_RATE), before)
        audio.release_until(2 * SAMPLE_RATE)
        assert np.array_equal(audio.read(SAMPLE_RATE, SAMPLE_RATE), before)


def test_analysis_track_is_chunked_but_equals_the_direct_computation(tmp_path):
    """Le découpage en tranches avec chevauchement ne doit pas changer la piste."""
    from scipy.signal import decimate

    scene = build_scene(tmp_path / "src", seconds=25.0)
    with Workspace(tmp_path) as workspace:
        audio = load_input(scene.a, workspace, "a")
        track, rate = audio.analysis_track()
        whole = np.asarray(audio.data, dtype=np.float64).mean(axis=1)
    factor = SAMPLE_RATE // rate
    reference = decimate(whole, factor, ftype="fir", zero_phase=True)
    length = min(track.size, reference.size)
    assert abs(track.size - reference.size) <= 2, (track.size, reference.size)
    # Les tranches introduisent une erreur de filtrage négligeable devant le signal.
    error = float(np.abs(track[:length] - reference[:length]).max())
    assert error < 1e-3 * float(np.abs(reference).max()), error
