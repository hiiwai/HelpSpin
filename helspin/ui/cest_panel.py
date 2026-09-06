"""Dedicated CEST panel: nutation calibration and Z-spectrum display.

A window of its own rather than a canvas mode. The main canvas overlays whole
spectra against a shared ppm axis; both CEST experiments instead reduce a
pseudo-2D series to ONE number per row and plot that against a quantity the
main axis cannot express -- pulse duration in milliseconds, or saturation
offset. Sharing the canvas would have meant a ppm axis that means something
different in each mode.

The panel builds its widgets in __init__ and shows nothing by itself, so a
test can construct it, drive it and read results back without any modal
`exec` ever running. That is the same split HANDOFF.md records for the other
dialogs: QDialog.exec on a Shiboken-wrapped class cannot be monkeypatched
reliably, so construction and showing stay separate.
"""

from __future__ import annotations

import contextlib
import math
from pathlib import Path

import numpy as np
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from matplotlib.figure import Figure
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ..domain.cest import (
    CestError,
    choose_nutation_model,
    corrected_power,
    dip_candidates,
    find_peak_ppm,
    measure_rows,
    normalise_z,
    power_ratio_db,
    remove_dip,
    row_noise,
    window_indices,
)
from ..infrastructure.cest_reader import (
    classify,
    load_pseudo_2d,
    nominal_nutation_field,
    nutation_axis,
    read_offsets,
)
from ..services.cest_fit import fit_dip, fit_two_dips, polish_nutation

MEASURE_MODES = (("Integral", "integral"), ("Peak height", "height"),
                 ("Fixed point", "fixed"))

# Distinguishable at a glance and safe for the common colour-vision
# deficiencies -- an overlay of five Z-spectra is unreadable otherwise.
OVERLAY_COLOURS = (
    "#1b6ca8", "#d1495b", "#3f8f52", "#8b5fbf", "#e0a458",
    "#00838f", "#a8442a", "#5a6b7c",
)


class _Plot(FigureCanvasQTAgg):
    """Small matplotlib canvas with an optional residual strip.

    Wheel scrolling zooms the x axis about the cursor. Typing numbers into
    range boxes is fine for a precise window but hopeless for hunting, which
    is what the Z-spectrum is actually for -- the reader wants to sweep along
    the baseline looking for a dip, not compute two bounds each time.
    """

    zoomed = Signal()

    def __init__(self, residuals: bool = False, wheel_zoom: bool = False):
        figure = Figure(figsize=(5.4, 3.8), layout="constrained")
        super().__init__(figure)
        if residuals:
            self.axes = figure.add_subplot(4, 1, (1, 3))
            self.residual_axes = figure.add_subplot(4, 1, 4, sharex=self.axes)
        else:
            self.axes = figure.add_subplot(1, 1, 1)
            self.residual_axes = None
        self._wheel_zoom = wheel_zoom
        if wheel_zoom:
            self.setFocusPolicy(Qt.WheelFocus)

    def wheelEvent(self, event):
        """Zoom x about the cursor; with Shift held, zoom y instead.

        Anchored on the cursor rather than the axis centre so the feature
        under the pointer stays put -- centre-anchored zoom walks the thing
        you are looking at off the edge.
        """
        if not self._wheel_zoom:
            super().wheelEvent(event)
            return
        delta = event.angleDelta().y()
        if not delta:
            return
        # 0.85 per notch: brisk enough to cross a Z-spectrum in a few turns,
        # gentle enough to settle on a dip.
        factor = 0.85 if delta > 0 else 1.0 / 0.85

        position = event.position()
        height = max(self.height(), 1)
        width = max(self.width(), 1)
        inside = self.axes.get_position()
        # Qt y grows downward, matplotlib figure coordinates grow upward.
        fx = position.x() / width
        fy = 1.0 - position.y() / height
        if not (inside.x0 <= fx <= inside.x1 and inside.y0 <= fy <= inside.y1):
            return                      # outside the axes: not our gesture

        vertical = bool(event.modifiers() & Qt.ShiftModifier)
        if vertical:
            low, high = self.axes.get_ylim()
            frac = (fy - inside.y0) / max(inside.y1 - inside.y0, 1e-9)
        else:
            low, high = self.axes.get_xlim()
            frac = (fx - inside.x0) / max(inside.x1 - inside.x0, 1e-9)
        anchor = low + (high - low) * frac
        new_low = anchor + (low - anchor) * factor
        new_high = anchor + (high - anchor) * factor
        if new_low == new_high:
            return
        if vertical:
            self.axes.set_ylim(new_low, new_high)
        else:
            # Preserve the axis direction: on the Z-spectrum x is inverted
            # for NMR convention, and set_xlim would silently undo it.
            self.axes.set_xlim(new_low, new_high)
        self.draw_idle()
        self.zoomed.emit()
        event.accept()

    def clear(self):
        self.axes.clear()
        if self.residual_axes is not None:
            self.residual_axes.clear()


class CestPanel(QWidget):
    """Load one pseudo-2D experiment and analyse it."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("CEST")
        self.resize(1040, 720)
        self._data = None
        self._offsets = None
        self._fit = None
        self._z = None
        # Kept Z-spectra, each already normalised to ITS OWN I0 so that
        # experiments with different receiver gain, scan count or
        # concentration are still comparable on one axis. Storing the
        # normalised curve rather than the raw rows also means an overlay
        # entry survives its dataset being unloaded.
        self._overlays: list[tuple[str, object]] = []

        self._path_label = QLabel("No experiment loaded")
        self._path_label.setWordWrap(True)
        self._source = QComboBox()
        self._source.addItem("Automatic (raw ser unless pdata is complete)", "auto")
        self._source.addItem("Raw ser", "ser")
        self._source.addItem("Processed 2rr", "2rr")

        open_button = QPushButton("Open experiment\u2026")
        open_button.clicked.connect(self._choose)
        reload_button = QPushButton("Reload")
        reload_button.clicked.connect(self._reload)

        header = QHBoxLayout()
        header.addWidget(open_button)
        header.addWidget(reload_button)
        header.addWidget(QLabel("Source:"))
        header.addWidget(self._source, 1)

        self._notes = QPlainTextEdit()
        self._notes.setReadOnly(True)
        self._notes.setMaximumHeight(74)
        self._notes.setPlaceholderText(
            "Warnings about the loaded dataset appear here."
        )

        self._tabs = QTabWidget()
        self._tabs.addTab(self._build_nutation_tab(), "Calibration (nutation)")
        self._tabs.addTab(self._build_z_tab(), "Z-spectrum")

        layout = QVBoxLayout(self)
        layout.addLayout(header)
        layout.addWidget(self._path_label)
        layout.addWidget(self._notes)
        layout.addWidget(self._tabs, 1)

    # ---------------------------------------------------------------- build

    def _build_nutation_tab(self) -> QWidget:
        self._n_centre = QDoubleSpinBox()
        self._n_centre.setRange(-2000.0, 2000.0)
        self._n_centre.setDecimals(4)
        self._n_centre.setSuffix(" ppm")
        self._n_half = QDoubleSpinBox()
        self._n_half.setRange(0.0005, 50.0)
        self._n_half.setDecimals(4)
        self._n_half.setSingleStep(0.01)
        # Measured optimum on 19F nutation data: residuals fell from 7.0% at
        # +/-0.20 ppm to 3.9% at +/-0.03, then rose again at +/-0.02 as
        # signal was discarded.
        self._n_half.setValue(0.03)
        self._n_half.setSuffix(" ppm")
        self._n_half.setToolTip(
            "Half-width: the window runs centre MINUS this to centre PLUS\n"
            "this. Narrow is better here -- on 19F data residuals fell from\n"
            "7.0% at ±0.20 ppm to 3.9% at ±0.03 ppm."
        )
        self._n_span = QLabel("—")
        self._n_centre.valueChanged.connect(self._update_n_span)
        self._n_half.valueChanged.connect(self._update_n_span)
        self._n_mode = QComboBox()
        for label, value in MEASURE_MODES:
            self._n_mode.addItem(label, value)
        self._n_mode.setToolTip(
            "Integration is preferred for a nutation: it averages noise over a\n"
            "peak whose SIGN carries the measurement."
        )
        self._n_model = QComboBox()
        self._n_model.addItem("Automatic (AICc)", "auto")
        self._n_model.addItem("Plain sine", "plain")
        self._n_model.addItem("Damped sine", "damped")
        self._n_baseline = QCheckBox("Subtract flanking baseline")
        self._n_target = QDoubleSpinBox()
        self._n_target.setRange(0.1, 100000.0)
        self._n_target.setDecimals(2)
        self._n_target.setValue(60.0)
        self._n_target.setSuffix(" Hz")
        self._n_target.setToolTip(
            "Field you want. Need not equal the nominal field of this\n"
            "calibration: power scales as B1 squared, so the correction\n"
            "factor transfers between them."
        )

        form = QFormLayout()
        form.addRow("Peak centre", self._n_centre)
        form.addRow("Half-width (±)", self._n_half)
        form.addRow("Window", self._n_span)
        form.addRow("Measurement", self._n_mode)
        form.addRow("Model", self._n_model)
        form.addRow("", self._n_baseline)
        form.addRow("Target field", self._n_target)

        fit_button = QPushButton("Fit nutation")
        fit_button.clicked.connect(self._fit_nutation)
        pick_button = QPushButton("Find peak")
        pick_button.clicked.connect(self._pick_nutation_peak)
        export_button = QPushButton("Export record\u2026")
        export_button.clicked.connect(self._export_nutation)
        buttons = QHBoxLayout()
        buttons.addWidget(pick_button)
        buttons.addWidget(fit_button)
        buttons.addWidget(export_button)

        self._n_result = QPlainTextEdit()
        self._n_result.setReadOnly(True)
        self._n_result.setPlaceholderText("Fit results appear here.")

        controls = QWidget()
        controls_layout = QVBoxLayout(controls)
        controls_layout.addLayout(form)
        controls_layout.addLayout(buttons)
        controls_layout.addWidget(QLabel("Result"))
        controls_layout.addWidget(self._n_result, 1)

        self._n_plot = _Plot(residuals=True)
        split = QSplitter(Qt.Horizontal)
        split.addWidget(controls)
        split.addWidget(self._n_plot)
        split.setStretchFactor(1, 1)
        split.setSizes([330, 700])

        page = QWidget()
        page_layout = QVBoxLayout(page)
        page_layout.addWidget(split)
        return page

    def _build_z_tab(self) -> QWidget:
        self._z_centre = QDoubleSpinBox()
        self._z_centre.setRange(-2000.0, 2000.0)
        self._z_centre.setDecimals(4)
        self._z_centre.setSuffix(" ppm")
        self._z_half = QDoubleSpinBox()
        self._z_half.setRange(0.0005, 50.0)
        self._z_half.setDecimals(4)
        self._z_half.setSingleStep(0.01)
        self._z_half.setValue(0.12)
        self._z_half.setSuffix(" ppm")
        self._z_half.setToolTip(
            "Half-width: the window runs centre MINUS this to centre PLUS\n"
            "this, and every row is measured over exactly the same span."
        )
        self._z_span = QLabel("—")
        self._z_span.setToolTip("Resulting window, and how many points it covers.")
        self._z_centre.valueChanged.connect(self._update_z_span)
        self._z_half.valueChanged.connect(self._update_z_span)
        self._z_mode = QComboBox()
        for label, value in (("Peak height", "height"), ("Integral", "integral"),
                             ("Fixed point", "fixed")):
            self._z_mode.addItem(label, value)
        self._z_mode.setToolTip(
            "Peak height is preferred for a Z-spectrum. A saturated peak sits\n"
            "on a drifting baseline that an integral accumulates without\n"
            "bound; on 19F test data repeated reference offsets scattered by\n"
            "0.007 using height against 0.090 using the integral."
        )
        self._z_ref = QDoubleSpinBox()
        self._z_ref.setRange(0.0, 1e6)
        self._z_ref.setDecimals(0)
        self._z_ref.setValue(0.0)
        self._z_ref.setSuffix(" Hz")
        self._z_ref.setSpecialValueText("Automatic")
        self._z_ref.setToolTip(
            "Offsets at least this far out are treated as unsaturated and\n"
            "averaged to give I0. Zero picks them automatically."
        )
        self._z_ppm = QCheckBox("Plot offset in ppm")
        self._z_ppm.setChecked(True)
        self._z_dips = QCheckBox("Fit deepest dip (Lorentzian)")
        self._z_dips.setChecked(True)
        self._z_errors = QCheckBox("Error bars from spectrum noise")
        self._z_errors.setChecked(True)
        self._z_errors.setToolTip(
            "One sigma per point, measured from the signal-free part of that\n"
            "row's own spectrum and propagated through the I0 division."
        )
        self._z_two = QCheckBox("Fit two dips together")
        self._z_two.setToolTip(
            "Fits the deepest dip and the best other candidate at the same\n"
            "time, so the major dip's wings do not bias the minor depth.\n"
            "Reports positions, widths and a depth ratio -- NOT a population."
        )
        self._z_residual = QCheckBox("Subtract fitted dip (reveal second dip)")
        self._z_residual.setToolTip(
            "Direct saturation is far deeper than any exchange feature and\n"
            "its wings hide small dips nearby. Removing the fitted profile\n"
            "flattens them. A display aid for LOCATING a dip, not a\n"
            "quantitative correction."
        )
        self._z_sigma = QDoubleSpinBox()
        self._z_sigma.setRange(1.0, 20.0)
        self._z_sigma.setDecimals(1)
        self._z_sigma.setSingleStep(0.5)
        self._z_sigma.setValue(3.0)
        self._z_sigma.setPrefix("report dips above ")
        self._z_sigma.setSuffix(" sigma")
        self._z_ymin = QDoubleSpinBox()
        self._z_ymin.setRange(-10.0, 10.0)
        self._z_ymin.setDecimals(3)
        self._z_ymin.setSingleStep(0.01)
        self._z_ymax = QDoubleSpinBox()
        self._z_ymax.setRange(-10.0, 10.0)
        self._z_ymax.setDecimals(3)
        self._z_ymax.setSingleStep(0.01)
        # Three decimals because these boxes show ppm as well as Hz, and one
        # decimal cannot express a ppm offset -- it would round the wheel's
        # position away and make the readout disagree with the plot.
        self._z_xmin = QDoubleSpinBox()
        self._z_xmin.setRange(-1e6, 1e6)
        self._z_xmin.setDecimals(3)
        self._z_xmax = QDoubleSpinBox()
        self._z_xmax.setRange(-1e6, 1e6)
        self._z_xmax.setDecimals(3)
        for box in (self._z_xmin, self._z_xmax, self._z_ymin, self._z_ymax):
            box.valueChanged.connect(self._apply_z_limits)
        self._z_full = QPushButton("Full range")
        self._z_full.clicked.connect(self._reset_z_limits)

        form = QFormLayout()
        form.addRow("Peak centre", self._z_centre)
        form.addRow("Half-width (±)", self._z_half)
        form.addRow("Window", self._z_span)
        form.addRow("Measurement", self._z_mode)
        form.addRow("Reference from", self._z_ref)
        form.addRow("", self._z_ppm)
        form.addRow("", self._z_errors)
        form.addRow("", self._z_dips)
        form.addRow("", self._z_two)
        form.addRow("", self._z_residual)
        form.addRow("", self._z_sigma)
        zoom_x = QHBoxLayout()
        zoom_x.addWidget(self._z_xmin)
        zoom_x.addWidget(self._z_xmax)
        form.addRow("X range", zoom_x)
        zoom_y = QHBoxLayout()
        zoom_y.addWidget(self._z_ymin)
        zoom_y.addWidget(self._z_ymax)
        form.addRow("Y range", zoom_y)
        form.addRow("", self._z_full)

        self._overlay_list = QListWidget()
        self._overlay_list.setMaximumHeight(96)
        self._overlay_list.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self._overlay_list.setToolTip(
            "Kept Z-spectra, drawn together with the current one.\n"
            "Each keeps its own I0 normalisation, so experiments with\n"
            "different gain, scans or concentration stay comparable."
        )
        keep_button = QPushButton("Keep on plot")
        keep_button.clicked.connect(self._keep_overlay)
        drop_button = QPushButton("Remove")
        drop_button.clicked.connect(self._drop_overlay)
        clear_button = QPushButton("Clear all")
        clear_button.clicked.connect(self._clear_overlays)
        overlay_buttons = QHBoxLayout()
        overlay_buttons.addWidget(keep_button)
        overlay_buttons.addWidget(drop_button)
        overlay_buttons.addWidget(clear_button)

        build_button = QPushButton("Build Z-spectrum")
        build_button.clicked.connect(self._build_z)
        pick_button = QPushButton("Find peak")
        pick_button.clicked.connect(self._pick_z_peak)
        export_button = QPushButton("Export CSV\u2026")
        export_button.clicked.connect(self._export_z)
        buttons = QHBoxLayout()
        buttons.addWidget(pick_button)
        buttons.addWidget(build_button)
        buttons.addWidget(export_button)

        self._z_result = QPlainTextEdit()
        self._z_result.setReadOnly(True)
        self._z_result.setPlaceholderText("Z-spectrum summary appears here.")

        controls = QWidget()
        controls_layout = QVBoxLayout(controls)
        controls_layout.addLayout(form)
        controls_layout.addLayout(buttons)
        controls_layout.addWidget(QLabel("Result"))
        controls_layout.addWidget(self._z_result, 1)

        self._z_plot = _Plot(wheel_zoom=True)
        self._z_plot.zoomed.connect(self._sync_z_limit_boxes)
        split = QSplitter(Qt.Horizontal)
        split.addWidget(controls)
        split.addWidget(self._z_plot)
        split.setStretchFactor(1, 1)
        split.setSizes([330, 700])

        page = QWidget()
        page_layout = QVBoxLayout(page)
        page_layout.addWidget(split)
        return page

    def _span_text(self, centre: float, half: float) -> str:
        """The window a centre and half-width actually produce.

        Shown because "Half-width 0.12" does not tell you the span is 0.24 ppm
        wide, nor how many points that is -- and the point count is what
        decides whether an integral is averaging noise or accumulating
        baseline.
        """
        low, high = centre - half, centre + half
        if self._data is None:
            return f"{low:+.4f} to {high:+.4f} ppm"
        try:
            lo, hi = window_indices(self._data.ppm, centre, half)
        except CestError:
            return f"{low:+.4f} to {high:+.4f} ppm"
        return f"{low:+.4f} to {high:+.4f} ppm  ({hi - lo} pts)"

    def _update_z_span(self) -> None:
        self._z_span.setText(
            self._span_text(self._z_centre.value(), self._z_half.value())
        )

    def _update_n_span(self) -> None:
        self._n_span.setText(
            self._span_text(self._n_centre.value(), self._n_half.value())
        )

    # ----------------------------------------------------------------- load

    def _choose(self) -> None:                            # pragma: no cover
        directory = QFileDialog.getExistingDirectory(
            self, "Choose a CEST or nutation experiment (the expno folder)"
        )
        if directory:
            self.load(Path(directory))

    def _reload(self) -> None:                            # pragma: no cover
        if self._data is not None:
            self.load(Path(self._data_path))

    def load(self, expno: Path) -> None:
        """Load an experiment and pre-fill both tabs from what it is."""
        expno = Path(expno)
        try:
            data = load_pseudo_2d(expno, prefer=self._source.currentData())
        except Exception as exc:
            self._notes.setPlainText(f"Could not load {expno}: {exc}")
            QMessageBox.warning(self, "CEST", f"Could not load this experiment:\n{exc}")
            return

        self._data = data
        self._data_path = expno
        self._offsets = None
        self._fit = None
        self._z = None

        kind = classify(str(data.acqus.get("PULPROG", "")))
        notes = list(data.notes)
        if kind == "cest":
            try:
                offsets, list_path = read_offsets(expno, data.sfo1_mhz)
                self._offsets = offsets
                if offsets is not None:
                    notes.append(
                        f"{offsets.size} saturation offsets from "
                        f"{list_path.name}: {offsets.min():+.0f} to "
                        f"{offsets.max():+.0f} Hz"
                    )
            except CestError as exc:
                notes.append(f"frequency list unreadable: {exc}")
            if self._offsets is None:
                notes.append(
                    "No FQ1LIST found in this experiment; the Z-spectrum "
                    "cannot be built without saturation offsets."
                )

        self._path_label.setText(
            f"{expno}  \u2014  {data.n_rows} rows from {data.source}, "
            f"PULPROG {str(data.acqus.get('PULPROG', '?')).strip('<>')} "
            f"({kind})"
        )
        self._notes.setPlainText("\n".join(notes) if notes else "No warnings.")

        centre = find_peak_ppm(data.rows[0], data.ppm)
        if kind == "nutation":
            strongest = int(np.argmax(np.abs(data.rows).sum(axis=1)))
            centre = find_peak_ppm(data.rows[strongest], data.ppm)
            self._n_mode.setCurrentIndex(0)
            nominal = nominal_nutation_field(data)
            if nominal > 0:
                self._n_target.setValue(nominal)
        self._n_centre.setValue(centre)
        self._z_centre.setValue(centre)
        self._tabs.setCurrentIndex(0 if kind == "nutation" else 1)

    # ------------------------------------------------------------- nutation

    def _pick_nutation_peak(self) -> None:
        if self._data is None:
            return
        strongest = int(np.argmax(np.abs(self._data.rows).sum(axis=1)))
        self._n_centre.setValue(find_peak_ppm(self._data.rows[strongest], self._data.ppm))

    def _fit_nutation(self) -> None:
        if self._data is None:
            self._n_result.setPlainText("Load an experiment first.")
            return
        data = self._data
        try:
            t = nutation_axis(data)
            y = measure_rows(
                data.rows, data.ppm, self._n_centre.value(), self._n_half.value(),
                mode=self._n_mode.currentData(), baseline=self._n_baseline.isChecked(),
            )
            nominal = nominal_nutation_field(data)
            choice = self._n_model.currentData()
            if choice == "auto":
                plain, damped, use_damped = choose_nutation_model(
                    t, y, nominal_hz=nominal
                )
                seed = damped if use_damped else plain
                reason = (
                    f"damped preferred (AICc {damped.aicc:.1f} vs "
                    f"{plain.aicc:.1f})" if use_damped else
                    f"plain preferred (AICc {plain.aicc:.1f} vs "
                    f"{damped.aicc:.1f})"
                )
            else:
                from ..domain.cest import search_nutation

                seed = search_nutation(
                    t, y, nominal_hz=nominal, damped=(choice == "damped")
                )
                reason = "model chosen manually"
            fit = polish_nutation(t, y, seed)
        except CestError as exc:
            self._n_result.setPlainText(f"Fit failed: {exc}")
            return

        self._fit = fit
        target = self._n_target.value()
        lines = [
            f"B1 (fitted)     {fit.field_hz:.2f}"
            + (f" +/- {fit.field_error_hz:.2f} Hz" if math.isfinite(fit.field_error_hz)
               else " Hz (no error bar: polish did not converge)"),
            f"Nominal (CNST)  {nominal:.2f} Hz"
            + (f"   ratio {fit.field_hz / nominal:.4f}" if nominal else ""),
            f"Model           {'damped sine' if fit.damped else 'plain sine'}"
            f"  \u2014 {reason}",
        ]
        if fit.t2_s:
            lines.append(f"T2 effective    {fit.t2_s * 1e3:.1f} ms")
        lines += [
            f"Residual RMS    {100 * fit.residual_fraction:.2f}% of amplitude",
            f"Points          {fit.n_points}",
            "",
        ]

        plw8 = _get_float(data.acqus, "PLW", 8)
        plw1 = _get_float(data.acqus, "PLW", 1)
        p1 = _get_float(data.acqus, "P", 1)
        if plw8 > 0 and nominal > 0:
            try:
                # Two DIFFERENT factors, kept apart on purpose. The
                # calibration correction (nominal/fitted)^2 is the transferable
                # one: it says how wrong the probe's power calibration is, and
                # multiplies any nominal power on the same probe and tuning.
                # Retuning to a different field additionally scales by
                # (target/nominal)^2, which is not a correction at all.
                # Reporting only their product invites it being applied to a
                # power that was already computed for the target field.
                correction = (nominal / fit.field_hz) ** 2
                at_nominal = corrected_power(plw8, nominal, fit.field_hz)
                ratio = fit.field_hz / nominal
                lines += [
                    f"PLW8 as set     {plw8:.6g} W  (nominal {nominal:.1f} Hz)",
                    f"PLW8 corrected  {at_nominal:.6g} W  for a true "
                    f"{nominal:.1f} Hz"
                    f"   ({power_ratio_db(at_nominal, plw8):+.3f} dB)",
                    "",
                    f"CORRECTION FACTOR  {correction:.4f}"
                    f"   = ({nominal:.1f}/{fit.field_hz:.2f})^2",
                    f"The probe delivers {ratio:.4f}x the nominal field. "
                    "Multiply any NOMINAL power on this probe and tuning by "
                    "the factor above; it transfers to a CEST experiment run "
                    "at a different field, because it corrects the "
                    "calibration rather than the field.",
                ]
                # The spectrometer-ready numbers. Both sequences derive their
                # pulse and power from a CNST, so asking for a LOWER nominal
                # field is the whole adjustment -- and in 19f_cest it is the
                # only one available, because that sequence recomputes plw25
                # unconditionally and overwrites anything typed into it.
                lines += ["", "TO SET ON THE SPECTROMETER", ""]
                lines.append(
                    f"  {'true field':>10}   {'set CNST':>9}   "
                    f"{'-> pulse':>10}   {'-> PLW':>12}"
                )
                wanted = sorted({nominal, target, 30.0, 60.0, 100.0})
                for true_hz in wanted:
                    cnst = true_hz / ratio
                    pulse_us = 1e6 / (4.0 * cnst)
                    marker = "  <-- target" if abs(true_hz - target) < 1e-9 else ""
                    lines.append(
                        f"  {true_hz:>7.1f} Hz   {cnst:>9.2f}   "
                        f"{pulse_us:>7.1f} us   {plw1_for(p1, plw1, pulse_us):>12.6g} W"
                        f"{marker}"
                    )
                lines += [
                    "",
                    f"  Calibration re-check: set CNST8 = {nominal / ratio:.2f} "
                    f"and refit; B1 should come back near {nominal:.1f} Hz.",
                    f"  CEST: set CNST25 = {target / ratio:.2f} for a true "
                    f"{target:.1f} Hz saturation field.",
                    "  Record the TRUE field in the title -- acqus will show "
                    "the lowered CNST, not the field you actually applied.",
                ]
            except CestError as exc:
                lines.append(f"Power correction unavailable: {exc}")
        else:
            lines.append("PLW8 not set in acqus; no power correction possible.")

        if fit.warnings:
            lines += [""] + [f"WARNING: {note}" for note in fit.warnings]
        self._n_result.setPlainText("\n".join(lines))
        self._draw_nutation(t, y, fit)

    def _draw_nutation(self, t, y, fit) -> None:
        from ..domain.cest import nutation_model

        plot = self._n_plot
        plot.clear()
        axes, residual_axes = plot.axes, plot.residual_axes
        axes.plot(t * 1e3, y, "o", ms=4, color="#1b6ca8", label="measured")
        dense = np.linspace(float(t[0]), float(t[-1]), 600)
        axes.plot(
            dense * 1e3,
            nutation_model(dense, fit.offset, fit.amplitude, fit.field_hz,
                           fit.phase_rad, fit.t2_s),
            "-", color="#d1495b", lw=1.5,
            label=("damped sine" if fit.damped else "plain sine"),
        )
        axes.axhline(0.0, color="k", lw=0.5)
        axes.set_ylabel("signal (a.u.)")
        axes.set_title(
            f"B1 = {fit.field_hz:.2f} Hz"
            + (
                f" \u00b1 {fit.field_error_hz:.2f}"
                if math.isfinite(fit.field_error_hz) else ""
            )
            + f"   residual {100 * fit.residual_fraction:.2f}%"
        )
        axes.legend(loc="upper right", frameon=False, fontsize=8)
        residual = y - nutation_model(
            t, fit.offset, fit.amplitude, fit.field_hz, fit.phase_rad, fit.t2_s
        )
        residual_axes.axhline(0.0, color="k", lw=0.5)
        residual_axes.plot(t * 1e3, residual, "o-", ms=3, lw=0.8, color="#777")
        residual_axes.set_xlabel("pulse duration (ms)")
        residual_axes.set_ylabel("resid")
        plot.draw_idle()

    # ------------------------------------------------------------ Z-spectrum

    def _pick_z_peak(self) -> None:
        if self._data is None:
            return
        self._z_centre.setValue(find_peak_ppm(self._data.rows[0], self._data.ppm))

    def _build_z(self) -> None:
        if self._data is None:
            self._z_result.setPlainText("Load an experiment first.")
            return
        if self._offsets is None:
            self._z_result.setPlainText(
                "No saturation offsets available.\n\n"
                "This experiment has no FQ1LIST under lists/f1/. Without it "
                "the row order carries no frequency information and a "
                "Z-spectrum cannot be built."
            )
            return
        data = self._data
        offsets = self._offsets
        rows = data.rows
        partial_note = None
        if rows.shape[0] != offsets.size:
            if rows.shape[0] > offsets.size:
                # More rows than offsets: nothing says which offset each
                # extra row belongs to, so any pairing would be invented.
                self._z_result.setPlainText(
                    f"{rows.shape[0]} rows but only {offsets.size} offsets in "
                    f"the frequency list.\n\n"
                    "There is no way to know which offset the extra rows were "
                    "acquired at, so they cannot be plotted. Check that "
                    f"{'the list is the one this experiment used'}."
                )
                return
            if rows.shape[0] < 2:
                self._z_result.setPlainText(
                    f"Only {rows.shape[0]} row available; a Z-spectrum needs "
                    "at least two.\n\n"
                    "Switch Source to 'Raw ser', which always holds every row."
                )
                return
            # Fewer rows than offsets is recoverable. F1QF steps the list in
            # order, so row i IS offset i -- the pairing stays correct and
            # only the tail is absent. Plotting the rows that exist is more
            # useful than refusing, provided the loss is stated plainly.
            lost = offsets[rows.shape[0]:]
            offsets = offsets[:rows.shape[0]]
            partial_note = (
                f"PARTIAL: {rows.shape[0]} of {self._offsets.size} offsets. "
                f"Missing {lost.min():+.0f} to {lost.max():+.0f} Hz "
                f"({lost.size} offsets). Switch Source to 'Raw ser' for all "
                f"of them."
            )
        try:
            centre, half = self._z_centre.value(), self._z_half.value()
            mode = self._z_mode.currentData()
            intensity = measure_rows(rows, data.ppm, centre, half, mode=mode)
            errors = None
            if self._z_errors.isChecked():
                with contextlib.suppress(CestError):
                    exclude = max(4.0 * half, half + 0.05)
                    sigma = row_noise(rows, data.ppm, centre, exclude)
                    if mode == "integral":
                        # An integral of n points accumulates n independent
                        # noise samples, so its error grows as sqrt(n) --
                        # using the per-point sigma unchanged would understate
                        # it by an order of magnitude on a wide window.
                        lo, hi = window_indices(data.ppm, centre, half)
                        sigma = sigma * math.sqrt(max(hi - lo, 1))
                    errors = sigma
            threshold = self._z_ref.value() or None
            z = normalise_z(
                intensity, offsets, sfo1_mhz=data.sfo1_mhz,
                reference_min_hz=threshold, intensity_error=errors,
            )
        except CestError as exc:
            self._z_result.setPlainText(f"Could not build the Z-spectrum: {exc}")
            return

        self._z = z
        n_ref = int(z.reference_mask.sum())
        lines = []
        if partial_note:
            lines += [partial_note, ""]
        lines += [
            f"Offsets         {z.offsets_hz.size}"
            f"   ({z.offsets_hz.min():+.0f} to {z.offsets_hz.max():+.0f} Hz)",
            f"I0              {z.i0:.4g}   from {n_ref} reference offset"
            + ("s" if n_ref != 1 else ""),
        ]
        if math.isfinite(z.i0_scatter):
            lines.append(
                f"Reference sd    {z.i0_scatter:.4f} in I/I0"
                "   \u2014 this is the noise on every point"
            )
        else:
            lines.append(
                "Reference sd    unavailable (one reference offset only; "
                "two or more give a direct noise estimate)"
            )
        if n_ref > 1 and (
            np.all(z.offsets_hz[z.reference_mask] > 0)
            or np.all(z.offsets_hz[z.reference_mask] < 0)
        ):
            lines.append(
                "  All references lie on ONE side of the carrier, so I0 "
                "carries any baseline tilt across the spectrum."
            )
        lines.append(f"Lowest I/I0     {z.intensity.min():.3f}")
        if z.intensity.min() < -0.05:
            lines.append(
                "  Negative I/I0 is unphysical and indicates baseline drift "
                "in the measurement window \u2014 try Peak height."
            )

        sigma = z.noise
        if math.isfinite(sigma):
            lines.append(f"Noise (1 sigma) {sigma:.4f} in I/I0")

        fitted = None
        display = z
        if self._z_dips.isChecked():
            floor = self._z_sigma.value()
            candidates = dip_candidates(z, min_sigma=floor)
            if not candidates:
                lines += ["", f"No dip reaching {floor:.1f} sigma "
                              f"({floor * sigma:.4f} in I/I0)."]
            else:
                try:
                    fitted = fit_dip(z, candidates[0][0])
                    lines += [
                        "",
                        f"Dip centre      {fitted.centre_hz:+.1f} "
                        f"\u00b1 {fitted.centre_error_hz:.1f} Hz"
                        f"   = {fitted.centre_ppm:+.4f} ppm from the carrier",
                        f"Dip depth       {fitted.depth:.3f}",
                        f"Dip FWHM        {fitted.width_hz:.0f} Hz "
                        f"({fitted.width_ppm:.3f} ppm)",
                    ]
                    if abs(fitted.centre_hz) < 2.0 * fitted.width_hz:
                        lines.append(
                            "  This dip sits on the carrier, so it is most "
                            "likely DIRECT saturation of the observed "
                            "resonance rather than exchange."
                        )
                except CestError as exc:
                    lines += ["", f"Dip fit failed: {exc}"]

            # Everything else that clears the threshold, with its
            # significance, so a candidate can be judged rather than guessed
            # at. This is the list to look at when hunting a bound state.
            others = [c for c in candidates
                      if fitted is None
                      or abs(c[0] - fitted.centre_hz) > max(fitted.width_hz, 1.0)]
            if others:
                lines += ["", f"OTHER CANDIDATE DIPS ({len(others)})",
                          "   offset        depth   significance"]
                for offset_hz, depth, significance in others[:8]:
                    lines.append(
                        f"  {offset_hz:+8.0f} Hz  {depth:7.4f}   "
                        f"{significance:5.1f} sigma"
                        f"   ({offset_hz / z.sfo1_mhz:+.3f} ppm)"
                    )
                if fitted is not None:
                    lines.append(
                        "  Tick 'Subtract fitted dip' to flatten the direct-"
                        "saturation wings under these."
                    )
            elif fitted is not None:
                lines += ["", "No other candidate above the threshold. Lower "
                              "the sigma setting, or improve the noise with "
                              "more scans, to look deeper."]

        if (
            self._z_two.isChecked()
            and fitted is not None
            and self._z_dips.isChecked()
        ):
            others = [c for c in dip_candidates(z, min_sigma=self._z_sigma.value())
                      if abs(c[0] - fitted.centre_hz) > max(fitted.width_hz, 1.0)]
            if not others:
                lines += ["", "Two-dip fit skipped: no second candidate above "
                              "the threshold."]
            else:
                try:
                    pair = fit_two_dips(z, fitted.centre_hz, others[0][0])
                    d_major, c_major, w_major = pair.major
                    d_minor, c_minor, w_minor = pair.minor
                    lines += [
                        "",
                        "TWO-DIP FIT",
                        f"  major   {c_major:+8.1f} Hz "
                        f"({c_major / z.sfo1_mhz:+.4f} ppm)"
                        f"   depth {d_major:.4f}   fwhm {w_major:.0f} Hz",
                        f"  minor   {c_minor:+8.1f} Hz "
                        f"({c_minor / z.sfo1_mhz:+.4f} ppm)"
                        f"   depth {d_minor:.4f}   fwhm {w_minor:.0f} Hz",
                        f"  separation  {pair.separation_hz:+.1f} Hz "
                        f"= {pair.separation_ppm:+.4f} ppm",
                        f"  minor share of total depth  "
                        f"{100 * pair.minor_fraction:.1f}%",
                        f"  fit residual {pair.residual_rms:.4f} "
                        f"({pair.residual_rms / sigma:.1f} sigma)"
                        if math.isfinite(sigma) and sigma > 0 else
                        f"  fit residual {pair.residual_rms:.4f}",
                        "",
                        "  The percentage is a DEPTH RATIO, not a populated "
                        "fraction. Dip depth depends on the exchange rate, "
                        "the saturation field, D18 and both states' "
                        "relaxation as well as on population, so a small "
                        "population in fast exchange can dig deeper than a "
                        "larger one in slow exchange. It is reproducible and "
                        "comparable between experiments run under IDENTICAL "
                        "conditions -- a titration or a control series -- "
                        "but a real population needs Bloch-McConnell fitting "
                        "against several saturation fields.",
                    ]
                except CestError as exc:
                    lines += ["", f"Two-dip fit failed: {exc}"]

        if self._z_residual.isChecked() and fitted is not None:
            display = remove_dip(z, fitted.baseline, fitted.depth,
                                 fitted.centre_hz, fitted.width_hz)
            lines += ["", "SHOWING RESIDUAL after removing the fitted dip. "
                          "Depths here are relative to the flattened "
                          "baseline; the fitted dip itself is gone by "
                          "construction."]

        self._z_result.setPlainText("\n".join(lines))
        self._seed_z_limits(display)
        self._draw_z(display, None if display is not z else fitted)

    def _draw_z(self, z, fitted) -> None:
        from ..domain.cest import lorentzian_dip

        plot = self._z_plot
        plot.clear()
        axes = plot.axes
        use_ppm = self._z_ppm.isChecked()
        x = z.offsets_ppm if use_ppm else z.offsets_hz
        interior = ~z.reference_mask
        errors = z.error

        # Kept spectra first, so the current one draws on top of them.
        for index, (name, kept) in enumerate(self._overlays):
            kx = kept.offsets_ppm if use_ppm else kept.offsets_hz
            keep_interior = ~kept.reference_mask
            axes.plot(
                kx[keep_interior], kept.intensity[keep_interior],
                "-", lw=1.0, alpha=0.75,
                color=OVERLAY_COLOURS[index % len(OVERLAY_COLOURS)],
                label=name,
            )

        current_label = "current" if self._overlays else "I/I$_0$"
        if errors is not None and np.any(np.isfinite(errors)):
            axes.errorbar(
                x[interior], z.intensity[interior],
                yerr=errors[interior], fmt="o-", ms=4, lw=1.0, elinewidth=0.9,
                capsize=2, color="#111111", ecolor="#111111",
                label=current_label,
            )
        else:
            axes.plot(x[interior], z.intensity[interior], "o-", ms=4, lw=1.0,
                      color="#111111", label=current_label)
        if z.reference_mask.any():
            axes.plot(x[z.reference_mask], z.intensity[z.reference_mask], "s",
                      ms=6, color="#e0a458", label="I$_0$ reference")
        if fitted is not None:
            dense = np.linspace(float(x[interior].min()), float(x[interior].max()), 500)
            centre = fitted.centre_ppm if use_ppm else fitted.centre_hz
            width = fitted.width_ppm if use_ppm else fitted.width_hz
            axes.plot(dense, lorentzian_dip(dense, fitted.baseline, fitted.depth,
                                            centre, width),
                      "-", color="#d1495b", lw=1.4, label="Lorentzian dip")
        # A one-sigma band makes a marginal dip readable at a glance: a point
        # dipping below it is worth a second look, one inside it is not.
        sigma = z.noise
        if not self._overlays and math.isfinite(sigma) and sigma > 0:
            baseline = float(np.median(z.intensity[interior]))
            axes.axhspan(baseline - sigma, baseline + sigma,
                         color="#999", alpha=0.15, lw=0, label="\u00b11 sigma")
        axes.axhline(1.0, color="k", lw=0.5, ls=":")
        axes.axvline(0.0, color="k", lw=0.5, ls=":")
        # NMR convention: frequency increases to the LEFT.
        axes.invert_xaxis()
        axes.set_xlabel(
            "saturation offset from carrier (ppm)" if use_ppm
            else "saturation offset from carrier (Hz)"
        )
        axes.set_ylabel("I / I$_0$")
        axes.legend(loc="lower right", frameon=False,
                    fontsize=7 if self._overlays else 8)
        self._z_axes_ready = True
        self._apply_z_limits()
        plot.draw_idle()

    def _seed_z_limits(self, z) -> None:
        """Fill the range boxes from the data without triggering a redraw loop.

        Seeded rather than left at zero because a range control whose default
        is meaningless has to be discovered before it can be used. Signals are
        blocked while setting: each setValue would otherwise fire
        valueChanged, and four of those would redraw the plot four times.
        """
        use_ppm = self._z_ppm.isChecked()
        xs = [z.offsets_ppm if use_ppm else z.offsets_hz]
        ys = [z.intensity]
        for _, kept in self._overlays:
            xs.append(kept.offsets_ppm if use_ppm else kept.offsets_hz)
            ys.append(kept.intensity)
        x = np.concatenate(xs)
        y = np.concatenate(ys)
        pad_y = 0.05 * (float(np.ptp(y)) or 1.0)
        for box, value in (
            (self._z_xmin, float(np.min(x))), (self._z_xmax, float(np.max(x))),
            (self._z_ymin, float(np.min(y)) - pad_y),
            (self._z_ymax, float(np.max(y)) + pad_y),
        ):
            blocked = box.blockSignals(True)
            box.setValue(value)
            box.blockSignals(blocked)

    def _apply_z_limits(self) -> None:
        """Apply the range boxes, ignoring an empty or inverted range."""
        if not getattr(self, "_z_axes_ready", False):
            return
        axes = self._z_plot.axes
        lo_x, hi_x = self._z_xmin.value(), self._z_xmax.value()
        if hi_x > lo_x:
            # The axis is inverted for NMR convention, so high ppm goes on
            # the left; set_xlim must be given the pair in that order or the
            # inversion is silently undone.
            axes.set_xlim(hi_x, lo_x)
        lo_y, hi_y = self._z_ymin.value(), self._z_ymax.value()
        if hi_y > lo_y:
            axes.set_ylim(lo_y, hi_y)
        self._z_plot.draw_idle()

    def _sync_z_limit_boxes(self) -> None:
        """Write the axes' limits back into the range boxes after a wheel zoom.

        Without this the boxes still show the pre-zoom numbers, and the next
        keystroke in one of them would snap the view back to where the wheel
        started. Signals are blocked so writing them does not re-apply and
        fight the zoom that just happened.
        """
        low_x, high_x = self._z_plot.axes.get_xlim()
        low_y, high_y = self._z_plot.axes.get_ylim()
        # x is displayed inverted, so the axis reports (high, low).
        pairs = (
            (self._z_xmin, min(low_x, high_x)), (self._z_xmax, max(low_x, high_x)),
            (self._z_ymin, min(low_y, high_y)), (self._z_ymax, max(low_y, high_y)),
        )
        for box, value in pairs:
            blocked = box.blockSignals(True)
            box.setValue(value)
            box.blockSignals(blocked)

    def _keep_overlay(self) -> None:
        """Add the current Z-spectrum to the overlay set."""
        if self._z is None:
            self._z_result.setPlainText("Build a Z-spectrum before keeping it.")
            return
        name = Path(self._data_path).parent.name or str(self._data_path)
        label = f"{name}/{Path(self._data_path).name}"
        # A field and duration suffix, because the usual reason to overlay is
        # a power or saturation-time series and the expno alone does not say
        # which is which.
        field = _get_float(self._data.acqus, "CNST", 25)
        d18 = _get_float(self._data.acqus, "D", 18)
        if field > 0:
            label += f"  {field:.0f} Hz"
        if d18 > 0:
            label += f" / {d18:g} s"
        if any(existing == label for existing, _ in self._overlays):
            label = f"{label} ({len(self._overlays) + 1})"
        self._overlays.append((label, self._z))
        self._overlay_list.addItem(QListWidgetItem(label))
        self._build_z()

    def _drop_overlay(self) -> None:
        for item in self._overlay_list.selectedItems():
            row = self._overlay_list.row(item)
            self._overlay_list.takeItem(row)
            del self._overlays[row]
        self._build_z()

    def _clear_overlays(self) -> None:
        self._overlays.clear()
        self._overlay_list.clear()
        self._build_z()

    def _on_z_scroll(self, event) -> None:
        """Wheel zooms X about the cursor; shift-wheel zooms Y.

        Anchored on the pointer rather than the axis centre, so the feature
        being examined stays under the cursor instead of sliding away -- the
        behaviour every plotting tool has trained the hand for.
        """
        axes = self._z_plot.axes
        if event.inaxes is not axes or not event.step:
            return
        factor = 0.85 ** event.step
        shift = bool(getattr(event, "key", None) and "shift" in str(event.key))
        if shift:
            anchor = event.ydata
            low, high = axes.get_ylim()
            if anchor is None:
                return
            axes.set_ylim(anchor + (low - anchor) * factor,
                          anchor + (high - anchor) * factor)
        else:
            anchor = event.xdata
            low, high = axes.get_xlim()
            if anchor is None:
                return
            # The X axis is INVERTED for NMR convention, so low > high here.
            # Scaling both ends about the anchor preserves that ordering;
            # sorting them would silently flip the spectrum.
            axes.set_xlim(anchor + (low - anchor) * factor,
                          anchor + (high - anchor) * factor)
        self._sync_z_boxes()
        self._z_plot.draw_idle()

    def _on_z_click(self, event) -> None:
        """Double-click anywhere on the plot returns to the full range."""
        if getattr(event, "dblclick", False):
            self._reset_z_limits()

    def _sync_z_boxes(self) -> None:
        """Make the range boxes follow the wheel, without redrawing again."""
        axes = self._z_plot.axes
        high_x, low_x = axes.get_xlim()      # inverted axis
        low_y, high_y = axes.get_ylim()
        for box, value in (
            (self._z_xmin, low_x), (self._z_xmax, high_x),
            (self._z_ymin, low_y), (self._z_ymax, high_y),
        ):
            blocked = box.blockSignals(True)
            box.setValue(float(value))
            box.blockSignals(blocked)

    def _reset_z_limits(self) -> None:
        """Back to the full data range."""
        if self._z is None:
            return
        display = self._z
        self._seed_z_limits(display)
        self._apply_z_limits()

    # --------------------------------------------------------------- export

    def nutation_record(self) -> str:
        """Part 8.1 record sheet as text."""
        if self._data is None or self._fit is None:
            return ""
        data, fit = self._data, self._fit
        nominal = nominal_nutation_field(data)
        try:
            plw8 = float(data.acqus.get("PLW", [])[8])
        except (IndexError, TypeError, ValueError):
            plw8 = 0.0
        target = self._n_target.value()
        rows = [
            ("Experiment", str(self._data_path)),
            ("Pulse programme", str(data.acqus.get("PULPROG", "")).strip("<>")),
            ("Data source", data.source),
            ("Rows", f"{data.n_rows}"),
            ("P1 / PLW1", f"{_get(data.acqus, 'P', 1)} us / "
                          f"{_get(data.acqus, 'PLW', 1)} W"),
            ("Nominal field (CNST8)", f"{nominal:.2f} Hz"),
            ("Nominal PLW8", f"{plw8:.6g} W"),
            ("Measurement", f"{self._n_mode.currentText()}, "
                            f"{self._n_centre.value():.4f} "
                            f"+/- {self._n_half.value():.4f} ppm"),
            ("Model", "damped sine" if fit.damped else "plain sine"),
            ("Fitted B1", f"{fit.field_hz:.3f} Hz"
                          + (f" +/- {fit.field_error_hz:.3f}"
                             if math.isfinite(fit.field_error_hz) else "")),
            ("Residual RMS", f"{100 * fit.residual_fraction:.2f}% of amplitude"),
            ("Target field", f"{target:.2f} Hz"),
        ]
        if plw8 > 0 and nominal > 0:
            ratio = fit.field_hz / nominal
            rows += [
                ("Probe delivers", f"{ratio:.4f} x nominal"),
                ("Correction factor", f"{(nominal / fit.field_hz) ** 2:.4f}"),
                ("SET CNST for a true "
                 f"{nominal:.0f} Hz", f"{nominal / ratio:.2f}"),
                ("SET CNST for a true "
                 f"{target:.0f} Hz", f"{target / ratio:.2f}"),
            ]
            with contextlib.suppress(CestError):
                rows += [
                    ("PLW8 corrected to nominal",
                     f"{corrected_power(plw8, nominal, fit.field_hz):.6g} W"),
                    ("PLW8 for target field",
                     f"{corrected_power(plw8, target, fit.field_hz):.6g} W"),
                ]
        width = max(len(name) for name, _ in rows)
        return "\n".join(f"{name.ljust(width)}  {value}" for name, value in rows)

    def _export_nutation(self) -> None:                   # pragma: no cover
        text = self.nutation_record()
        if not text:
            QMessageBox.information(self, "CEST", "Fit a nutation series first.")
            return
        name, _ = QFileDialog.getSaveFileName(
            self, "Save calibration record", "cest_calibration.txt",
            "Text (*.txt);;All files (*)"
        )
        if name:
            Path(name).write_text(text + "\n", encoding="utf-8")

    def z_csv(self) -> str:
        """Z-spectrum as CSV, offsets in both Hz and ppm."""
        if self._z is None:
            return ""
        z = self._z
        lines = ["offset_hz,offset_ppm,intensity_over_i0,is_reference"]
        for hz, ppm, value, reference in zip(
            z.offsets_hz, z.offsets_ppm, z.intensity, z.reference_mask, strict=True
        ):
            lines.append(f"{hz:.4f},{ppm:.6f},{value:.6f},{int(bool(reference))}")
        return "\n".join(lines)

    def _export_z(self) -> None:                          # pragma: no cover
        text = self.z_csv()
        if not text:
            QMessageBox.information(self, "CEST", "Build a Z-spectrum first.")
            return
        name, _ = QFileDialog.getSaveFileName(
            self, "Save Z-spectrum", "z_spectrum.csv", "CSV (*.csv);;All files (*)"
        )
        if name:
            Path(name).write_text(text + "\n", encoding="utf-8")


def plw1_for(p1_us: float, plw1_w: float, pulse_us: float) -> float:
    """plw = plw1*(p1/pulse)^2 -- the relation both pulse programs use.

    19f_calib_nut computes plw8 this way from p8, and 19f_cest computes
    plw25 from p25. Reproducing it lets the panel show exactly what the
    spectrometer will derive from a given CNST, so the value can be checked
    in eda rather than taken on trust.
    """
    if pulse_us <= 0 or p1_us <= 0 or plw1_w <= 0:
        return float("nan")
    return plw1_w * (p1_us / pulse_us) ** 2


def _get_float(acqus: dict, key: str, index: int) -> float:
    """A numeric acqus array entry, 0.0 when absent or unparsable."""
    try:
        return float(acqus.get(key, [])[index])
    except (IndexError, TypeError, ValueError, KeyError):
        return 0.0


def _get(acqus: dict, key: str, index: int) -> str:
    try:
        return str(acqus.get(key, [])[index])
    except (IndexError, TypeError, ValueError, KeyError):
        return "?"
