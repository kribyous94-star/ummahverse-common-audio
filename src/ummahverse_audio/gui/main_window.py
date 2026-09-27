"""Fenêtre principale : deux onglets, un par opération, en français.

Onglet 1 — deux langues : choix des fichiers et des flux, dossier de sortie,
« Analyser / synchroniser » puis « Extraire le commun et les résidus ».
Onglet 2 — nouvelle langue : `common.wav` + `common.json` + une autre piste,
« Soustraire le commun ».

Tout le calcul part dans un thread de travail (`JobRunner`) : la fenêtre reste
réactive et l'annulation est propre.
"""

from __future__ import annotations

import time
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDoubleSpinBox, QFormLayout,
                               QGroupBox, QHBoxLayout, QLabel, QMessageBox,
                               QProgressBar, QPushButton, QSizePolicy, QTabWidget,
                               QVBoxLayout, QWidget)

from ..analyze import run_analysis
from ..scout import run_scout
from ..errors import OutputCollisionError, UmmahAudioError
from ..extract import ExtractOptions, run_extract
from ..separate import METHODS
from ..subtract import SubtractOptions, default_report_path, run_subtract
from .widgets import (AUDIO_FILTER, JSON_FILTER, WAV_FILTER, LogView, PathField,
                      StreamSelector, open_document, open_in_file_manager)
from .worker import JobRunner


class OperationPanel(QWidget):
    """Socle commun aux deux onglets : progression, journal, sorties, annulation.

    Les sous-classes appellent `super().__init__(parent)` en premier, construisent
    leurs propres champs, puis insèrent `self.status_box` et `self.log_box` dans leur
    disposition.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.runner = JobRunner(self)
        self.runner.progressed.connect(self._on_progress)
        self._started_at: float | None = None
        self._timer = QTimer(self)
        self._timer.setInterval(500)
        self._timer.timeout.connect(self._tick)
        self._result = None
        self.status_box = self._build_status_block()
        self.log_box = self._build_log_block()
        self.runner.logged.connect(self.log.append)
        self.runner.finished.connect(self._finished)
        self.open_folder_button.clicked.connect(self._open_folder)
        self.open_report_button.clicked.connect(self._open_report)

    # --- à implémenter par les sous-classes -------------------------------------
    def _finished(self, outcome) -> None:
        raise NotImplementedError

    def _open_folder(self) -> None:
        raise NotImplementedError

    def _open_report(self) -> None:
        raise NotImplementedError

    # --- éléments communs -------------------------------------------------------
    def _build_status_block(self) -> QWidget:
        box = QGroupBox("État")
        self.progress = QProgressBar()
        self.progress.setRange(0, 1000)
        self.progress.setValue(0)
        self.progress.setFormat("%p %")
        self.status = QLabel("Prêt.")
        self.status.setWordWrap(True)
        self.elapsed = QLabel("Durée : —")
        self.cancel_button = QPushButton("Annuler")
        self.cancel_button.setEnabled(False)
        self.cancel_button.clicked.connect(self._cancel)
        row = QHBoxLayout()
        row.addWidget(self.elapsed)
        row.addStretch(1)
        row.addWidget(self.cancel_button)
        layout = QVBoxLayout(box)
        layout.addWidget(self.progress)
        layout.addWidget(self.status)
        layout.addLayout(row)
        return box

    def _build_log_block(self) -> QWidget:
        box = QGroupBox("Journal, avertissements et mesures")
        self.log = LogView()
        self.log.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.outputs_row = QHBoxLayout()
        self.open_folder_button = QPushButton("Ouvrir le dossier des sorties")
        self.open_folder_button.setEnabled(False)
        self.open_report_button = QPushButton("Ouvrir le rapport JSON")
        self.open_report_button.setEnabled(False)
        self.outputs_row.addWidget(self.open_folder_button)
        self.outputs_row.addWidget(self.open_report_button)
        self.outputs_row.addStretch(1)
        layout = QVBoxLayout(box)
        layout.addWidget(self.log, 1)
        layout.addLayout(self.outputs_row)
        return box

    # --- progression -----------------------------------------------------------
    def _on_progress(self, fraction: float, message: str) -> None:
        if fraction >= 0:
            self.progress.setValue(int(max(0.0, min(fraction, 1.0)) * 1000))
        if message:
            self.status.setText(message)

    def _tick(self) -> None:
        if self._started_at is None:
            return
        seconds = time.monotonic() - self._started_at
        self.elapsed.setText(f"Durée : {int(seconds // 60):d} min {seconds % 60:04.1f} s")

    def _begin(self, message: str) -> None:
        self.progress.setValue(0)
        self.status.setText(message)
        self.log.append(f"— {message}")
        self.cancel_button.setEnabled(True)
        self._started_at = time.monotonic()
        self._timer.start()

    def _end(self, message: str) -> None:
        self._timer.stop()
        self._tick()
        self.cancel_button.setEnabled(False)
        self.status.setText(message)

    def _cancel(self) -> None:
        if self.runner.busy:
            self.runner.cancel()
            self.status.setText("Annulation demandée, arrêt au prochain bloc…")
            self.cancel_button.setEnabled(False)


class ExtractTab(OperationPanel):
    """Onglet « Deux langues → commun »."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.on_extract_done = None          # rempli par la fenêtre pour lier l'onglet 2
        self.field_a = PathField("Première langue (A)", "open", AUDIO_FILTER)
        self.stream_a = StreamSelector("Flux audio de A")
        self.field_b = PathField("Seconde langue (B)", "open", AUDIO_FILTER)
        self.stream_b = StreamSelector("Flux audio de B")
        self.field_out = PathField("Dossier de sortie", "directory")
        self.field_a.changed.connect(lambda _: self._reload(self.field_a, self.stream_a, "A"))
        self.field_b.changed.connect(lambda _: self._reload(self.field_b, self.stream_b, "B"))

        self.method = QComboBox()
        for name in METHODS:
            self.method.addItem("wiener (méthode retenue)" if name == "wiener" else
                                f"{name} (baseline de comparaison)", name)
        self.transfer = QComboBox()
        self.transfer.addItem("gain + égalisation par bande (défaut)", "band")
        self.transfer.addItem("gain large bande seul", "gain")
        self.transfer.addItem("aucune transformation", "none")
        self.max_offset = QDoubleSpinBox()
        self.max_offset.setRange(0.5, 600.0)
        self.max_offset.setValue(30.0)
        self.max_offset.setSuffix(" s")
        self.allow_unknown = QCheckBox(
            "Accepter un agencement de canaux inconnu (appariement dans l'ordre du fichier)")

        self.scout_button = QPushButton("Repérer les zones exploitables")
        self.scout_button.setToolTip(
            "Balaie tout le film et indique où les deux versions partagent une bande "
            "commune. N'écrit aucun fichier.")
        self.scout_button.clicked.connect(self._scout)
        self.analyze_button = QPushButton("Analyser / synchroniser")
        self.analyze_button.clicked.connect(self._analyze)
        self.extract_button = QPushButton("Extraire le commun et les résidus")
        self.extract_button.clicked.connect(self._extract)

        inputs = QGroupBox("Entrées")
        form = QFormLayout(inputs)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        for widget in (self.field_a, self.stream_a, self.stream_a.status,
                       self.field_b, self.stream_b, self.stream_b.status, self.field_out):
            form.addRow(widget)

        advanced = QGroupBox("Réglages")
        grid = QFormLayout(advanced)
        grid.addRow("Estimateur du commun", self.method)
        grid.addRow("Transformation entre versions", self.transfer)
        grid.addRow("Décalage maximal recherché", self.max_offset)
        grid.addRow(self.allow_unknown)

        buttons = QHBoxLayout()
        buttons.addWidget(self.scout_button)
        buttons.addWidget(self.analyze_button)
        buttons.addWidget(self.extract_button)
        buttons.addStretch(1)

        layout = QVBoxLayout(self)
        layout.addWidget(inputs)
        layout.addWidget(advanced)
        layout.addLayout(buttons)
        layout.addWidget(self.status_box)
        layout.addWidget(self.log_box, 1)

    def _reload(self, field: PathField, selector: StreamSelector, tag: str) -> None:
        message = selector.load(field.path)
        if message:
            self.log.append(f"{tag} : {message}")

    def _collect(self) -> ExtractOptions | None:
        if self.field_a.path is None or self.field_b.path is None:
            QMessageBox.warning(self, "Entrées manquantes",
                                "Choisissez les deux fichiers audio à comparer.")
            return None
        if self.field_out.path is None:
            QMessageBox.warning(self, "Dossier manquant",
                                "Choisissez un dossier de sortie.")
            return None
        options = ExtractOptions(
            input_a=self.field_a.path, input_b=self.field_b.path,
            out_dir=self.field_out.path,
            stream_a=self.stream_a.selected_index, stream_b=self.stream_b.selected_index,
            method=self.method.currentData(), transfer_mode=self.transfer.currentData(),
            max_offset_s=self.max_offset.value(),
            allow_unknown_layout=self.allow_unknown.isChecked(),
        )
        return options if self._outputs_are_safe(options) else None

    def _outputs_are_safe(self, options: ExtractOptions) -> bool:
        """Refuse tout de suite si une sortie écraserait A ou B.

        Le moteur refuse de toute façon, mais l'utilisateur mérite de l'apprendre au
        clic plutôt qu'après le décodage.
        """
        from ..extract import A_ONLY_NAME, B_ONLY_NAME, COMMON_NAME, METADATA_NAME
        from ..paths import ensure_inputs_untouched

        out_dir = Path(options.out_dir)
        produced = [out_dir / name for name in
                    (COMMON_NAME, A_ONLY_NAME, B_ONLY_NAME, METADATA_NAME)]
        try:
            ensure_inputs_untouched([options.input_a, options.input_b], produced)
        except OutputCollisionError as exc:
            QMessageBox.warning(self, "Les entrées ne sont jamais modifiées",
                                exc.user_message())
            return False
        return True

    def _guard(self) -> bool:
        if self.runner.busy:
            QMessageBox.information(self, "Traitement en cours",
                                    "Attendez la fin du traitement ou annulez-le.")
            return False
        return True

    def _analyze(self) -> None:
        options = self._collect()
        if options is None or not self._guard():
            return
        self._set_busy(True)
        self._begin("Analyse et synchronisation…")
        self.runner.start("analyze", lambda progress: run_analysis(options, progress))

    def _scout(self) -> None:
        """Repérage : où, dans le film, la séparation a-t-elle une chance ?"""
        options = self._collect()
        if options is None or not self._guard():
            return
        self._set_busy(True)
        self._begin("Repérage des zones à bande commune…")
        self.runner.start("scout", lambda progress: run_scout(
            input_a=options.input_a, input_b=options.input_b,
            stream_a=options.stream_a, stream_b=options.stream_b,
            sample_rate=options.sample_rate,
            allow_unknown_layout=options.allow_unknown_layout,
            workspace_dir=options.workspace_dir, progress=progress))

    def _extract(self) -> None:
        options = self._collect()
        if options is None or not self._guard():
            return
        self._set_busy(True)
        self._begin("Extraction du commun et des résidus…")
        self.runner.start("extract", lambda progress: run_extract(options, progress))

    def _set_busy(self, busy: bool) -> None:
        self.scout_button.setEnabled(not busy)
        self.analyze_button.setEnabled(not busy)
        self.extract_button.setEnabled(not busy)

    def _finished(self, outcome) -> None:
        self._set_busy(False)
        if outcome.cancelled:
            self._end("Annulé. Aucun fichier de sortie complet n'a été conservé.")
            self.log.append("Traitement annulé par l'utilisateur.")
            return
        if outcome.error:
            self._end("Échec.")
            self.log.append(f"ERREUR : {outcome.error}")
            QMessageBox.critical(self, "Échec du traitement", outcome.error)
            return
        if outcome.kind == "scout":
            report = outcome.result
            self._end("Repérage terminé.")
            for line in report.summary_lines():
                self.log.append("  " + line)
            self._log_verdict(report.payload["common_bed_verdict"])
            self._log_warnings(report.warnings)
            return
        if outcome.kind == "analyze":
            report = outcome.result
            self._end("Analyse terminée.")
            for line in report.summary_lines():
                self.log.append("  " + line)
            if report.verdict is not None:
                self._log_verdict(report.verdict.as_dict())
            self._log_warnings(report.warnings)
            return
        result = outcome.result
        self._result = result
        self._end("Extraction terminée.")
        if getattr(result, "verdict", None) is not None:
            self._log_verdict(result.verdict.as_dict())
        self.log.append("Fichiers produits :")
        for path in result.outputs:
            self.log.append(f"  {path}")
        self._log_quality(result.metadata)
        self._log_warnings(result.warnings)
        self.open_folder_button.setEnabled(True)
        self.open_report_button.setEnabled(True)
        if callable(self.on_extract_done):
            self.on_extract_done(result)

    def _log_verdict(self, payload: dict) -> None:
        """Le verdict est l'information la plus importante du journal."""
        marker = {"absente": "ÉCHEC", "faible": "FAIBLE",
                  "partielle": "PARTIEL", "bonne": "OK"}.get(payload["level"], "")
        self.log.append("")
        self.log.append(f"[{marker}] {payload['headline']}")
        for key in ("detail", "advice"):
            if payload.get(key):
                self.log.append(f"  {payload[key]}")
        self.log.append("")

    def _log_quality(self, metadata: dict) -> None:
        alignment = metadata["alignment"]
        diagnostics = metadata["separation"]["diagnostics"]
        measurements = metadata["outputs"]["measurements"]
        performance = metadata["performance"]
        self.log.append("Métadonnées de qualité :")
        self.log.append(f"  décalage global : {alignment['global_delay_s'] * 1000:.2f} ms "
                        f"(confiance {alignment['global_confidence']:.3f})")
        self.log.append(f"  dérive : {alignment['drift_ppm']:.1f} ppm ; blocs comparables : "
                        f"{alignment['comparable_block_fraction'] * 100:.1f} %")
        self.log.append(f"  cohérence pondérée par l'énergie : "
                        f"{diagnostics['energy_weighted_coherence']:.3f}")
        self.log.append(f"  part du commun dans l'énergie de A : "
                        f"{diagnostics['common_share_of_a_energy'] * 100:.1f} %")
        self.log.append(f"  crêtes : commun {measurements['a']['peak_common']:.3f}, "
                        f"a_only {measurements['a']['peak_residual']:.3f}, "
                        f"b_only {measurements['b']['peak_residual']:.3f}")
        self.log.append(f"  erreur de reconstruction : "
                        f"A {measurements['a']['reconstruction_max_abs_error']:.1e}, "
                        f"B {measurements['b']['reconstruction_max_abs_error']:.1e}")
        for tag in ("a", "b"):
            untreated = measurements[tag]["untreated_seconds"]
            if untreated:
                self.log.append(f"  zones non traitées ({tag}) : {untreated:.2f} s")
        if "peak_rss_mb" in performance:
            self.log.append(f"  pic mémoire : {performance['peak_rss_mb']:.0f} Mio")

    def _log_warnings(self, warnings: list[str]) -> None:
        if not warnings:
            self.log.append("Aucun avertissement de compatibilité ou d'alignement.")
            return
        self.log.append("Avertissements :")
        for warning in warnings:
            self.log.append(f"  ⚠ {warning}")

    def _open_folder(self) -> None:
        if self.field_out.path:
            open_in_file_manager(self.field_out.path)

    def _open_report(self) -> None:
        if self._result is not None:
            open_document(self._result.metadata_path)


class SubtractTab(OperationPanel):
    """Onglet « Réutiliser le commun sur une autre langue »."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.field_common = PathField("common.wav", "open", WAV_FILTER)
        self.field_metadata = PathField("common.json", "open", JSON_FILTER)
        self.field_input = PathField("Piste d'une autre langue", "open", AUDIO_FILTER)
        self.stream_input = StreamSelector("Flux audio de cette piste")
        self.field_output = PathField("Fichier de sortie (résidu)", "save", WAV_FILTER)
        self.field_input.changed.connect(
            lambda _: self._reload(self.field_input, self.stream_input))

        self.adapt = QComboBox()
        self.adapt.addItem("gain par bloc, borné (défaut)", "block-gain")
        self.adapt.addItem("transformation globale seule", "global")
        self.export_adapted = QCheckBox(
            "Écrire aussi le commun adapté (utile pour l'écoute de contrôle)")
        self.subtract_button = QPushButton("Soustraire le commun")
        self.subtract_button.clicked.connect(self._subtract)

        inputs = QGroupBox("Entrées")
        form = QFormLayout(inputs)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        for widget in (self.field_common, self.field_metadata, self.field_input,
                       self.stream_input, self.stream_input.status, self.field_output):
            form.addRow(widget)

        advanced = QGroupBox("Réglages")
        grid = QFormLayout(advanced)
        grid.addRow("Adaptation du commun", self.adapt)
        grid.addRow(self.export_adapted)

        buttons = QHBoxLayout()
        buttons.addWidget(self.subtract_button)
        buttons.addStretch(1)

        layout = QVBoxLayout(self)
        layout.addWidget(inputs)
        layout.addWidget(advanced)
        layout.addLayout(buttons)
        layout.addWidget(self.status_box)
        layout.addWidget(self.log_box, 1)

    def _reload(self, field: PathField, selector: StreamSelector) -> None:
        message = selector.load(field.path)
        if message:
            self.log.append(message)
        if field.path and self.field_output.path is None:
            suggestion = field.path.with_name(field.path.stem + "_only.wav")
            self.field_output.set_path(suggestion)

    def prefill_from_extract(self, result) -> None:
        """Renseigne les champs à partir d'une extraction qui vient de se terminer."""
        self.field_common.set_path(result.common_path)
        self.field_metadata.set_path(result.metadata_path)
        self.log.append("Commun et métadonnées repris de l'onglet précédent : "
                        "aucune ré-extraction n'est nécessaire.")

    def _subtract(self) -> None:
        if self.runner.busy:
            QMessageBox.information(self, "Traitement en cours",
                                    "Attendez la fin du traitement ou annulez-le.")
            return
        if self.field_common.path is None or self.field_input.path is None:
            QMessageBox.warning(self, "Entrées manquantes",
                                "Choisissez common.wav et la piste de la nouvelle langue.")
            return
        if self.field_output.path is None:
            QMessageBox.warning(self, "Sortie manquante",
                                "Choisissez le fichier WAV de sortie.")
            return
        options = SubtractOptions(
            common=self.field_common.path, input_c=self.field_input.path,
            output=self.field_output.path, metadata=self.field_metadata.path,
            stream=self.stream_input.selected_index, adapt=self.adapt.currentData(),
            export_adapted_common=self.export_adapted.isChecked(),
        )
        if not self._outputs_are_safe(options):
            return
        self.subtract_button.setEnabled(False)
        self._begin("Soustraction du commun…")
        self.runner.start("subtract", lambda progress: run_subtract(options, progress))

    def _outputs_are_safe(self, options: SubtractOptions) -> bool:
        """Refuse tout de suite si la sortie écraserait le commun ou la piste d'entrée."""
        from ..paths import ensure_inputs_untouched

        output = Path(options.output)
        produced = [output, default_report_path(output)]
        if options.export_adapted_common:
            produced.append(output.with_name(output.stem + "_adapted_common.wav"))
        try:
            ensure_inputs_untouched(
                [options.common, options.input_c, options.metadata], produced)
        except OutputCollisionError as exc:
            QMessageBox.warning(self, "Les entrées ne sont jamais modifiées",
                                exc.user_message())
            return False
        return True

    def _finished(self, outcome) -> None:
        self.subtract_button.setEnabled(True)
        if outcome.cancelled:
            self._end("Annulé. Le fichier de sortie partiel a été supprimé.")
            self.log.append("Traitement annulé par l'utilisateur.")
            return
        if outcome.error:
            self._end("Échec.")
            self.log.append(f"ERREUR : {outcome.error}")
            QMessageBox.critical(self, "Échec du traitement", outcome.error)
            return
        result = outcome.result
        self._result = result
        self._end("Soustraction terminée.")
        measurements = result.report["outputs"]["measurements"]
        self.log.append(f"Résidu écrit : {result.output_path}")
        self.log.append(f"Rapport : {result.report_path}")
        if result.adapted_common_path:
            self.log.append(f"Commun adapté : {result.adapted_common_path}")
        self.log.append("Métadonnées de qualité :")
        self.log.append(f"  gain appliqué au commun : médian "
                        f"{measurements['applied_gain_median']}, "
                        f"min {measurements['applied_gain_min']}, "
                        f"max {measurements['applied_gain_max']}")
        self.log.append(f"  cases fiables : "
                        f"{measurements['mean_reliable_cell_fraction'] * 100:.2f} %")
        self.log.append(f"  zones non traitées : {measurements['untreated_seconds']:.2f} s")
        self.log.append(f"  crête du résidu : {measurements['peak_residual']:.3f}")
        self.log.append("  erreur de reconstruction : "
                        f"{measurements['reconstruction_max_abs_error']:.1e}")
        if result.warnings:
            self.log.append("Avertissements :")
            for warning in result.warnings:
                self.log.append(f"  ⚠ {warning}")
        else:
            self.log.append("Aucun avertissement.")
        self.open_folder_button.setEnabled(True)
        self.open_report_button.setEnabled(True)

    def _open_folder(self) -> None:
        if self.field_output.path:
            open_in_file_manager(self.field_output.path)

    def _open_report(self) -> None:
        if self._result is not None:
            open_document(self._result.report_path)
        elif self.field_output.path:
            open_document(default_report_path(self.field_output.path))


class MainWindow(QWidget):
    """Assemble les deux onglets et gère la fermeture pendant un traitement."""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("UmmahVerse — bande commune de deux langues (POC)")
        self.resize(1000, 800)
        self.extract_tab = ExtractTab()
        self.subtract_tab = SubtractTab()
        self.extract_tab.on_extract_done = self._after_extract
        self.tabs = QTabWidget()
        self.tabs.addTab(self.extract_tab, "1 · Deux langues → commun")
        self.tabs.addTab(self.subtract_tab, "2 · Réutiliser le commun")
        header = QLabel(
            "Les fichiers restent sur cet ordinateur et <b>les fichiers d'entrée ne "
            "sont jamais modifiés</b> : ils sont uniquement lus, tout est écrit dans le "
            "dossier de sortie. Les sorties « only » sont des résidus estimés, pas des "
            "voix isolées garanties : voir les limites dans le rapport JSON et le README."
        )
        header.setWordWrap(True)
        header.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout = QVBoxLayout(self)
        layout.addWidget(header)
        layout.addWidget(self.tabs, 1)

    def _after_extract(self, result) -> None:
        self.subtract_tab.prefill_from_extract(result)

    def closeEvent(self, event) -> None:  # noqa: N802 (API Qt)
        busy = [tab for tab in (self.extract_tab, self.subtract_tab) if tab.runner.busy]
        if busy:
            answer = QMessageBox.question(
                self, "Traitement en cours",
                "Un traitement est en cours. Le fermer l'annulera. Continuer ?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No)
            if answer != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
            for tab in busy:
                tab.runner.cancel()
            for tab in busy:
                tab.runner.wait(15_000)
        event.accept()


def check_ffmpeg(parent: QWidget | None = None) -> bool:
    """Vérifie FFmpeg au démarrage et affiche une erreur explicite s'il manque."""
    from ..ffmpeg_tools import find_ffmpeg, find_ffprobe

    try:
        find_ffmpeg()
        find_ffprobe()
    except UmmahAudioError as exc:
        QMessageBox.critical(parent, "FFmpeg introuvable", exc.user_message())
        return False
    return True


def suggest_output_dir(source: Path) -> Path:
    return source.parent / "ummahverse_sorties"
