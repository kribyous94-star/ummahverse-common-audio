"""Estimation de la bande commune dans le domaine STFT complexe.

Modèle par case temps-fréquence, après alignement et compensation de `H(f)` :

    A = C + D_A        B = H·C + D_B

Hypothèse de travail : `D_A` et `D_B` (les dialogues et ce qui les accompagne) sont
**décorrélés entre eux et du commun** sur le voisinage de lissage. Sous cette seule
hypothèse, l'inter-spectre lissé vaut `S_AB = conj(H)·S_CC`, ce qui donne une mesure
de la puissance commune **même quand le dialogue recouvre la musique**, sans aucun
détecteur de parole. On applique ensuite l'estimateur MMSE (Wiener) à deux
observations pour reconstituer `C` en amplitude *et* en phase.

Limites à garder en tête, mesurées dans `docs/results.md` :

* `S_AB` est une statistique moyennée ; sur un voisinage court elle est bruitée, sur
  un voisinage long elle perd la résolution temps-fréquence. Un terme correcteur de
  biais `S_AA·S_BB/N` est implémenté (`bias_correction`) mais **désactivé par défaut** :
  mesuré sur le jeu synthétique, il dégrade toutes les métriques, car la contrainte
  `S_CC ≤ min(S_AA, S_BB/|H|²)` borne déjà la surestimation et la correction, elle,
  sous-estime la puissance commune.
* Tout ce qui est réellement commun aux deux versions est capturé : musique et
  bruitages, mais aussi mêmes mots prononcés au même instant, voix originale laissée
  en fond, réverbération de dialogue, et pompage d'un compresseur piloté par la voix.
  « Cohérent » ne veut pas dire « musique ».
* L'estimateur peut effacer une voix s'il surajuste : la contrainte
  `S_CC ≤ min(S_AA, S_BB/|H|²)` et le plafond d'amplitude limitent ce risque sans
  le supprimer.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .stft import (StftConfig, debias_coherence, local_spectra, smooth2d,
                   magnitude_squared_coherence)

#: Méthodes disponibles ; `wiener` est la méthode retenue, les autres sont les baselines.
METHODS = ("auto", "wiener", "magnitude", "average", "min-magnitude", "coherence-mask")
#: Méthodes réellement applicables (« auto » est résolu avant le traitement).
CONCRETE_METHODS = ("wiener", "magnitude", "average", "min-magnitude", "coherence-mask")


@dataclass(frozen=True)
class SeparationParams:
    """Paramètres de l'estimateur, versionnés dans `common.json`."""

    method: str = "wiener"
    bias_correction: float = 0.0
    magnitude_cap: float = 1.5
    floor_ratio: float = 1e-10
    magnitude_oversubtraction: float = 1.0
    """Facteur appliqué à la puissance commune estimée en mode magnitude.

    Au-dessus de 1, on retire davantage : moins de fond dans le résidu, mais la voix
    commence à être entamée. En dessous, l'inverse.
    """

    def as_dict(self) -> dict:
        return {
            "method": self.method,
            "bias_correction": self.bias_correction,
            "magnitude_cap": self.magnitude_cap,
            "floor_ratio": self.floor_ratio,
            "magnitude_oversubtraction": self.magnitude_oversubtraction,
        }


@dataclass
class BlockDiagnostics:
    """Mesures accumulées sur un bloc, agrégées ensuite dans les métadonnées."""

    energy_a: float = 0.0
    energy_common: float = 0.0
    coherence_weighted: float = 0.0
    coherence_weight: float = 0.0
    cells: int = 0
    capped_cells: int = 0

    def merge(self, other: "BlockDiagnostics") -> None:
        self.energy_a += other.energy_a
        self.energy_common += other.energy_common
        self.coherence_weighted += other.coherence_weighted
        self.coherence_weight += other.coherence_weight
        self.cells += other.cells
        self.capped_cells += other.capped_cells

    @property
    def mean_coherence(self) -> float:
        return self.coherence_weighted / self.coherence_weight if self.coherence_weight else 0.0

    @property
    def common_energy_ratio(self) -> float:
        return self.energy_common / self.energy_a if self.energy_a else 0.0

    def debiased_coherence(self, config: "StftConfig") -> float:
        return debias_coherence(self.mean_coherence, config.independent_cells)

    def as_dict(self) -> dict:
        return {
            "energy_weighted_coherence": round(self.mean_coherence, 4),
            "common_share_of_a_energy": round(self.common_energy_ratio, 4),
            "amplitude_capped_cell_fraction": round(
                self.capped_cells / self.cells if self.cells else 0.0, 5),
        }


def estimate_common_spectrum(spec_a: np.ndarray, spec_b: np.ndarray,
                             transfer: np.ndarray, config: StftConfig,
                             params: SeparationParams,
                             magnitude_transfer: np.ndarray | None = None
                             ) -> tuple[np.ndarray, BlockDiagnostics]:
    """Estime le spectre du commun dans le repère de A pour un canal et un bloc.

    `spec_b` est déjà aligné sur `spec_a` (décalage entier + rampe de phase), et
    `transfer` est la réponse `H(f)` du canal.
    """
    if params.method not in METHODS:
        raise ValueError(f"Méthode inconnue : {params.method}")

    h = np.asarray(transfer, dtype=np.complex128)[:, None]
    h_mag2 = np.abs(h) ** 2
    h_mag2 = np.maximum(h_mag2, 1e-12)
    saa, sbb, sab = local_spectra(spec_a, spec_b, config)
    gamma = magnitude_squared_coherence(saa, sbb, sab)
    floor = params.floor_ratio * float(np.mean(saa) + np.mean(sbb)) + 1e-30

    diagnostics = BlockDiagnostics()
    diagnostics.energy_a = float(np.sum(np.abs(spec_a) ** 2))
    diagnostics.coherence_weighted = float(np.sum(gamma * saa))
    diagnostics.coherence_weight = float(np.sum(saa))
    diagnostics.cells = gamma.size

    spec_b_equalized = spec_b / h
    cap_magnitude = np.minimum(np.abs(spec_a), np.abs(spec_b_equalized))

    if params.method == "average":
        spec_c = 0.5 * (spec_a + spec_b_equalized)
    elif params.method == "min-magnitude":
        spec_c = cap_magnitude * np.exp(1j * np.angle(spec_a))
    elif params.method == "coherence-mask":
        spec_c = gamma * spec_a
    elif params.method == "magnitude":
        # Aucune phase n'intervient. Modèle en puissance, par case :
        #     P_A = P_M + P_DA        P_B = |H|² P_M + P_DB
        # Les dialogues des deux langues n'occupent pas les mêmes cases — mots
        # différents, instants différents — donc le minimum des deux puissances
        # égalisées estime le fond commun. Biaisé vers le haut là où les deux
        # parlent en même temps : c'est la limite connue de la méthode.
        h_magnitude = (np.abs(h) if magnitude_transfer is None
                       else np.asarray(magnitude_transfer, dtype=np.float64)[:, None])
        h_magnitude = np.maximum(h_magnitude, 1e-6)
        power_b = smooth2d(np.abs(spec_b) ** 2 / h_magnitude ** 2,
                           config.smooth_bins, config.smooth_frames)
        power_a = smooth2d(np.abs(spec_a) ** 2, config.smooth_bins, config.smooth_frames)
        common_power = params.magnitude_oversubtraction * np.minimum(power_a, power_b)
        mask = np.sqrt(np.clip(common_power / np.maximum(power_a, floor), 0.0, 1.0))
        spec_c = mask * spec_a   # la phase de A est conservée telle quelle
    else:
        # Puissance commune lue dans l'inter-spectre : |S_AB| = |H|·S_CC.
        cross2 = np.abs(sab) ** 2
        bias = params.bias_correction * saa * sbb / max(config.independent_cells, 1.0)
        scc = np.sqrt(np.maximum(cross2 - bias, 0.0)) / np.abs(h)
        scc = np.minimum(scc, np.minimum(saa, sbb / h_mag2))
        s_da = np.maximum(saa - scc, floor)
        s_db = np.maximum(sbb - h_mag2 * scc, floor)
        numerator = scc * (spec_a / s_da + np.conj(h) * spec_b / s_db)
        denominator = 1.0 + scc * (1.0 / s_da + h_mag2 / s_db)
        spec_c = numerator / denominator

    magnitude = np.abs(spec_c)
    limit = params.magnitude_cap * cap_magnitude
    over = magnitude > limit
    if over.any():
        diagnostics.capped_cells = int(over.sum())
        scale = np.ones_like(magnitude)
        np.divide(limit, np.maximum(magnitude, 1e-30), out=scale, where=over)
        spec_c = np.where(over, spec_c * scale, spec_c)

    diagnostics.energy_common = float(np.sum(np.abs(spec_c) ** 2))
    return spec_c, diagnostics


@dataclass
class SeparationSummary:
    """Agrégat des diagnostics de tous les blocs traités."""

    diagnostics: BlockDiagnostics = field(default_factory=BlockDiagnostics)
    blocks_processed: int = 0
    blocks_skipped: int = 0
    blocks_uncertain: int = 0

    def as_dict(self) -> dict:
        payload = self.diagnostics.as_dict()
        payload.update({
            "blocks_processed": self.blocks_processed,
            "blocks_skipped_no_alignment": self.blocks_skipped,
            "blocks_processed_with_interpolated_delay": self.blocks_uncertain,
        })
        return payload
