"""FaceplateManager -- lifecycle manager for ISA-101 3-layer faceplate hierarchy.

Dynamically discovers PID blocks from the strategy runtime and creates
faceplates on demand. No predefined controller list is required — any PID
block in the strategy graph automatically gets a faceplate.

Predefined CONTROLLER_CONFIGS entries (with unit conversions, alarm limits,
etc.) are used as overrides when available; otherwise a ControllerViewConfig
is auto-generated from the PID block's own config params.
"""
from __future__ import annotations

import logging
import math
import weakref
from typing import TYPE_CHECKING

import numpy as np

from PySide6.QtCore import Qt, QPoint
from PySide6.QtWidgets import QMessageBox

from azeo_control_trainer.core.pid.core.pid_block_core import Mode, LimitStatus
from azeo_control_trainer.core.presentation.user_role import role_manager
from azeo_control_trainer.core.pid.widgets.faceplate_full import (
    InlineDynamo,
    CompactDynamo,
    FaceplatePopup,
    ControllerDetailDialog,
    PidTrendDialog,
)
from azeo_control_trainer.core.pid.widgets.io_faceplates import (
    ValveFaceplatePopup,
    AIFaceplatePopup,
    AOFaceplatePopup,
)
from .pid_block_view import (
    PIDBlockView, CONTROLLER_CONFIGS, ControllerViewConfig, _coerce_view_config,
)
from .auto_tuner import RelayAutoTuner, AutoTuneResultsDialog

if TYPE_CHECKING:
    from PySide6.QtWidgets import QWidget

log = logging.getLogger(__name__)


def _config_from_pid_block(block) -> ControllerViewConfig:
    """Build a ControllerViewConfig from a PID FBD block's config params.

    Uses the block's pv_scale, sp limits, output limits, and alarm limits
    to create a faceplate config with raw engineering units (no conversion).
    """
    p = block.config.params
    pv_lo = p.get("pv_scale_lo", 0.0)
    pv_hi = p.get("pv_scale_hi", 100.0)
    op_lo = p.get("out_lo", 0.0)    # already 0-100%
    op_hi = p.get("out_hi", 100.0)
    pv_unit = p.get("pv_unit", "") or "EU"
    op_unit = p.get("op_unit", "%") or "%"
    desc = p.get("description", "") or block.instance_name

    # Determine reasonable decimals from scale span
    span = abs(pv_hi - pv_lo)
    if span < 1:
        decimals = 3
    elif span < 10:
        decimals = 2
    elif span < 1000:
        decimals = 1
    else:
        decimals = 0

    return ControllerViewConfig(
        tag=block.instance_name,
        description=desc,
        pv_lo=pv_lo,
        pv_hi=pv_hi,
        pv_unit=pv_unit,
        op_lo=op_lo,
        op_hi=op_hi,
        op_unit=op_unit,
        decimals=decimals,
        # Alarm limits from block config (already in PV engineering units)
        hihi_lim=p.get("hi_hi_lim", math.inf),
        hi_lim=p.get("hi_lim", math.inf),
        lo_lim=p.get("lo_lim", -math.inf),
        lolo_lim=p.get("lo_lo_lim", -math.inf),
        dv_hi_lim=p.get("dv_hi_lim", math.inf),
        dv_lo_lim=p.get("dv_lo_lim", -math.inf),
    )


class FaceplateManager:
    """Coordinates the ISA-101 3-layer faceplate hierarchy for all controllers.

    Controllers are discovered dynamically from the strategy runtime.
    No predefined controller list is required.

    Usage::

        mgr = FaceplateManager(store)

        # In on_tick():
        mgr.refresh_all()   # auto-discovers PID blocks, updates all views
    """

    def __init__(self, store):
        self._store = store
        self._configs: dict[str, ControllerViewConfig] = {}
        self._views: dict[str, PIDBlockView] = {}
        self._dynamos: dict[str, InlineDynamo | CompactDynamo] = {}
        self._popups: dict[str, FaceplatePopup] = {}
        self._details: dict[str, ControllerDetailDialog] = {}
        self._trends: dict[str, PidTrendDialog] = {}
        self._discovered_tags: set[str] = set()
        # Valve (IO) faceplates
        self._valve_popups: dict[str, object] = {}  # tag -> ValveFaceplatePopup
        # AI faceplates
        self._ai_popups: dict[str, AIFaceplatePopup] = {}
        # AO faceplates
        self._ao_popups: dict[str, AOFaceplatePopup] = {}
        # Auto-tuners (one per controller at most)
        self._auto_tuners: dict[str, RelayAutoTuner] = {}
        # Block-bound faceplates (DEVCTL, MOTOR_INTERLOCK), keyed on the tag
        # database's MODULE/BLOCK path rather than on a store tag — their
        # state lives on terminals and never reaches the store.
        self._block_popups: dict[str, object] = {}
        # DMC / APC status faceplate (one per store; it polls for itself)
        self._dmc_popup = None

    # -- Dynamic discovery -----------------------------------------------------

    def _get_online_runtimes(self) -> list:
        """Collect all online strategy runtimes from the store."""
        runtimes = []
        # Check plural list (used by auto_go_online)
        for rt in self._store.get_strategy_runtimes():
            if getattr(rt, 'is_online', False):
                runtimes.append(rt)
        # Check legacy singular attribute
        legacy = getattr(self._store, '_strategy_runtime', None)
        if legacy and getattr(legacy, 'is_online', False):
            if legacy not in runtimes:
                runtimes.append(legacy)
        return runtimes

    def discover_controllers(self) -> list[str]:
        """Discover PID blocks from the strategy runtime attached to the store.

        Returns list of newly discovered tags. Safe to call repeatedly —
        only processes new PID blocks.
        """
        runtimes = self._get_online_runtimes()
        if not runtimes:
            return []

        new_tags = []
        for rt in runtimes:
            compiled = rt.compiled
            if compiled is None:
                continue

            for block in compiled.graph.blocks.values():
                if block.block_type != "PID":
                    continue
                tag = block.instance_name
                if tag in self._discovered_tags:
                    continue

                # Use predefined config if available, else auto-generate
                # Try exact match first, then hyphen-stripped (e.g. "TIC-101" → "TIC101")
                cfg = CONTROLLER_CONFIGS.get(tag) or CONTROLLER_CONFIGS.get(
                    self._normalize_tag(tag))
                if cfg is None:
                    cfg = _config_from_pid_block(block)
                # Predefined configs may be plain dicts (plugin convention);
                # normalise to a ControllerViewConfig.
                cfg = _coerce_view_config(tag, cfg)

                self._configs[tag] = cfg
                self._views[tag] = PIDBlockView(tag, cfg, self._store)
                self._discovered_tags.add(tag)
                new_tags.append(tag)
                log.info("Discovered PID controller: %s (%s)", tag, cfg.description)

        return new_tags

    def register_controller(self, tag: str, cfg: ControllerViewConfig) -> None:
        """Manually register a controller config (e.g. from predefined configs).

        Use this for controllers that need specific unit conversions.
        """
        cfg = _coerce_view_config(tag, cfg)
        self._configs[tag] = cfg
        self._views[tag] = PIDBlockView(tag, cfg, self._store)
        self._discovered_tags.add(tag)

    def get_config(self, tag: str) -> ControllerViewConfig | None:
        """Get the config for a controller tag, or None if not discovered."""
        return self._configs.get(tag)

    @property
    def known_tags(self) -> list[str]:
        """List of all discovered/registered controller tags."""
        return list(self._discovered_tags)

    # Legacy P&ID layout tag names → current strategy controller tags
    _TAG_ALIASES: dict[str, str] = {
        "PIC-100": "PC-1101",
        "TIC-100": "TC-1101",
        "TIC-200": "TC-1301",
        "LIC-200": "LC-1301",
        "LIC-300": "LC-1501",
    }

    @staticmethod
    def _normalize_tag(tag: str) -> str:
        """Strip hyphens from tag for matching (e.g. 'TIC-101' → 'TIC101')."""
        return tag.replace("-", "")

    def _ensure_config(self, tag: str) -> ControllerViewConfig:
        """Get config for tag, raising ValueError if not found."""
        # Resolve legacy tag aliases
        tag = self._TAG_ALIASES.get(tag, tag)

        cfg = self._configs.get(tag)
        if cfg is None:
            # Try discovering first
            self.discover_controllers()
            cfg = self._configs.get(tag)
        if cfg is None:
            # Fall back to CONTROLLER_CONFIGS for backward compatibility
            # Try exact match first, then try with hyphens stripped
            cfg = CONTROLLER_CONFIGS.get(tag)
            if cfg is None:
                norm = self._normalize_tag(tag)
                cfg = CONTROLLER_CONFIGS.get(norm)
            if cfg is not None:
                self.register_controller(tag, cfg)
        if cfg is None:
            raise ValueError(f"Unknown controller tag: {tag}")
        return cfg

    # -- Factory methods -------------------------------------------------------

    def create_dynamo(self, tag: str, parent: QWidget | None = None) -> InlineDynamo:
        """Create an InlineDynamo widget for the given controller tag."""
        tag = self._TAG_ALIASES.get(tag, tag)
        if tag in self._dynamos and isinstance(self._dynamos[tag], InlineDynamo):
            return self._dynamos[tag]

        cfg = self._ensure_config(tag)

        dynamo = InlineDynamo(
            tag=tag,
            description=cfg.description,
            sp_lo=cfg.pv_lo,
            sp_hi=cfg.pv_hi,
            sp_units=cfg.pv_unit,
            op_lo=cfg.op_lo,
            op_hi=cfg.op_hi,
            op_units=cfg.op_unit,
            decimals=cfg.decimals,
            parent=parent,
        )

        # Configure alarm bands on the bar graph
        if math.isfinite(cfg.lo_lim) or math.isfinite(cfg.hi_lim):
            dynamo.set_alarm_limits_config({
                'lo': cfg.lo_lim if math.isfinite(cfg.lo_lim) else cfg.pv_lo,
                'hi': cfg.hi_lim if math.isfinite(cfg.hi_lim) else cfg.pv_hi,
            })

        # Connect click -> open faceplate popup (weak ref to avoid preventing GC)
        ref = weakref.ref(self)
        dynamo.faceplate_requested.connect(lambda t=tag: ref() and ref().open_faceplate(t))

        self._dynamos[tag] = dynamo
        return dynamo

    def create_compact_dynamo(self, tag: str, parent: QWidget | None = None) -> CompactDynamo:
        """Create a CompactDynamo widget (PV/SP/OP stacked readout)."""
        tag = self._TAG_ALIASES.get(tag, tag)
        if tag in self._dynamos and isinstance(self._dynamos[tag], CompactDynamo):
            return self._dynamos[tag]

        cfg = self._ensure_config(tag)

        dynamo = CompactDynamo(
            tag=tag,
            description=cfg.description,
            sp_lo=cfg.pv_lo,
            sp_hi=cfg.pv_hi,
            sp_units=cfg.pv_unit,
            op_lo=cfg.op_lo,
            op_hi=cfg.op_hi,
            op_units=cfg.op_unit,
            decimals=cfg.decimals,
            parent=parent,
        )

        # Connect click -> open faceplate popup (weak ref to avoid preventing GC)
        ref = weakref.ref(self)
        dynamo.faceplate_requested.connect(lambda t=tag: ref() and ref().open_faceplate(t))

        self._dynamos[tag] = dynamo
        return dynamo

    def open_faceplate(self, tag: str, near_widget: QWidget | None = None) -> FaceplatePopup:
        """Open or raise a FaceplatePopup for the given controller tag."""
        tag = self._TAG_ALIASES.get(tag, tag)
        if tag in self._popups:
            popup = self._popups[tag]
            if popup.isVisible():
                popup.raise_()
                popup.activateWindow()
                return popup
            else:
                del self._popups[tag]

        try:
            cfg = self._ensure_config(tag)
        except ValueError:
            log.warning("Unknown controller tag '%s' — no faceplate available", tag)
            return None

        popup = FaceplatePopup(
            tag=tag,
            description=cfg.description,
            sp_lo=cfg.pv_lo,
            sp_hi=cfg.pv_hi,
            sp_units=cfg.pv_unit,
            op_lo=cfg.op_lo,
            op_hi=cfg.op_hi,
            op_units=cfg.op_unit,
            decimals=cfg.decimals,
        )

        # Set alarm bands
        if math.isfinite(cfg.lo_lim) or math.isfinite(cfg.hi_lim):
            popup.set_alarm_bands(
                lo=cfg.lo_lim if math.isfinite(cfg.lo_lim) else cfg.pv_lo,
                hi=cfg.hi_lim if math.isfinite(cfg.hi_lim) else cfg.pv_hi,
            )

        # Connect signals using weak reference to avoid preventing GC of manager
        ref = weakref.ref(self)
        popup.sp_changed.connect(lambda val, t=tag: ref() and ref()._on_sp_changed(t, val))
        popup.op_changed.connect(lambda val, t=tag: ref() and ref()._on_op_changed(t, val))
        popup.mode_changed.connect(lambda mode, t=tag: ref() and ref()._on_mode_changed(t, mode))
        popup.detail_requested.connect(lambda t=tag: ref() and ref().open_detail(t))
        popup.trend_requested.connect(lambda t=tag: ref() and ref().open_trend(t))
        popup.historian_requested.connect(lambda t=tag: ref() and ref()._open_historian(t))
        popup.autotune_requested.connect(lambda t=tag: ref() and ref()._start_autotune(t))
        popup.finished.connect(lambda _, t=tag: ref() and ref()._popups.pop(t, None))

        # Position near the triggering widget
        if near_widget is not None:
            pos = near_widget.mapToGlobal(QPoint(near_widget.width() + 8, 0))
            popup.move(pos)

        self._popups[tag] = popup
        popup.show()

        # Initial update
        view = self._views.get(tag)
        if view is not None:
            popup.update_from_block(view)

        return popup

    def open_detail(self, tag: str) -> ControllerDetailDialog | None:
        """Open or raise a ControllerDetailDialog for the given tag.

        Requires Admin role.
        """
        if not role_manager.is_admin:
            QMessageBox.warning(
                None, "Access Denied",
                "Tuning parameters are restricted to Admin role.\n\n"
                "Switch to Admin via the User menu to modify controller tuning.")
            return None

        if tag in self._details:
            dlg = self._details[tag]
            if dlg.isVisible():
                dlg.raise_()
                dlg.activateWindow()
                return dlg
            else:
                del self._details[tag]

        cfg = self._ensure_config(tag)

        # Prepare alarm limits dict
        alarm_limits = {}
        for key, attr in [('hihi_lim', 'hihi_lim'), ('hi_lim', 'hi_lim'),
                          ('lo_lim', 'lo_lim'), ('lolo_lim', 'lolo_lim'),
                          ('dv_hi_lim', 'dv_hi_lim'), ('dv_lo_lim', 'dv_lo_lim')]:
            val = getattr(cfg, attr)
            if math.isfinite(val):
                alarm_limits[key] = val

        dlg = ControllerDetailDialog(
            tag=tag,
            description=cfg.description,
            sp_lo=cfg.pv_lo,
            sp_hi=cfg.pv_hi,
            sp_units=cfg.pv_unit,
            op_lo=cfg.op_lo,
            op_hi=cfg.op_hi,
            op_units=cfg.op_unit,
            decimals=cfg.decimals,
            alarm_limits=alarm_limits,
        )

        # Connect signals (weak ref to avoid preventing GC of manager)
        ref = weakref.ref(self)
        dlg.param_changed.connect(
            lambda attr, val, t=tag: ref() and ref()._on_param_changed(t, attr, val)
        )
        dlg.flag_changed.connect(
            lambda attr, val, t=tag: ref() and ref()._on_flag_changed(t, attr, val)
        )
        dlg.mode_change_requested.connect(
            lambda mode, t=tag: ref() and ref()._on_mode_changed(t, mode)
        )
        dlg.finished.connect(lambda _, t=tag: ref() and ref()._details.pop(t, None))

        self._details[tag] = dlg
        dlg.show()

        # Initial sync
        view = self._views.get(tag)
        if view is not None:
            dlg.update_from_block(view)

        return dlg

    def open_trend(self, tag: str) -> PidTrendDialog:
        """Open or raise a PidTrendDialog for the given tag."""
        if tag in self._trends:
            dlg = self._trends[tag]
            if dlg.isVisible():
                dlg.raise_()
                dlg.activateWindow()
                return dlg
            else:
                del self._trends[tag]

        cfg = self._ensure_config(tag)

        dlg = PidTrendDialog(
            tag=tag,
            description=cfg.description,
            pv_tag=f"ctrl.{tag}.PV",
            prefix=tag.lower(),
            pv_lo=cfg.pv_lo,
            pv_hi=cfg.pv_hi,
            pv_units=cfg.pv_unit,
        )

        ref = weakref.ref(self)
        dlg.finished.connect(lambda _, t=tag: ref() and ref()._trends.pop(t, None))

        self._trends[tag] = dlg
        dlg.show()
        return dlg

    def _open_historian(self, tag: str):
        """Open the historian popup with PV/SP/OUT tags for this controller."""
        from PySide6.QtWidgets import QApplication
        # Find the MainWindow instance
        main = None
        for widget in QApplication.topLevelWidgets():
            if hasattr(widget, '_open_historian_popup'):
                main = widget
                break
        if main is not None:
            # Add PV, SP, OUT tags to historian
            main._open_historian_popup(f"ctrl.{tag}.PV")
            main._open_historian_popup(f"ctrl.{tag}.SP")
            main._open_historian_popup(f"ctrl.{tag}.OUT")

    # -- Auto-tune relay feedback ------------------------------------------------

    def _start_autotune(self, tag: str) -> None:
        """Start a relay-feedback auto-tune test for the given controller."""
        tag = self._TAG_ALIASES.get(tag, tag)

        # Check if already running
        if tag in self._auto_tuners and self._auto_tuners[tag].is_running:
            reply = QMessageBox.question(
                None, "Auto-Tune Running",
                f"Auto-tune is already running for {tag}.\n"
                f"Do you want to abort it?",
                QMessageBox.Yes | QMessageBox.No,
            )
            if reply == QMessageBox.Yes:
                self._auto_tuners[tag].abort("User restarted")
            return

        view = self._views.get(tag)
        if view is None:
            QMessageBox.warning(
                None, "Auto-Tune",
                f"No controller view found for {tag}.")
            return

        if not role_manager.is_admin:
            QMessageBox.warning(
                None, "Access Denied",
                "Auto-tuning is restricted to Admin role.\n\n"
                "Switch to Admin via the User menu to run auto-tune.")
            return

        # Confirm with user
        reply = QMessageBox.warning(
            None, "Auto-Tune Confirmation",
            f"This will put {tag} in MANUAL mode and apply relay "
            f"output perturbations (+/- 5%) to identify the process.\n\n"
            f"The process variable will oscillate around its current value.\n\n"
            f"Current OP: {view.OUT.value:.1f}%\n"
            f"Current PV: {view.PV:.2f}\n\n"
            f"Do you want to proceed?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return

        tuner = RelayAutoTuner(view, relay_amplitude=5.0, min_cycles=4)
        ref = weakref.ref(self)
        tuner.finished.connect(lambda results, t=tag: ref() and ref()._on_autotune_finished(t, results))
        tuner.aborted.connect(lambda reason, t=tag: ref() and ref()._on_autotune_aborted(t, reason))
        tuner.status_changed.connect(
            lambda msg: log.info("Auto-tune: %s", msg)
        )

        self._auto_tuners[tag] = tuner
        tuner.start()

    def _on_autotune_finished(self, tag: str, results: dict) -> None:
        """Handle completed auto-tune test -- show results dialog."""
        view = self._views.get(tag)
        if view is None:
            return

        # Clean up tuner
        self._auto_tuners.pop(tag, None)

        dlg = AutoTuneResultsDialog(results, view)
        dlg.exec()

    def _on_autotune_aborted(self, tag: str, reason: str) -> None:
        """Handle aborted auto-tune test."""
        self._auto_tuners.pop(tag, None)
        QMessageBox.information(
            None, "Auto-Tune Aborted",
            f"Auto-tune for {tag} was aborted:\n{reason}")

    # -- Valve (control valve) faceplates --------------------------------------

    def open_valve_faceplate(self, tag: str,
                             description: str = "") -> ValveFaceplatePopup:
        """Open or raise a ValveFaceplatePopup for the given valve tag.

        *tag* is the data_key the valve reads from (e.g. ``valve_feed``).
        """
        if tag in self._valve_popups:
            popup = self._valve_popups[tag]
            if popup.isVisible():
                popup.raise_()
                popup.activateWindow()
                return popup
            else:
                del self._valve_popups[tag]

        # Use ISA tag name from tag registry if available
        from azeo_control_trainer.core.presentation.tag_registry import TAG_REGISTRY
        meta = TAG_REGISTRY.get(tag)
        if meta:
            desc = meta.display_name
            if meta.description:
                desc += f", {meta.description}"
        else:
            desc = description or tag
        popup = ValveFaceplatePopup(tag=tag, description=desc)

        # Connect output change -> write to store
        popup.output_changed.connect(self._on_valve_output_changed)
        ref = weakref.ref(self)
        popup.finished.connect(lambda _, t=tag: ref() and ref()._valve_popups.pop(t, None))

        self._valve_popups[tag] = popup
        popup.show()

        # Initial update
        if self._store is not None:
            popup.update_data(self._store.get_all())

        return popup

    def _on_valve_output_changed(self, tag: str, value: float) -> None:
        """Operator changed valve output manually (value is 0-100%)."""
        if self._store is not None:
            # Write fractional value (0-1) to the store
            self._store.queue_write(tag, value / 100.0)

    # -- AI (analog input) faceplates ------------------------------------------

    def open_ai_faceplate(self, tag: str) -> AIFaceplatePopup:
        """Open or raise an AIFaceplatePopup for the given AI tag."""
        if tag in self._ai_popups:
            popup = self._ai_popups[tag]
            if popup.isVisible():
                popup.raise_()
                popup.activateWindow()
                return popup
            else:
                del self._ai_popups[tag]

        # Try to get scale/unit info from the strategy AI block
        scale_lo, scale_hi = 0.0, 100.0
        units = ""
        decimals = 1
        # Use ISA tag name from tag registry if available
        from azeo_control_trainer.core.presentation.tag_registry import TAG_REGISTRY
        meta = TAG_REGISTRY.get(tag)
        if meta:
            desc = meta.display_name
            if meta.description:
                desc += f", {meta.description}"
        else:
            desc = tag
        for rt in self._get_online_runtimes():
            compiled = rt.compiled
            if compiled is None:
                continue
            for block in compiled.graph.blocks.values():
                if block.block_type != "AI":
                    continue
                btag = block.config.params.get("tag", "")
                if btag == tag or block.instance_name == tag:
                    p = block.config.params
                    scale_lo = p.get("scale_lo", 0.0)
                    scale_hi = p.get("scale_hi", 100.0)
                    units = p.get("eng_units", "") or p.get("unit", "") or ""
                    # Fallback to label/instance_name if not in registry
                    if not meta:
                        desc = p.get("label", "") or block.instance_name
                    span = abs(scale_hi - scale_lo)
                    if span < 1:
                        decimals = 3
                    elif span < 10:
                        decimals = 2
                    elif span < 1000:
                        decimals = 1
                    else:
                        decimals = 0
                    break

        popup = AIFaceplatePopup(
            tag=tag, description=desc,
            scale_lo=scale_lo, scale_hi=scale_hi,
            units=units, decimals=decimals,
        )
        ref = weakref.ref(self)
        popup.finished.connect(lambda _, t=tag: ref() and ref()._ai_popups.pop(t, None))
        self._ai_popups[tag] = popup
        popup.show()

        # Initial update
        if self._store is not None:
            popup.update_data(self._store.get_all())

        return popup

    # -- AO (analog output) faceplates -----------------------------------------

    def open_ao_faceplate(
        self,
        tag: str,
        description: str = "",
        parent_widget: QWidget | None = None,
    ) -> AOFaceplatePopup:
        """Open or raise an AOFaceplatePopup for the given AO tag.

        *tag* is the data_key the AO block reads from (e.g. ``valve_fuel``).
        *description* is shown in the faceplate header.
        *parent_widget* is used for positioning the popup nearby.
        """
        if tag in self._ao_popups:
            popup = self._ao_popups[tag]
            if popup.isVisible():
                popup.raise_()
                popup.activateWindow()
                return popup
            else:
                del self._ao_popups[tag]

        # Try to get scale/unit info from the strategy AO block
        desc = description or tag
        out_lo, out_hi = 0.0, 100.0
        units = "%"
        decimals = 1
        for rt in self._get_online_runtimes():
            compiled = rt.compiled
            if compiled is None:
                continue
            for block in compiled.graph.blocks.values():
                if block.block_type != "AO":
                    continue
                btag = block.config.params.get("tag", "")
                if btag == tag or block.instance_name == tag:
                    p = block.config.params
                    out_lo = p.get("scale_lo", 0.0)
                    out_hi = p.get("scale_hi", 100.0)
                    units = p.get("unit", "%") or "%"
                    desc = description or p.get("description", "") or block.instance_name
                    span = abs(out_hi - out_lo)
                    if span < 1:
                        decimals = 3
                    elif span < 10:
                        decimals = 2
                    elif span < 1000:
                        decimals = 1
                    else:
                        decimals = 0
                    break

        popup = AOFaceplatePopup(
            tag=tag, description=desc,
            out_lo=out_lo, out_hi=out_hi,
            units=units, decimals=decimals,
        )

        # Connect output change -> write to store
        popup.output_changed.connect(self._on_ao_output_changed)
        ref = weakref.ref(self)
        popup.finished.connect(lambda _, t=tag: ref() and ref()._ao_popups.pop(t, None))

        # Position near the parent widget
        if parent_widget is not None:
            pos = parent_widget.mapToGlobal(
                QPoint(parent_widget.width() + 8, 0))
            popup.move(pos)

        self._ao_popups[tag] = popup
        popup.show()

        # Initial update
        if self._store is not None:
            popup.update_data(self._store.get_all())

        return popup

    def _on_ao_output_changed(self, tag: str, value: float) -> None:
        """Operator changed AO output manually (value in engineering units)."""
        if self._store is not None:
            self._store.queue_write(tag, value)

    # -- Block-bound faceplates ------------------------------------------------

    def open_device_faceplate(self, block, graph=None, module: str = "",
                              parent: QWidget | None = None):
        """Open or raise the faceplate for a ``DEVCTL``/``MOTOR_INTERLOCK`` block.

        Keyed on ``MODULE/BLOCK`` — the same address the tag database uses —
        because two modules can each hold a ``DC1`` and they are different
        devices.
        """
        from . import BLOCK_FACEPLATES

        cls = BLOCK_FACEPLATES.get(block.block_type)
        if cls is None:
            raise ValueError(
                f"No faceplate is registered for {block.block_type}")
        from azeo_control_trainer.core.presentation.headless import is_headless
        headless = is_headless()

        key = f"{module}/{block.instance_name}" if module else block.instance_name
        popup = self._block_popups.get(key)
        if popup is not None:
            if headless or popup.isVisible():
                if not headless:
                    popup.raise_()
                    popup.activateWindow()
                return popup
            del self._block_popups[key]

        kwargs = {}
        if block.block_type == "DEVCTL":
            kwargs["description"] = str(
                block.config.params.get("description", "")
                or getattr(graph, "description", "") or "")
        popup = cls(block, graph=graph, module=module, store=self._store,
                    parent=parent, **kwargs)

        ref = weakref.ref(self)
        popup.finished.connect(
            lambda _, k=key: ref() and ref()._block_popups.pop(k, None))
        self._block_popups[key] = popup
        # Offscreen tests exercise visibility-dependent layout and command
        # state without mapping another native top-level surface through the
        # Windows offscreen plugin (which corrupts its heap after several
        # popups). A real desktop receives the same modeless faceplate.
        if headless:
            popup.setAttribute(Qt.WA_DontShowOnScreen, True)
        popup.show()
        return popup

    def open_dmc_faceplate(self, parent: QWidget | None = None):
        """Open or raise the DMC / APC status faceplate."""
        from .dmc_faceplate import DMCFaceplatePopup

        if self._dmc_popup is not None:
            if self._dmc_popup.isVisible():
                self._dmc_popup.raise_()
                self._dmc_popup.activateWindow()
                return self._dmc_popup
            self._dmc_popup = None

        popup = DMCFaceplatePopup(store=self._store, parent=parent)
        ref = weakref.ref(self)

        def _forget(_result, r=ref):
            mgr = r()
            if mgr is not None:
                mgr._dmc_popup = None

        popup.finished.connect(_forget)
        self._dmc_popup = popup
        popup.show()
        return popup

    # -- Refresh cycle ---------------------------------------------------------

    def has_open_views(self) -> bool:
        """True if any popups or trend dialogs are open.

        Lets a host decide whether a background refresh is worthwhile
        without reaching into this manager's private collections.
        """
        return bool(self._popups or self._trends or self._block_popups)

    def refresh_all(self, data: dict | None = None) -> None:
        """Refresh all PIDBlockView adapters and update visible widgets.

        Also auto-discovers new PID blocks from the strategy runtime.
        """
        if data is None:
            if self._store is None:
                return
            data = self._store.get_all()

        # Auto-discover PID blocks from running strategy
        self.discover_controllers()

        # Refresh all views
        for view in self._views.values():
            view.refresh(data)

        # Update visible dynamos
        for tag, dynamo in self._dynamos.items():
            view = self._views.get(tag)
            if view is not None and dynamo.isVisible():
                dynamo.update_from_block(view)

        # Update open popups
        for tag, popup in list(self._popups.items()):
            if popup.isVisible():
                view = self._views.get(tag)
                if view is not None:
                    popup.update_from_block(view)

        # Update open detail dialogs
        for tag, dlg in list(self._details.items()):
            if dlg.isVisible():
                view = self._views.get(tag)
                if view is not None:
                    dlg.update_from_block(view)

        # Update open valve faceplates
        for tag, popup in list(self._valve_popups.items()):
            if popup.isVisible():
                popup.update_data(data)

        # Update open AI faceplates
        for tag, popup in list(self._ai_popups.items()):
            if popup.isVisible():
                popup.update_data(data)

        # Update open AO faceplates
        for tag, popup in list(self._ao_popups.items()):
            if popup.isVisible():
                popup.update_data(data)

        # Update open block-bound faceplates. These read their block's
        # terminals directly, so they take no data dict.
        for key, popup in list(self._block_popups.items()):
            if popup.isVisible():
                try:
                    popup.refresh()
                except Exception:                          # noqa: BLE001
                    log.exception("Faceplate %s failed to refresh", key)

        # Update open trend dialogs from in-memory trend deques
        if self._trends and self._store is not None:
            sim_times = self._store.get_trend('sim_time')
            if sim_times:
                t_arr = np.array(sim_times) / 60.0  # seconds -> minutes
                for tag, dlg in list(self._trends.items()):
                    if dlg.isVisible():
                        for chart in (dlg._trend_top, dlg._trend_bot):
                            for pen_tag in chart._pens:
                                values = self._store.get_trend(pen_tag)
                                if values:
                                    v_arr = np.array(values)
                                    n = min(len(t_arr), len(v_arr))
                                    chart.update_data(
                                        pen_tag, t_arr[-n:], v_arr[-n:])

    def cleanup(self):
        """Close all open popups, dialogs, trends, and stop auto-tuners."""
        for popup in list(self._popups.values()):
            popup.close()
        self._popups.clear()
        for dlg in list(self._details.values()):
            dlg.close()
        self._details.clear()
        for dlg in list(self._trends.values()):
            dlg.close()
        self._trends.clear()
        for tuner in list(self._auto_tuners.values()):
            tuner.stop()
        self._auto_tuners.clear()
        for popup in list(self._valve_popups.values()):
            popup.close()
        self._valve_popups.clear()
        for popup in list(self._ai_popups.values()):
            popup.close()
        self._ai_popups.clear()
        for popup in list(self._ao_popups.values()):
            popup.close()
        self._ao_popups.clear()
        for popup in list(self._block_popups.values()):
            popup.close()
        self._block_popups.clear()
        if self._dmc_popup is not None:
            self._dmc_popup.close()
            self._dmc_popup = None

    # -- Signal handlers -------------------------------------------------------

    def _on_sp_changed(self, tag: str, value: float) -> None:
        """Operator changed SP in faceplate (value in display units)."""
        view = self._views.get(tag)
        if view is not None:
            view.write_sp(value)

    def _on_op_changed(self, tag: str, value: float) -> None:
        """Operator changed OP in faceplate (value in %)."""
        view = self._views.get(tag)
        if view is not None:
            view.write_op(value)

    def _on_mode_changed(self, tag: str, mode: Mode) -> None:
        """Operator changed mode in faceplate or detail dialog."""
        view = self._views.get(tag)
        if view is not None:
            view.set_target_mode(mode)

    def _on_param_changed(self, tag: str, attr: str, value: float) -> None:
        """Tuning parameter changed in detail dialog."""
        view = self._views.get(tag)
        if view is not None:
            view.write_param(attr, value)

    def _on_flag_changed(self, tag: str, attr: str, value: bool) -> None:
        """Boolean option changed in detail dialog."""
        view = self._views.get(tag)
        if view is not None:
            view.write_flag(attr, value)
