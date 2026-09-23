"""ISA-101 PID Faceplate adapted for fired_heater_sim.

Classic industrial HMI faceplate with vertical scale bar, SP/PV/OUT
display, mode buttons (AUTO/MAN/CAS), and tuning/trend buttons.
Connected to ``SharedDataStore`` for live updates and write-back.
"""
from __future__ import annotations

from PySide6.QtCore import Qt, QPointF, QTimer, Signal
from PySide6.QtGui import QBrush, QColor, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import (
    QGridLayout, QFrame, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QScrollArea, QSizePolicy, QVBoxLayout, QWidget,
)

from azeo_control_trainer.core.presentation.user_role import role_manager
from azeo_control_trainer.config.units import (
    K_to_F, F_to_K, Pa_to_inH2O, inH2O_to_Pa,
    kgs_to_bpd, bpd_to_kgs, kgs_to_mmscfd_fuel, mmscfd_to_kgs_fuel,
    kgs_to_mmscfd_air, mmscfd_to_kgs_air,
    TEMP_UNIT, PRESS_UNIT, FEED_FLOW_UNIT, GAS_FLOW_UNIT,
)


def _identity(x):
    return x


# Per-controller display configuration
CONTROLLER_DISPLAY = {
    'TIC101': {
        'description': 'COT Temperature',
        'pv_min': 500.0, 'pv_max': 800.0,
        'pv_unit': TEMP_UNIT,
        'si_to_display': K_to_F, 'display_to_si': F_to_K,
    },
    'FIC101': {
        'description': 'Total Feed Flow',
        'pv_min': 0.0, 'pv_max': 200_000.0,
        'pv_unit': FEED_FLOW_UNIT,
        'si_to_display': kgs_to_bpd, 'display_to_si': bpd_to_kgs,
    },
    'FIC102': {
        'description': 'Fuel Flow',
        'pv_min': 0.0, 'pv_max': 5.0,
        'pv_unit': GAS_FLOW_UNIT,
        'si_to_display': kgs_to_mmscfd_fuel, 'display_to_si': mmscfd_to_kgs_fuel,
    },
    'FIC103': {
        'description': 'Air Flow',
        'pv_min': 0.0, 'pv_max': 20.0,
        'pv_unit': GAS_FLOW_UNIT,
        'si_to_display': kgs_to_mmscfd_air, 'display_to_si': mmscfd_to_kgs_air,
    },
    'PIC101': {
        'description': 'Firebox Draft',
        'pv_min': -2.0, 'pv_max': 0.0,
        'pv_unit': PRESS_UNIT,
        'si_to_display': Pa_to_inH2O, 'display_to_si': inH2O_to_Pa,
    },
    'AIC101': {
        'description': 'Stack O2 Trim',
        'pv_min': 0.0, 'pv_max': 10.0,
        'pv_unit': '%',
        'si_to_display': _identity, 'display_to_si': _identity,
    },
    'AIC102': {
        'description': 'CO Override',
        'pv_min': 0.0, 'pv_max': 1000.0,
        'pv_unit': 'ppm',
        'si_to_display': _identity, 'display_to_si': _identity,
    },
    'FIC101A': {
        'description': 'Pass 1 Feed',
        'pv_min': 0.0, 'pv_max': 50_000.0,
        'pv_unit': FEED_FLOW_UNIT,
        'si_to_display': kgs_to_bpd, 'display_to_si': bpd_to_kgs,
    },
    'FIC101B': {
        'description': 'Pass 2 Feed',
        'pv_min': 0.0, 'pv_max': 50_000.0,
        'pv_unit': FEED_FLOW_UNIT,
        'si_to_display': kgs_to_bpd, 'display_to_si': bpd_to_kgs,
    },
    'FIC101C': {
        'description': 'Pass 3 Feed',
        'pv_min': 0.0, 'pv_max': 50_000.0,
        'pv_unit': FEED_FLOW_UNIT,
        'si_to_display': kgs_to_bpd, 'display_to_si': bpd_to_kgs,
    },
    'FIC101D': {
        'description': 'Pass 4 Feed',
        'pv_min': 0.0, 'pv_max': 50_000.0,
        'pv_unit': FEED_FLOW_UNIT,
        'si_to_display': kgs_to_bpd, 'display_to_si': bpd_to_kgs,
    },
    'PIC102': {
        'description': 'Fuel Gas Pressure',
        'pv_min': 0.0, 'pv_max': 100.0,
        'pv_unit': PRESS_UNIT,
        'si_to_display': Pa_to_inH2O, 'display_to_si': inH2O_to_Pa,
    },
}


class ScaleBarWidget(QWidget):
    """Classic vertical scale bar with SP/PV/OUT indicators."""

    valueChanged = Signal(float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._sp = 50.0
        self._pv = 50.0
        self._out = 50.0
        self._min = 0.0
        self._max = 100.0
        self._dragging = False
        self.setMinimumHeight(100)
        self.setMouseTracking(True)
        self.setCursor(Qt.PointingHandCursor)

    def setSP(self, v): self._sp = v; self.update()
    def setPV(self, v): self._pv = v; self.update()
    def setOutput(self, v): self._out = v; self.update()
    def setRange(self, lo, hi): self._min = lo; self._max = hi; self.update()

    def _val_to_y(self, v):
        h = self.height()
        margin = 10
        top, bot = margin, h - margin
        if self._max - self._min < 1e-9:
            return (top + bot) / 2
        frac = max(0.0, min(1.0, (v - self._min) / (self._max - self._min)))
        return bot - frac * (bot - top)

    def _y_to_val(self, y):
        h = self.height()
        margin = 10
        top, bot = margin, h - margin
        if bot - top < 1:
            return (self._min + self._max) / 2
        frac = max(0.0, min(1.0, (bot - y) / (bot - top)))
        return self._min + frac * (self._max - self._min)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        margin = 10
        bl, br = 18, w - 18
        bt, bb = margin, h - margin
        bw, bh = br - bl, bb - bt

        # Background bar
        p.setPen(QPen(QColor("#404040"), 2))
        p.setBrush(QColor("#FFFFFF"))
        p.drawRect(bl, bt, bw, bh)

        # Output fill
        out_frac = max(0.0, min(1.0, self._out / 100.0))
        oh = int(out_frac * bh)
        if oh > 0:
            p.setPen(Qt.NoPen)
            p.setBrush(QColor("#60C060"))
            p.drawRect(bl + 1, bb - oh, bw - 2, oh)

        # Tick marks
        p.setPen(QPen(QColor("#000000"), 1))
        for i in range(11):
            frac = i / 10.0
            y = bb - frac * bh
            tl = 5 if i % 5 == 0 else 3
            p.drawLine(bl, int(y), bl + tl, int(y))
            p.drawLine(br - tl, int(y), br, int(y))

        # PV pointer (cyan, right)
        pv_y = self._val_to_y(self._pv)
        self._draw_ptr(p, br + 3, pv_y, "left", "#00C0C0", "#008080")

        # SP pointer (yellow, left)
        sp_y = self._val_to_y(self._sp)
        self._draw_ptr(p, bl - 3, sp_y, "right", "#FFFF00", "#C0C000")

        # SP dashed line
        p.setPen(QPen(QColor("#C0A000"), 2, Qt.DashLine))
        p.drawLine(bl + 2, int(sp_y), br - 2, int(sp_y))

    def _draw_ptr(self, p, x, y, direction, fill, border):
        sz = 8
        if direction == "right":
            pts = [QPointF(x, y), QPointF(x - sz, y - sz / 1.4), QPointF(x - sz, y + sz / 1.4)]
        else:
            pts = [QPointF(x, y), QPointF(x + sz, y - sz / 1.4), QPointF(x + sz, y + sz / 1.4)]
        p.setPen(QPen(QColor(border), 1))
        p.setBrush(QColor(fill))
        p.drawPolygon(QPolygonF(pts))

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._dragging = True
            self._update_from_mouse(e.pos().y())

    def mouseMoveEvent(self, e):
        if self._dragging:
            self._update_from_mouse(e.pos().y())

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._dragging = False

    def _update_from_mouse(self, y):
        v = max(self._min, min(self._max, self._y_to_val(y)))
        self._sp = v
        self.update()
        self.valueChanged.emit(v)


class FiredHeaterFaceplate(QWidget):
    """ISA-101 faceplate for a single fired heater PID controller."""

    spChanged = Signal(str, float)
    outputChanged = Signal(str, float)
    modeChanged = Signal(str, str)
    tuningRequested = Signal(str)
    trendRequested = Signal(str)
    historianRequested = Signal(str)
    helpRequested = Signal(str)

    def __init__(self, tag: str, store=None, parent=None):
        super().__init__(parent)
        self._tag = tag
        self._store = store
        self._config = CONTROLLER_DISPLAY.get(tag, CONTROLLER_DISPLAY['TIC101'])
        self._mode = "AUTO"
        self._sp = 0.0
        self._pv = 0.0
        self._out = 0.0

        self.setFixedWidth(260)
        self.setMinimumHeight(350)
        self.setMaximumHeight(420)

        self._setup_ui()
        self._update_display()

        self._timer = None
        if store:
            self._timer = QTimer(self)
            self._timer.setInterval(500)
            self._timer.timeout.connect(self._poll_store)
            self._timer.start()

    def closeEvent(self, event):
        if self._timer:
            self._timer.stop()
        super().closeEvent(event)

    def _setup_ui(self):
        self.setStyleSheet(
            "FiredHeaterFaceplate { background: #D4D4D4; "
            "border: 1px solid #505050; border-radius: 3px; }")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(5, 5, 5, 5)
        layout.setSpacing(3)

        # Header
        hdr = QFrame()
        hdr.setStyleSheet(
            "QFrame { background: #C8C8C8; border: 1px solid #A0A0A0; border-radius: 2px; }")
        hl = QVBoxLayout(hdr)
        hl.setContentsMargins(4, 2, 4, 2)
        hl.setSpacing(0)
        self._tag_lbl = QLabel(self._tag)
        self._tag_lbl.setAlignment(Qt.AlignCenter)
        self._tag_lbl.setStyleSheet(
            "font-weight: bold; font-size: 11pt; color: #000080; background: transparent;")
        hl.addWidget(self._tag_lbl)
        self._desc_lbl = QLabel(self._config['description'])
        self._desc_lbl.setAlignment(Qt.AlignCenter)
        self._desc_lbl.setStyleSheet(
            "font-size: 8pt; color: #4A4A4A; background: transparent;")
        hl.addWidget(self._desc_lbl)
        layout.addWidget(hdr)

        # Value display: SP | PV | OUT
        val_frame = QFrame()
        val_frame.setStyleSheet(
            "QFrame { background: #C0C0C0; border: 1px solid #A0A0A0; border-radius: 2px; }")
        vl = QHBoxLayout(val_frame)
        vl.setContentsMargins(3, 3, 3, 3)
        vl.setSpacing(3)

        pv_unit = self._config['pv_unit']
        self._sp_box = self._make_value_box("SP", "#FFFF80", "#C0C060", pv_unit)
        self._pv_box = self._make_value_box("PV", "#80FFFF", "#60C0C0", pv_unit)
        self._out_box = self._make_value_box("OUT", "#80FF80", "#60C060", "%")
        for lbl_title, box in [(None, self._sp_box), (None, self._pv_box), (None, self._out_box)]:
            vl.addWidget(box[2])  # container widget
        layout.addWidget(val_frame)

        # Main area: mode buttons | scale bar | entry fields
        main = QHBoxLayout()
        main.setSpacing(4)

        # Mode buttons
        mode_frame = QFrame()
        mode_frame.setStyleSheet(
            "QFrame { background: #C8C8C8; border: 1px solid #A0A0A0; border-radius: 2px; }")
        ml = QVBoxLayout(mode_frame)
        ml.setContentsMargins(3, 3, 3, 3)
        ml.setSpacing(3)

        self._btn_auto = QPushButton("AUTO")
        self._btn_auto.setFixedSize(50, 26)
        self._btn_auto.clicked.connect(lambda: self._set_mode("AUTO"))
        ml.addWidget(self._btn_auto)

        self._btn_man = QPushButton("MAN")
        self._btn_man.setFixedSize(50, 26)
        self._btn_man.clicked.connect(lambda: self._set_mode("MAN"))
        ml.addWidget(self._btn_man)

        self._btn_cas = QPushButton("CAS")
        self._btn_cas.setFixedSize(50, 26)
        self._btn_cas.clicked.connect(lambda: self._set_mode("CAS"))
        ml.addWidget(self._btn_cas)

        self._mode_ind = QLabel("AUTO")
        self._mode_ind.setAlignment(Qt.AlignCenter)
        self._mode_ind.setFixedSize(50, 20)
        ml.addWidget(self._mode_ind)

        ml.addStretch()
        main.addWidget(mode_frame)

        # Scale bar
        self._scale = ScaleBarWidget()
        self._scale.setFixedWidth(65)
        self._scale.setMinimumHeight(160)
        self._scale.valueChanged.connect(self._on_scale_sp)
        main.addWidget(self._scale, 1)

        # Right column: scale labels + entry
        right = QFrame()
        right.setStyleSheet(
            "QFrame { background: #C8C8C8; border: 1px solid #A0A0A0; border-radius: 2px; }")
        rl = QVBoxLayout(right)
        rl.setContentsMargins(3, 3, 3, 3)
        rl.setSpacing(2)

        self._max_lbl = QLabel(self._fmt_pv(self._config['pv_max']))
        self._max_lbl.setAlignment(Qt.AlignCenter)
        self._max_lbl.setStyleSheet(
            "font-size: 8pt; font-weight: bold; font-family: Consolas; "
            "color: #1A1A1A; background: transparent;")
        rl.addWidget(self._max_lbl)

        self._unit_lbl = QLabel(self._config['pv_unit'])
        self._unit_lbl.setAlignment(Qt.AlignCenter)
        self._unit_lbl.setStyleSheet(
            "font-size: 7pt; color: #4A4A4A; background: transparent;")
        rl.addWidget(self._unit_lbl)

        rl.addStretch()

        sp_lbl = QLabel("SP:")
        sp_lbl.setStyleSheet(
            "font-size: 8pt; font-weight: bold; color: #806000; background: transparent;")
        rl.addWidget(sp_lbl)
        self._sp_entry = QLineEdit("0.00")
        self._sp_entry.setFixedSize(72, 22)
        self._sp_entry.setAlignment(Qt.AlignRight)
        self._sp_entry.setStyleSheet(
            "QLineEdit { background: #FFFFC0; border: 1px solid #A0A0A0; font-size: 9pt; "
            "font-family: Consolas; color: #000000; padding: 1px; }"
            "QLineEdit:focus { border: 2px solid #4169E1; }")
        self._sp_entry.editingFinished.connect(self._on_sp_entered)
        rl.addWidget(self._sp_entry)

        co_lbl = QLabel("CO%:")
        co_lbl.setStyleSheet(
            "font-size: 8pt; font-weight: bold; color: #006000; background: transparent;")
        rl.addWidget(co_lbl)
        self._co_entry = QLineEdit("0.0")
        self._co_entry.setFixedSize(72, 22)
        self._co_entry.setAlignment(Qt.AlignRight)
        self._co_entry.setStyleSheet(
            "QLineEdit { background: #C0FFC0; border: 1px solid #A0A0A0; font-size: 9pt; "
            "font-family: Consolas; color: #000000; padding: 1px; }"
            "QLineEdit:focus { border: 2px solid #4169E1; }"
            "QLineEdit:disabled { background: #E8E8E8; color: #808080; }")
        self._co_entry.editingFinished.connect(self._on_co_entered)
        rl.addWidget(self._co_entry)

        rl.addStretch()

        self._min_lbl = QLabel(self._fmt_pv(self._config['pv_min']))
        self._min_lbl.setAlignment(Qt.AlignCenter)
        self._min_lbl.setStyleSheet(
            "font-size: 8pt; font-weight: bold; font-family: Consolas; "
            "color: #1A1A1A; background: transparent;")
        rl.addWidget(self._min_lbl)

        main.addWidget(right)
        layout.addLayout(main, 1)

        # Bottom buttons
        btn_row = QHBoxLayout()
        btn_row.setSpacing(4)
        self._btn_tuning = self._make_bottom_btn("Tuning")
        self._btn_tuning.setToolTip("Open PID tuning dialog (Admin only)")
        self._btn_tuning.clicked.connect(lambda: self.tuningRequested.emit(self._tag))
        self._btn_tuning.setEnabled(role_manager.is_admin)
        btn_row.addWidget(self._btn_tuning)
        self._btn_trend = self._make_bottom_btn("Trend")
        self._btn_trend.setToolTip("Open real-time trend of PV, SP, OUT")
        self._btn_trend.clicked.connect(lambda: self.trendRequested.emit(self._tag))
        btn_row.addWidget(self._btn_trend)
        self._btn_hist = self._make_bottom_btn("Hist")
        self._btn_hist.setToolTip("Open in Historian")
        self._btn_hist.clicked.connect(lambda: self.historianRequested.emit(self._tag))
        btn_row.addWidget(self._btn_hist)
        self._btn_help = self._make_bottom_btn("?")
        self._btn_help.setFixedWidth(24)
        self._btn_help.setToolTip("Open Process Reference (F1)")
        self._btn_help.clicked.connect(self._on_help_clicked)
        btn_row.addWidget(self._btn_help)
        layout.addLayout(btn_row)

        role_manager.role_changed.connect(
            lambda _: self._btn_tuning.setEnabled(role_manager.is_admin))

        self._update_mode_display()

    def _make_value_box(self, label, bg, border, unit=""):
        container = QWidget()
        container.setStyleSheet("background: transparent;")
        cl = QVBoxLayout(container)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.setSpacing(1)
        title = QLabel(label)
        title.setAlignment(Qt.AlignCenter)
        title.setStyleSheet(f"font-size: 7pt; font-weight: bold; color: #404040; background: transparent;")
        cl.addWidget(title)
        box = QLabel("--")
        box.setAlignment(Qt.AlignCenter)
        box.setFixedHeight(22)
        box.setMinimumWidth(62)
        box.setStyleSheet(
            f"QLabel {{ background: {bg}; border: 2px inset {border}; font-weight: bold; "
            f"font-size: 9pt; font-family: Consolas, monospace; color: black; padding: 0 2px; }}")
        cl.addWidget(box)
        unit_lbl = QLabel(unit)
        unit_lbl.setAlignment(Qt.AlignCenter)
        unit_lbl.setStyleSheet(
            "font-size: 7pt; color: #505050; background: transparent;")
        cl.addWidget(unit_lbl)
        return title, box, container

    def _make_bottom_btn(self, text):
        btn = QPushButton(text)
        btn.setFixedHeight(24)
        btn.setStyleSheet(
            "QPushButton { background: #E0E0E0; border: 1px solid #A0A0A0; border-radius: 3px; "
            "font-size: 8pt; font-weight: bold; color: #1A1A1A; padding: 2px 8px; }"
            "QPushButton:hover { background: #D0D0D0; border-color: #808080; }"
            "QPushButton:pressed { background: #C0C0C0; border-color: #606060; }")
        return btn

    def _on_help_clicked(self):
        """Open the Process Reference dialog from the main window."""
        self.helpRequested.emit(self._tag)
        # Also try to open the help dialog directly via the main window
        w = self.window()
        if w and hasattr(w, '_show_help'):
            w._show_help()

    def _mode_btn_style(self, active, color="#4080FF"):
        if active:
            return (f"QPushButton {{ background: {color}; border: 1px solid {color}; border-radius: 3px; "
                    f"font-size: 8pt; font-weight: bold; color: white; }}")
        return ("QPushButton { background: #E0E0E0; border: 1px solid #A0A0A0; border-radius: 3px; "
                "font-size: 8pt; font-weight: bold; color: #404040; }"
                "QPushButton:hover { background: #D0D0D0; border-color: #808080; }"
                "QPushButton:pressed { background: #C0C0C0; border-color: #606060; }")

    def _update_mode_display(self):
        self._btn_auto.setStyleSheet(self._mode_btn_style(self._mode == "AUTO", "#2E8B2E"))
        self._btn_man.setStyleSheet(self._mode_btn_style(self._mode in ("MAN", "MANUAL"), "#4169E1"))
        self._btn_cas.setStyleSheet(self._mode_btn_style(self._mode in ("CAS", "CASCADE"), "#20B2AA"))

        mode_colors = {
            "AUTO": ("#2E8B2E", "#1E5B1E"),
            "MAN": ("#4169E1", "#3159C1"),
            "MANUAL": ("#4169E1", "#3159C1"),
            "CAS": ("#20B2AA", "#10827A"),
            "CASCADE": ("#20B2AA", "#10827A"),
        }
        c = mode_colors.get(self._mode, ("#A0A0A4", "#707074"))
        short = {"AUTO": "AUTO", "MAN": "MAN", "MANUAL": "MAN", "CAS": "CAS", "CASCADE": "CAS"}.get(self._mode, self._mode[:4])
        self._mode_ind.setText(short)
        self._mode_ind.setStyleSheet(
            f"QLabel {{ background: {c[0]}; border: 1px solid {c[1]}; border-radius: 3px; "
            f"color: white; font-size: 8pt; font-weight: bold; }}")

        self._co_entry.setEnabled(self._mode in ("MAN", "MANUAL"))

    @staticmethod
    def _fmt_pv(value: float) -> str:
        """Adaptive formatting: fewer decimals for larger values."""
        av = abs(value)
        if av >= 100_000:
            return f"{value:.0f}"
        if av >= 10_000:
            return f"{value:.1f}"
        if av >= 100:
            return f"{value:.1f}"
        if av >= 10:
            return f"{value:.2f}"
        return f"{value:.2f}"

    def _update_display(self):
        conv = self._config['si_to_display']
        sp_d = conv(self._sp) if self._sp else 0.0
        pv_d = conv(self._pv) if self._pv else 0.0

        self._sp_box[1].setText(self._fmt_pv(sp_d))
        self._pv_box[1].setText(self._fmt_pv(pv_d))
        self._out_box[1].setText(f"{self._out:.1f}")

        if not self._sp_entry.hasFocus():
            self._sp_entry.setText(self._fmt_pv(sp_d))
        if not self._co_entry.hasFocus():
            self._co_entry.setText(f"{self._out:.1f}")

        self._scale.setSP(sp_d)
        self._scale.setPV(pv_d)
        self._scale.setOutput(self._out)
        self._scale.setRange(self._config['pv_min'], self._config['pv_max'])

    def _set_mode(self, mode):
        if mode != self._mode:
            self._mode = mode
            self._update_mode_display()
            if self._store:
                self._store.queue_write(f'ctrl.{self._tag}.Mode', mode)
            self.modeChanged.emit(self._tag, mode)

    def _on_scale_sp(self, display_val):
        inv = self._config['display_to_si']
        si_val = inv(display_val)
        self._sp = si_val
        self._update_display()
        if self._store:
            self._store.queue_write(f'ctrl.{self._tag}.SP', si_val)
        self.spChanged.emit(self._tag, si_val)

    def _on_sp_entered(self):
        try:
            display_val = float(self._sp_entry.text())
            display_val = max(self._config['pv_min'], min(self._config['pv_max'], display_val))
            inv = self._config['display_to_si']
            si_val = inv(display_val)
            self._sp = si_val
            self._update_display()
            if self._store:
                self._store.queue_write(f'ctrl.{self._tag}.SP', si_val)
            self.spChanged.emit(self._tag, si_val)
        except ValueError:
            pass

    def _on_co_entered(self):
        if self._mode not in ("MAN", "MANUAL"):
            return
        try:
            val = float(self._co_entry.text())
            val = max(0.0, min(100.0, val))
            self._out = val
            self._update_display()
            if self._store:
                self._store.queue_write(f'ctrl.{self._tag}.ManOut', val)
            self.outputChanged.emit(self._tag, val)
        except ValueError:
            pass

    def _poll_store(self):
        if not self._store:
            return
        pv = self._store.get(f'ctrl.{self._tag}.PV')
        sp = self._store.get(f'ctrl.{self._tag}.SP')
        out = self._store.get(f'ctrl.{self._tag}.OUT')
        mode = self._store.get(f'ctrl.{self._tag}.Mode')

        if pv is not None and isinstance(pv, (int, float)):
            self._pv = pv
        if sp is not None and isinstance(sp, (int, float)):
            self._sp = sp
        if out is not None and isinstance(out, (int, float)):
            self._out = out  # store and display both 0-100%
        if mode is not None and isinstance(mode, str) and mode != self._mode:
            self._mode = mode
            self._update_mode_display()

        self._update_display()


class FaceplatePanel(QWidget):
    """Scrollable grid of faceplates for all 11 controllers."""

    tuningRequested = Signal(str)
    trendRequested = Signal(str)
    historianRequested = Signal(str)
    helpRequested = Signal(str)

    def __init__(self, store=None, parent=None):
        super().__init__(parent)
        self._store = store
        self._faceplates: dict[str, FiredHeaterFaceplate] = {}

        layout = QVBoxLayout(self)
        layout.setContentsMargins(2, 2, 2, 2)

        hdr = QLabel("Controller Faceplates")
        hdr.setStyleSheet("font-weight: bold; font-size: 10pt; padding: 4px;")
        layout.addWidget(hdr)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        content = QWidget()
        grid = QGridLayout(content)
        grid.setSpacing(6)

        tags = list(CONTROLLER_DISPLAY.keys())
        cols = 3
        for i, tag in enumerate(tags):
            fp = FiredHeaterFaceplate(tag, store)
            fp.tuningRequested.connect(self.tuningRequested.emit)
            fp.trendRequested.connect(self.trendRequested.emit)
            fp.historianRequested.connect(self.historianRequested.emit)
            fp.helpRequested.connect(self.helpRequested.emit)
            self._faceplates[tag] = fp
            grid.addWidget(fp, i // cols, i % cols)

        scroll.setWidget(content)
        layout.addWidget(scroll, 1)
