"""One machine faceplate, composed from the existing loop and device surfaces."""
from PySide6.QtCore import QPointF, Qt, Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout, QWidget

from ..theme.roles import Role
from ..theme.fonts import FontRole
from .device_surface import DeviceFaceplateSurface
from .loop_surface import LoopFaceplateSurface


class _MachineRunSurface(DeviceFaceplateSurface):
    def _paint_accept_and_modes(self, painter):
        from PySide6.QtCore import QRectF
        # DEVCTL supplies no accept/acknowledgment state. A checkbox here
        # would invite a command that this machine interface cannot perform.
        for y, caption, value, mismatch in (
                (183, "Target mode", self.target_mode, self.target_mode_mismatch),
                (202, "Actual mode", self.actual_mode, self.actual_mode_mismatch)):
            self._text(painter, QRectF(30, y, 115, 18), caption,
                       FontRole.LABEL, 10, Role.TEXT_DIM, align=Qt.AlignLeft)
            self._mode_row(painter, y, caption, value, mismatch)


class MachineFaceplateSurface(QWidget):
    WHOLE_SURFACE = True
    write_requested = Signal(str, object)
    action_requested = Signal(str)
    modeRequested = Signal(str)

    def __init__(self, palette, parent=None):
        super().__init__(parent)
        self._palette = palette
        self._params = {}
        self._description = "Machine speed"
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(4)
        row = QHBoxLayout()
        row.setSpacing(12)
        self.loop = LoopFaceplateSurface(palette, self)
        self.device = _MachineRunSurface(palette, self)
        row.addWidget(self.loop, 0, Qt.AlignTop)
        row.addWidget(self.device, 0, Qt.AlignTop)
        root.addLayout(row)
        self.message = QLabel("", self)
        self.message.setWordWrap(True)
        self.message.setFixedHeight(30)
        root.addWidget(self.message)
        self.loop.write_requested.connect(self.write_requested.emit)
        self.device.write_requested.connect(self._device_write)
        self.loop.action_requested.connect(self.action_requested.emit)
        self.loop.modeRequested.connect(self.modeRequested.emit)
        self.loop.mini_requested.connect(self._toggle_loop)
        self.device.set_available_actions(())
        self.set_write_permissions(())
        self.apply_theme(palette)

    def _device_write(self, key, value):
        self.write_requested.emit("device." + key, value)

    def _toggle_loop(self):
        self.loop.set_mini(not self.loop._mini)

    @property
    def mode(self):
        return self.loop.mode

    def actual_mode_arrow_geometry(self):
        return self.loop.actual_mode_arrow_geometry().translated(QPointF(self.loop.pos()))

    def set_parameters(self, params):
        self._params = dict(params)
        self.set_identity("", self._description)

    def set_identity(self, _path, description="Machine speed"):
        self._description = description
        self.loop.set_identity(self._params.get("path", ""), description)
        self.device.set_identity(self._params.get("device", ""), "Run / stop control")

    def set_available_actions(self, keys):
        self.loop.set_available_actions(keys)

    def set_write_permissions(self, keys):
        self.loop.set_write_permissions(key for key in keys if not key.startswith("device."))
        self.device.set_write_permissions(key[7:] for key in keys if key.startswith("device."))

    def set_alarm_records(self, records):
        self.loop.set_alarm_records(records)

    def show_write_result(self, key, success, error):
        self.message.setText("Command accepted" if success else str(error))

    def refresh(self, bound):
        self.loop.refresh(bound)
        # Device state codes must not survive Bad/Uncertain quality as a
        # believable running or healthy indication beside a fresh speed PV.
        device = {key[7:]: binding for key, binding in bound.items()
                  if key.startswith("device.") and not isinstance(binding, tuple)
                  and binding.result.quality.name == "GOOD"}
        self.device.refresh(device)
        for key, attr in (("permit", "no_permit"), ("interlock", "interlocked")):
            binding = device.get(key)
            value = binding.result.value if binding else None
            setattr(self.device, attr, not value if isinstance(value, bool) else None)
        self.device.update()

    def apply_theme(self, palette):
        self._palette = palette
        self.loop.apply_theme(palette)
        self.device.apply_theme(palette)
        self.message.setStyleSheet(f"color: {palette[Role.TEXT]}; background: transparent;")


WHOLE_SURFACES = {name: MachineFaceplateSurface for name in (
    "VFDSpeedFaceplate", "TurbineSpeedFaceplate", "CompressorSpeedFaceplate")}
