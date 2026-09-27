"""Quantification sur sources connues : musique sous voix, bruitages sous voix,
voix résiduelle, reconstruction, et comparaison à des baselines naïves.

Les seuils sont volontairement **lâches** : ils protègent contre une régression, ils
ne prétendent pas définir une qualité cinéma. Les valeurs mesurées de référence sont
publiées dans `docs/results.md`.
"""

from __future__ import annotations

import numpy as np
import pytest

from ummahverse_audio.extract import ExtractOptions, run_extract
from ummahverse_audio.metrics import EvaluationWindow, decompose, read_audio, si_sdr

from .conftest import SAMPLE_RATE, build_scene


def sources_in_a(scene) -> dict:
    return {"commun": scene.common, "voix_A": scene.voice_a, "voix_B": scene.voice_b}


def sources_in_b(scene) -> dict:
    return {"commun": scene.common_in_b(), "voix_B": scene.voice_b, "voix_A": scene.voice_a}


def window_for(scene) -> slice:
    return EvaluationWindow(sample_rate=scene.sample_rate).slice_for(scene.common.shape[0])


def test_common_is_dominated_by_the_true_common(scene, extracted):
    """Le commun estimé doit ressembler au commun vrai, pas aux voix."""
    common, rate = read_audio(extracted.common_path)
    assert rate == scene.sample_rate
    span = window_for(scene)
    parts = decompose(common, sources_in_a(scene), span)
    assert si_sdr(common, scene.common, span) > 12.0
    assert parts["commun_db"] > -1.5, "le commun vrai doit être restitué à son niveau"
    # Fuite de voix : très inférieure au niveau de la voix d'origine.
    assert parts["voix_A_db"] < -18.0
    assert parts["voix_B_db"] < -18.0


def test_residuals_keep_their_voice_and_lose_the_common(scene, extracted):
    """`a_only` doit garder la voix de A et perdre la musique et les bruitages."""
    a_only, _ = read_audio(extracted.a_only_path)
    b_only, _ = read_audio(extracted.b_only_path)
    span = window_for(scene)

    parts_a = decompose(a_only, sources_in_a(scene), span)
    assert parts_a["voix_A_db"] > -1.5, "la voix de A ne doit pas être effacée"
    assert parts_a["commun_db"] < -15.0, "la musique doit être nettement annulée"
    assert si_sdr(a_only, scene.voice_a, span) > 8.0

    parts_b = decompose(b_only, sources_in_b(scene), span)
    assert parts_b["voix_B_db"] > -1.5
    assert parts_b["commun_db"] < -15.0
    assert si_sdr(b_only, scene.voice_b, span) > 8.0


def test_music_under_voice_is_cancelled_during_speech(scene, extracted):
    """Le point clé : l'annulation doit aussi fonctionner **pendant** la parole.

    Un simple détecteur de passages parlés / non parlés échouerait ici : la mesure
    est restreinte aux trames où la voix de A est active.
    """
    a_only, _ = read_audio(extracted.a_only_path)
    envelope = np.abs(scene.voice_a).mean(axis=1)
    smooth = np.convolve(envelope, np.ones(2048) / 2048, mode="same")
    speaking = smooth > 0.6 * float(np.quantile(smooth, 0.9))
    span = window_for(scene)
    mask = np.zeros(scene.common.shape[0], dtype=bool)
    mask[span] = True
    mask &= speaking
    assert mask.sum() > SAMPLE_RATE, "il faut au moins une seconde de parole mesurable"

    index = np.flatnonzero(mask)
    parts = decompose(a_only[index], {"commun": scene.common[index],
                                      "voix_A": scene.voice_a[index]})
    assert parts["commun_db"] < -12.0, (
        "la musique sous la voix reste trop présente dans le résidu")
    assert parts["voix_A_db"] > -1.5, "la voix a été abîmée pendant la parole"


def test_transient_effects_under_voice_are_captured(scene, extracted):
    """Les bruitages brefs (impacts) doivent partir dans le commun, pas dans le résidu."""
    common, _ = read_audio(extracted.common_path)
    a_only, _ = read_audio(extracted.a_only_path)
    # Les impacts sont les trames où la dérivée du commun vrai est la plus forte.
    energy = np.abs(np.diff(scene.common.mean(axis=1), prepend=0.0))
    threshold = float(np.quantile(energy, 0.999))
    hits = np.flatnonzero(energy > threshold)
    hits = hits[(hits > SAMPLE_RATE // 2) & (hits < scene.common.shape[0] - SAMPLE_RATE // 2)]
    assert hits.size > 10
    index = np.unique(np.concatenate([np.arange(h - 240, h + 720) for h in hits]))
    index = index[(index >= 0) & (index < scene.common.shape[0])]

    in_common = decompose(common[index], {"commun": scene.common[index],
                                          "voix_A": scene.voice_a[index]})
    in_residual = decompose(a_only[index], {"commun": scene.common[index],
                                            "voix_A": scene.voice_a[index]})
    assert in_common["commun_db"] > -3.0, "les transitoires communs sont perdus"
    assert in_residual["commun_db"] < in_common["commun_db"] - 8.0


def test_reconstruction_is_exact_by_construction(scene, extracted):
    """commun + résidu doit redonner l'entrée à l'erreur numérique près."""
    measurements = extracted.metadata["outputs"]["measurements"]
    assert measurements["a"]["reconstruction_max_abs_error"] < 1e-6
    assert measurements["b"]["reconstruction_max_abs_error"] < 1e-6

    original, _ = read_audio(scene.a)
    common, _ = read_audio(extracted.common_path)
    a_only, _ = read_audio(extracted.a_only_path)
    length = min(original.shape[0], common.shape[0], a_only.shape[0])
    error = np.abs(common[:length] + a_only[:length] - original[:length]).max()
    assert error < 1e-6, error


def test_outputs_are_finite_unclipped_and_of_expected_length(scene, extracted):
    original, _ = read_audio(scene.a)
    original_b, _ = read_audio(scene.b)
    for path, reference in ((extracted.common_path, original),
                            (extracted.a_only_path, original),
                            (extracted.b_only_path, original_b)):
        data, rate = read_audio(path)
        assert rate == scene.sample_rate
        assert data.shape == reference.shape, path.name
        assert np.isfinite(data).all(), path.name
        # WAV flottant : pas d'écrêtage, mais on vérifie qu'aucune sortie n'explose.
        assert float(np.abs(data).max()) < 4.0, path.name


def test_gain_between_versions_is_estimated_accurately(scene, extracted):
    gains = extracted.metadata["transfer"]["broadband_gain_per_channel"]
    assert len(gains) == 2
    for gain in gains:
        assert abs(gain - scene.gain_b) < 0.03 * scene.gain_b, gains


def test_channels_are_not_mixed(scene, extracted):
    """Aucun downmix : chaque canal reste indépendant et à sa place."""
    common, _ = read_audio(extracted.common_path)
    # Le jeu applique 1.0 et 0.9 au canal gauche/droit du commun ; le rapport doit tenir.
    span = window_for(scene)
    left = float(np.sqrt(np.mean(common[span, 0] ** 2)))
    right = float(np.sqrt(np.mean(common[span, 1] ** 2)))
    assert abs(right / left - 0.9) < 0.05, (left, right)


@pytest.mark.parametrize("baseline", ["average", "min-magnitude", "coherence-mask"])
def test_retained_method_beats_naive_baselines(scene, extracted, baseline, tmp_path):
    """La méthode retenue doit être au moins aussi bonne que chaque baseline naïve."""
    out_dir = tmp_path / baseline
    other = run_extract(ExtractOptions(input_a=scene.a, input_b=scene.b, out_dir=out_dir,
                                       method=baseline))
    span = window_for(scene)
    retained, _ = read_audio(extracted.common_path)
    naive, _ = read_audio(other.common_path)
    retained_sdr = si_sdr(retained, scene.common, span)
    naive_sdr = si_sdr(naive, scene.common, span)
    assert retained_sdr > naive_sdr, (baseline, retained_sdr, naive_sdr)

    retained_residual, _ = read_audio(extracted.a_only_path)
    naive_residual, _ = read_audio(other.a_only_path)
    assert (si_sdr(retained_residual, scene.voice_a, span)
            > si_sdr(naive_residual, scene.voice_a, span)), baseline


def test_fully_silent_input_is_refused_explicitly(tmp_path):
    """Une entrée muette ne peut pas être alignée : refus clair plutôt que sortie vide."""
    from ummahverse_audio.errors import AlignmentError

    from .conftest import make_music, make_voice, write_wav

    rng = np.random.default_rng(41)
    n = 3 * SAMPLE_RATE
    silence = np.zeros((n, 2), dtype=np.float32)
    real = np.column_stack([make_music(n, rng) + make_voice(n, 143, 0.0)] * 2)
    a = write_wav(tmp_path / "silence_a.wav", silence)
    b = write_wav(tmp_path / "reel_b.wav", real)
    with pytest.raises(AlignmentError) as excinfo:
        run_extract(ExtractOptions(input_a=a, input_b=b, out_dir=tmp_path / "out"))
    message = excinfo.value.user_message()
    assert "silencieuse" in message
    assert "--stream-a" in message


def test_mostly_silent_pair_with_short_common(tmp_path):
    """Un passage calme suivi de contenu commun : pas de NaN, commun retrouvé après."""
    from .conftest import make_music, make_voice, write_wav

    rng = np.random.default_rng(31)
    n = 6 * SAMPLE_RATE
    music = make_music(n, rng)
    music[: 3 * SAMPLE_RATE] = 0.0
    voice_a = make_voice(n, 143, 0.0)
    voice_b = make_voice(n, 187, 0.16)
    a = write_wav(tmp_path / "calme_a.wav", np.column_stack([music + voice_a] * 2))
    b = write_wav(tmp_path / "calme_b.wav", np.column_stack([0.9 * music + voice_b] * 2))
    result = run_extract(ExtractOptions(input_a=a, input_b=b, out_dir=tmp_path / "out"))
    common, _ = read_audio(result.common_path)
    assert np.isfinite(common).all()
    late = slice(int(4 * SAMPLE_RATE), int(5.5 * SAMPLE_RATE))
    reference = np.column_stack([music] * 2)
    assert si_sdr(common, reference, late) > 5.0


def test_no_common_content_yields_a_warning_and_no_invented_common(tmp_path):
    """A et B sans bande commune : l'outil doit prévenir et ne rien inventer."""
    from scipy.signal import butter, sosfilt

    from .conftest import make_voice, write_wav

    n = 8 * SAMPLE_RATE
    sos = butter(3, [200, 6000], btype="bandpass", fs=SAMPLE_RATE, output="sos")
    first = sosfilt(sos, np.random.default_rng(1).normal(size=n)) * 0.05
    second = sosfilt(sos, np.random.default_rng(2).normal(size=n)) * 0.05
    a = write_wav(tmp_path / "sans_a.wav",
                  np.column_stack([first + make_voice(n, 143, 0.0)] * 2))
    b = write_wav(tmp_path / "sans_b.wav",
                  np.column_stack([second + make_voice(n, 187, 0.16)] * 2))
    from ummahverse_audio.errors import AlignmentError

    try:
        result = run_extract(ExtractOptions(input_a=a, input_b=b, out_dir=tmp_path / "out"))
    except AlignmentError as exc:  # refus explicite : c'est une réponse acceptable
        message = exc.user_message()
        assert "Aucun alignement exploitable" in message
        assert "montage" in message, "le refus doit expliquer quoi vérifier"
        return
    # Sinon : le verdict doit annoncer qu'il n'y a pas de bande commune, un
    # avertissement doit le répéter, et le commun rester d'énergie faible.
    assert result.verdict is not None and not result.verdict.usable, result.verdict
    assert result.verdict.level == "absente", result.verdict.level
    assert result.warnings, "aucun avertissement alors qu'il n'y a pas de bande commune"
    common, _ = read_audio(result.common_path)
    original, _ = read_audio(a)
    span = slice(SAMPLE_RATE, n - SAMPLE_RATE)
    energy_ratio = float(np.mean(common[span] ** 2) / max(np.mean(original[span] ** 2), 1e-30))
    assert energy_ratio < 0.35, energy_ratio


def test_longer_scene_is_processed_in_bounded_memory(tmp_path):
    """Un signal plus long passe par plusieurs blocs, sans clic aux raccords."""
    scene = build_scene(tmp_path / "long", seconds=25.0)
    result = run_extract(ExtractOptions(input_a=scene.a, input_b=scene.b,
                                       out_dir=tmp_path / "out"))
    common, _ = read_audio(result.common_path)
    span = EvaluationWindow(sample_rate=SAMPLE_RATE).slice_for(common.shape[0])
    assert si_sdr(common, scene.common, span) > 12.0
    # Absence de clic : aucun saut d'échantillon à échantillon anormal.
    jumps = np.abs(np.diff(common[span, 0]))
    reference = np.abs(np.diff(scene.common[span, 0]))
    assert jumps.max() < 4.0 * reference.max(), (jumps.max(), reference.max())
    assert result.metadata["separation"]["diagnostics"]["blocks_processed"] > 4
