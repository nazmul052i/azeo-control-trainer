"""Azeo Control Trainer — launcher.

Opens Control Designer against a tag store. A plant, whether supplied by a
project-configured in-process provider, a network process, or physical I/O,
remains behind the :class:`SharedDataStore` boundary; the control half does
not know which one.

Usage::

    azeo                       # boot Explorer over the registered default project
    azeo --classic             # Control Designer first, no Explorer shell
    azeo --blank               # empty scratch area, no example content
    azeo --no-plant            # engineering only, nothing driving the tags
    azeo --online              # download the area on startup
    azeo --station             # dedicated operator runtime; engineering stays hidden
    azeo --graphics            # Graphics Designer first
    azeo --simulation          # Simulation Workbench first
    azeo <path/to/area>        # open a specific strategy area
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

log = logging.getLogger("azeo.app")

#: Installed packages may not carry the repository-level ``projects``
#: directory, so the established Modbus exercise remains the deterministic
#: fallback when no project registry ships beside the source tree.
FALLBACK_AREA = "azeo_modbus"


def _strategies_root() -> Path:
    """Locate ``src/strategies`` for both a source tree and an install."""
    import os
    if os.environ.get("AZEO_WORKSPACE_DIR", "").strip():
        from azeo_control_trainer.config.paths import strategies_dir
        return strategies_dir()
    here = Path(__file__).resolve()
    for base in (here.parent.parent, here.parent.parent.parent):
        candidate = base / "strategies"
        if candidate.is_dir():
            return candidate
    # Fall back to a user-writable location so a fresh install still runs.
    fallback = Path.home() / ".azeo_control_trainer" / "strategies"
    fallback.mkdir(parents=True, exist_ok=True)
    return fallback


def _default_area() -> Path:
    """Return the registry-selected project, or installed-build fallback.

    Resolve from this module rather than the current working directory: a
    desktop shortcut and a terminal launch must open the same project.  The
    ``_project.json`` check prevents a coincidental empty directory from
    replacing a usable shipped area.
    """
    from azeo_control_trainer.azeo_explorer.project_registry import default_project_path

    project = default_project_path()
    if project is not None:
        return project
    return _strategies_root() / FALLBACK_AREA


class AreaContext:
    """What the designer needs to know about the open area.

    Upstream this role is filled by a simulation plugin. The trainer has no
    plant, but the designer still needs somewhere to ask which area is open
    and whether to load it on startup — so it gets a context that describes
    the area and nothing else. Three attributes are all the designer reads.
    """

    autoload_project = True
    preset_builders: dict = {}

    def __init__(self, area: Path):
        # The designer historically accepted only a shipped area name and
        # rebuilt its path under ``src/strategies``.  An engineering project
        # may live anywhere on disk, so retain the actual path; Path joining
        # deliberately preserves an absolute right-hand operand.
        self.strategy_subdir = str(area.resolve())
        self.display_name = area.name


def _resolve_area(arg: str | None) -> Path:
    root = _strategies_root()
    if arg in ("--blank", "-b", "blank"):
        area = root / "_scratch"
        area.mkdir(parents=True, exist_ok=True)
        return area
    elif arg:
        given = Path(arg)
        # ``run.py <path>`` promises an area anywhere on disk.  Preserve the
        # convenient shipped-area shorthand, but let an existing relative
        # project path resolve from the caller's working directory instead of
        # silently nesting it under ``src/strategies``.
        if given.is_absolute() or given.exists():
            area = given.resolve()
            if area.is_dir():
                return area
            raise FileNotFoundError(f"engineering project path is not a directory: {area}")

        # `run.py --list` presents engineering projects by name, so the same
        # name must open the same path.  Looking only under src/strategies used
        # to create an empty look-alike when a listed project was selected.
        from azeo_control_trainer.azeo_explorer.project_registry import resolve_project

        project = resolve_project(arg)
        if project is not None:
            return project
        area = root / arg
        if area.is_dir():
            return area
        raise FileNotFoundError(
            f"no engineering project or strategy area named {arg!r}")
    return _default_area()


def _field_io_config(area) -> dict:
    """What the area declares about its field I/O, if anything.

    In `_project.json`, beside the module list, because it is a property of
    the *area*: these modules are wired to registers on a particular device,
    and opening them against anything else would bind them to nothing.
    """
    import json

    path = area / "_project.json"
    if not path.exists():
        return {}
    try:
        project = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        log.warning("Could not read %s: %s", path.name, error)
        return {}
    for entry in project.get("areas", ()):
        # Local Virtual I/O is a controller-side transport, not an EIOC.  It
        # gets its own project node so switching between an in-process plant
        # and an external OPC UA device never rewrites either configuration.
        virtual_io = entry.get("virtual_io")
        if virtual_io:
            return dict(virtual_io)
        # External OPC UA devices belong to the EIOC engineering node. The
        # flat key remains a read-only compatibility path for old projects
        # and for controller-native Modbus I/O.
        config = (entry.get("eioc") or {}).get("field_io") \
            or entry.get("field_io")
        if config:
            return dict(config)
    return {}


def _eioc_config(area) -> dict:
    """The area's external OPC UA client node, if one is configured.

    Older projects put an OPC UA client directly in ``field_io``. Treat that
    as a synthetic EIOC while loading so they remain usable, then the EIOC
    configurator migrates the file when it next saves.
    """
    import json

    path = area / "_project.json"
    if not path.exists():
        return {}
    try:
        project = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    for entry in project.get("areas", ()):
        config = entry.get("eioc")
        if config:
            return dict(config)
        legacy = entry.get("field_io") or {}
        if legacy.get("type") == "opcua":
            return {"name": "EIOC-1", "field_io": dict(legacy)}
    return {}


def _controller_config(area) -> dict:
    """The area's `controller` declaration, if any — beside `field_io`,
    because which controller model an area runs on is a property of the
    area, not of the launcher."""
    import json

    path = area / "_project.json"
    if not path.exists():
        return {}
    try:
        project = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    for entry in project.get("areas", ()):
        config = entry.get("controller")
        if config:
            return dict(config)
    return {}


def _controller_node_config(area, controller_name: str) -> dict:
    """The persisted Control Network record for the active controller."""
    import json

    path = area / "_project.json"
    if not path.exists():
        return {}
    try:
        project = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return next((dict(node) for node in project.get("nodes", ())
                 if str(node.get("type") or "controller").lower()
                 == "controller"
                 and str(node.get("name") or "") == controller_name), {})


def _console_config(area) -> dict:
    """The area's `console` block, or `{}`.

    Sits beside `controller` and `field_io` in `_project.json` for the
    same reason those do: which furniture a seat wears is a property of
    the *area*, not of the code, and nothing in `app.py` should know
    which area is which.

        "console": {"chrome": "azeo"}

    `azeo` wears Azeo Operator Station's menu bar and navigation bar; anything
    else (or nothing) keeps the `docs/console/07` arrangement, which is the one
    that fits the 12 % chrome budget.
    """
    import json

    path = area / "_project.json"
    if not path.exists():
        return {}
    try:
        project = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    for entry in project.get("areas", ()):
        config = entry.get("console")
        if config:
            return dict(config)
    return {}


def _attach_field_io(store, area):
    """Attach the project-selected transport behind the shared store.

    Sits exactly where `_attach_plant` sits — on the far side of the store —
    which is what lets the same modules run against a simulated process or a
    device on a wire without being edited.
    """
    config = _field_io_config(area)
    if config.get("type") == "local_virtual_io":
        from azeo_control_trainer.connectivity.fieldio.local_virtual_io import (
            LocalVirtualIoDriver,
            UnavailableLocalVirtualIoDriver,
        )

        try:
            link = LocalVirtualIoDriver(store, config, area)
        except Exception as error:                 # noqa: BLE001
            # A declared field device that is misconfigured must not silently
            # fall through to an unrelated bundled process.  Keep an explicit
            # failed driver attached so every input remains honestly Bad and
            # Explorer can report the configuration fault.
            log.error("Local Virtual I/O configuration is invalid: %s", error)
            link = UnavailableLocalVirtualIoDriver(
                store, config, error, project_dir=area)
        # Set this before start: the Qt executive must never race the field
        # side for SharedDataStore's destructive output queue.
        store.field_io_driver = link
        if getattr(link, "startup_mode", "automatic") == "manual":
            log.info(
                "Local Virtual I/O configured for manual Explorer startup: "
                "%s", link.status())
            return link
        if not link.start():
            log.warning("Local Virtual I/O unavailable: %s", link.status())
        else:
            log.info("Field I/O attached in-process: %s", link.status())
        return link
    if config.get("type") == "opcua":
        # The EIOC's role: subscribe a third-party OPC UA server's signals
        # into the store — same boundary as the Modbus master, different
        # wire. Configured by the OPC UA browser dialog or by hand.
        from azeo_control_trainer.connectivity.fieldio.opcua_driver import OpcUaLink

        link = OpcUaLink(store, str(config.get("endpoint", "")),
                         dict(config.get("signals") or {}))
        # The field transport, not the Qt scan timer, owns the destructive
        # drain of controller output writes.  Without this marker the two
        # consumers race and an AO demand can disappear into the local store
        # before the OPC client sees it.
        store.field_io_driver = link
        link.start()
        log.info("Field I/O attached over OPC UA: %s", link.endpoint)
        return link
    if config.get("type") != "modbus":
        return None

    from azeo_control_trainer.connectivity.fieldio.modbus_driver import ModbusLink

    link = ModbusLink(store, host=config.get("host", "127.0.0.1"),
                      port=int(config.get("port", 1502)),
                      unit_id=int(config.get("unit_id", 1)),
                      period_ms=int(config.get("period_ms", 100)))
    store.field_io_driver = link
    if not link.start():
        # Not fatal. An engineering session with no device attached is a
        # normal thing to want, and every input reporting Bad is the correct
        # answer rather than a reason to refuse to open.
        log.warning("No Modbus device at %s:%s — %s. Inputs will report Bad.",
                    config.get("host"), config.get("port"), link.status())
        return link
    link.seed()
    log.info("Field I/O attached over Modbus TCP: %s", link.status())
    return link


def _attach_plant(store, area):
    """Start the simulated process that matches this area, if there is one.

    Matching is by the plant's declared ``area``, so opening an area with no
    plant behind it is a normal outcome and not an error — the trainer is the
    engineering environment first.
    """
    from azeo_control_trainer.plant import registry
    from azeo_control_trainer.plant.driver import PlantDriver

    chosen = next((cls for cls in registry.all() if cls.area == area.name), None)
    if chosen is None:
        log.info("No simulated process ships for area '%s' — inputs will "
                 "report Bad, which is correct with no I/O attached", area.name)
        return None

    plant = chosen()
    driver = PlantDriver(plant, store)
    # Seed before anything goes on scan, so the first scan sees a field rather
    # than a missing tag.
    driver.seed()
    driver.start()
    log.info("Process attached: %s — %s", plant.display_name, plant.description)

    tagdb = getattr(store, "tagdb", None)
    if tagdb is not None:
        for problem in plant.verify_against(tagdb):
            log.warning("Plant/configuration mismatch: %s", problem)
    return driver


#: Every flag the launcher accepts, including the ones `run.py` intercepts
#: before it gets here — a flag has to be *recognised* even where it is
#: handled elsewhere, or `run.py --station --list` would report `--list` as
#: a typo.
KNOWN_FLAGS = frozenset({
    "--blank", "-b", "--online", "--station", "--no-plant",
    "--help", "-h", "--list", "--plants", "--displays", "--check",
    "--vision", "--upsets", "--tagdb", "--classic", "--graphics",
    "--simulation", "--simulator", "--procedures", "--help-center",
})


def _startup_surface(argv, requested: str | None = None) -> str:
    """Return the one primary surface requested by the command line.

    A station launch is deliberately operator-first. Control Designer is
    still constructed behind it because it owns the running controller and
    is the engineering target of a faceplate's Control Designer button, but it
    must not appear until an operator deliberately follows that handoff.

    Product-specific console scripts pass ``requested`` instead of rewriting
    ``sys.argv``. This keeps command-line parsing testable and gives every
    application package a stable launch boundary.
    """
    if requested is not None:
        allowed = {"explorer", "control", "graphics", "station", "simulation", "procedures"}
        if requested not in allowed:
            raise ValueError(f"unknown startup surface: {requested!r}")
        return requested
    arguments = set(argv)
    if "--station" in arguments:
        return "station"
    if "--graphics" in arguments:
        return "graphics"
    if "--procedures" in arguments:
        return "procedures"
    if arguments & {"--simulation", "--simulator"}:
        return "simulation"
    if "--classic" in arguments:
        return "control"
    return "explorer"


def _reject_unknown_flags(argv) -> str:
    """The first unrecognised flag, described. Empty when all are known.

    **An unknown flag is refused, not ignored.** Silently dropping one is
    the worst of the three options: `azeo --live` opened Control Designer with
    no station and no error, which reads as the station being broken rather
    than as the flag being wrong. Refusing costs one line and answers the
    question the operator actually has.
    """
    import difflib

    for argument in argv:
        if not argument.startswith("-") or argument in KNOWN_FLAGS:
            continue
        near = difflib.get_close_matches(argument, sorted(KNOWN_FLAGS), 1,
                                         0.6)
        suggestion = f"  Did you mean {near[0]}?" if near else ""
        accepted = ", ".join(sorted(KNOWN_FLAGS))
        return (f"{argument} is not a flag this understands.{suggestion}"
                f"\n  Accepted: {accepted}")
    return ""


def main(*, surface: str | None = None) -> int:
    if "--help-center" in sys.argv[1:]:
        from azeo_control_trainer.core.presentation.product_help import main as help_main
        return help_main()
    from PySide6.QtWidgets import QApplication

    from azeo_control_trainer.config.applications import application_for_surface
    from azeo_control_trainer.config.logging_config import (
        audit_event,
        install_qt_message_logging,
        setup_logging,
        shutdown_logging,
    )
    from azeo_control_trainer.core.hmi.theme.fonts import (
        apply_application_font,
        ensure_font_directory,
    )
    from azeo_control_trainer.core.presentation.application_style import apply_application_style

    startup_surface = _startup_surface(sys.argv[1:], requested=surface)
    product = application_for_surface(startup_surface)
    from azeo_control_trainer.config.distribution import component_available
    if not component_available(product.id.value):
        print(f"{product.title} is not installed. Run Azeo Setup and select this application.", file=sys.stderr)
        return 2
    setup_logging(product.log_name)

    problem = _reject_unknown_flags(sys.argv[1:])
    if problem:
        print(problem, file=sys.stderr)
        return 2

    # Qt's offscreen/minimal platform can expose no system fonts at all.
    # Point it at the packaged faces before the platform starts, then make
    # that face the default so Qt-owned labels do not render as boxes.
    ensure_font_directory()
    app = QApplication(sys.argv)
    install_qt_message_logging()
    apply_application_style(app)
    apply_application_font()
    app.setApplicationName("Azeo Control Trainer")
    app.setApplicationDisplayName(product.title)
    app.setOrganizationName("Azeo")

    # Python's default SIGINT raises inside whichever Qt callback is active;
    # Qt logs that callback failure and continues its native event loop. Route
    # Ctrl+C to one graceful app.exit() so the cleanup below still runs.
    from azeo_control_trainer.core.presentation.interrupt_shutdown import (
        install_interrupt_shutdown,
    )
    install_interrupt_shutdown(app)

    # Icon and taskbar identity. Without the second, Windows groups the
    # trainer under the Python interpreter's icon no matter what the windows
    # themselves carry.
    from azeo_control_trainer.core.presentation.app_icon import apply_branding
    icon_identity = "simulator" if "--simulator" in sys.argv else product.id.value
    apply_branding(app, icon_identity)

    # A build that ships a licence file must hold a valid one before any
    # product window opens; source checkouts carry no file and pass through.
    from azeo_control_trainer.core.presentation.license_gate import enforce_license
    refused = enforce_license(product.title)
    if refused is not None:
        return refused

    # Registering the block library populates the palette and lets the
    # serializer resolve every ``block_type`` it finds in a module file.
    import azeo_control_trainer.core.strategy.blocks  # noqa: F401
    from azeo_control_trainer.core.strategy.serialization import strategy_io
    from azeo_control_trainer.core.datastore.shared_data_store import SharedDataStore
    from azeo_control_trainer.azeo_control_designer import (
        ControlDesignerWindow,
    )

    # The area is the first argument that is not a flag. Taking argv[1]
    # blindly meant `azeo --online` created a strategy area literally named
    # "--online" and then opened it, empty.
    positional = [a for a in sys.argv[1:]
                  if not a.startswith("-") or a in ("--blank", "-b")]
    arg = positional[0] if positional else None
    try:
        area = _resolve_area(arg)
    except (FileNotFoundError, ValueError) as error:
        print(f"Cannot open engineering project: {error}", file=sys.stderr)
        return 2
    from azeo_control_trainer.core.configuration.workspace import draft_root
    if draft_root(area):
        print("Open this working draft through Shared editing → Resume selected draft.", file=sys.stderr)
        return 2
    audit_event(product.log_name, "session.start", area=area.name,
                command_line=" ".join(sys.argv[1:]))
    # Rebind through the module, never a from-import: the designer's project
    # tree reads it live, and a from-imported copy would keep pointing at the
    # strategies root. It must stay a Path — the project tree joins it with
    # ``/``, which a str would fail on.
    strategy_io.STRATEGY_DIR = area
    log.info("Strategy area: %s", area)

    if startup_surface == "procedures":
        # Authoring needs the configured namespace, not a controller or native plant.
        from azeo_control_trainer.azeo_pa_designer import create_window

        editor = create_window(area)
        editor.show()
        try:
            code = app.exec()
        finally:
            audit_event(product.log_name, "session.stop", area=area.name)
            shutdown_logging()
        return code

    store = SharedDataStore()

    # Build the tag database from the area's modules. Nothing here is
    # hand-maintained: the addressable namespace is walked out of the loaded
    # configuration, so it cannot drift from it. Attached to the store so the
    # designer's browsers, the watch window and any HMI binding can reach it.
    from azeo_control_trainer.core.strategy.tagdb import EntryKind, TagDatabase

    tagdb = TagDatabase.from_area(area)
    store.tagdb = tagdb

    # The controller node (PK parity): every area gets one — a name, a
    # model, a DST capacity counted from the tag database, a keylock. An
    # area that declares nothing runs a PK100 named PK-CTLR-1.
    # External OPC UA signals are owned by a separate EIOC node. Construct it
    # before logging the PK identity so its signals can be excluded from the
    # controller's native DST capacity.
    from azeo_control_trainer.connectivity.fieldio.eioc import EthernetIoCard

    store.eioc = EthernetIoCard.from_config(_eioc_config(area))

    from azeo_control_trainer.core.strategy.engine.pk_controller import (
        PKController,
    )

    controller_config = _controller_config(area)
    store.controller = PKController.from_config(controller_config)
    # The console's furniture travels with the area, the same
    # way its controller and its field I/O do.
    store.console_config = _console_config(area)
    log.info("Controller node: %s", store.controller.identity_line(store))
    if store.eioc is not None:
        log.info("EIOC node: %s", store.eioc.identity_line())

    # The PK's server face (phase 2): serve the store over Modbus TCP so an
    # external HMI — or a second trainer — can read the process and issue
    # supervisory writes. Opt-in per area; the map derives from the tag
    # database, so it cannot drift from the configuration.
    server_config = controller_config.get("modbus_server") or {}
    if server_config:
        from azeo_control_trainer.connectivity.fieldio.modbus_server import (
            StoreModbusServer,
        )

        try:
            store.modbus_server = StoreModbusServer(
                store,
                host=str(server_config.get("host", "0.0.0.0")),
                port=int(server_config.get("port", 5020)))
            if not store.modbus_server.start():
                log.warning("Modbus server did not start — port %s busy?",
                            server_config.get("port", 5020))
        except Exception as error:                  # noqa: BLE001
            # A busy port must not stop the trainer from opening.
            log.warning("Modbus server not started: %s", error)

    # The PK's OPC UA face: the whole module namespace, served with
    # quality. Opt-in per area, like the Modbus server beside it.
    ua_config = controller_config.get("opcua_server") or {}
    if ua_config:
        from azeo_control_trainer.connectivity.opcua.pk_server import PKOpcUaServer

        try:
            store.opcua_server = PKOpcUaServer(
                store,
                host=str(ua_config.get("host", "127.0.0.1")),
                port=int(ua_config.get("port", 4840)))
            if not store.opcua_server.start():
                log.warning("OPC UA server did not start — port %s busy?",
                            ua_config.get("port", 4840))
        except Exception as error:                  # noqa: BLE001
            log.warning("OPC UA server not started: %s", error)

    # Every active controller answers the engineering network's discovery
    # probe. Discovery grants no authority; it only supplies the immutable
    # hardware identity Explorer requires before Add or Commission.
    import socket
    import uuid

    from azeo_control_trainer.core.strategy.engine.controller_discovery import (
        ControllerAdvertisement,
        ControllerDiscoveryResponder,
        DISCOVERY_PORT,
    )

    node_config = _controller_node_config(area, store.controller.name)
    commissioning = node_config.get("commissioning") or {}
    controller_state = str(
        commissioning.get("state") or "commissioned").lower()
    hardware_id = str(node_config.get("hardware_id") or
                      controller_config.get("hardware_id") or
                      "AZEO-" + uuid.uuid5(
                          uuid.NAMESPACE_DNS,
                          f"{socket.gethostname()}:{store.controller.name}",
                      ).hex[:16].upper())
    try:
        host_address = socket.gethostbyname(socket.gethostname())
    except OSError:
        host_address = "127.0.0.1"
    ua_server = getattr(store, "opcua_server", None)
    endpoint = str(getattr(ua_server, "endpoint", "") or "")
    store.controller_discovery = ControllerDiscoveryResponder(
        ControllerAdvertisement(
            hardware_id=hardware_id,
            name=store.controller.name,
            model=store.controller.model.name,
            serial=str(node_config.get("serial") or hardware_id),
            address=host_address,
            opcua_endpoint=endpoint,
            state=controller_state,
            description=str(node_config.get("description") or ""),
        ),
        port=int(controller_config.get("discovery_port", DISCOVERY_PORT)),
    )
    if store.controller_discovery.start():
        log.info("Controller discovery online: %s · %s",
                 store.controller.name, hardware_id)
    log.info("Tag database: %d points across %d modules "
             "(%d field I/O, %d terminals, %d parameters)",
             len(tagdb), len(tagdb.modules()),
             len(tagdb.of_kind(EntryKind.FIELD)),
             len(tagdb.of_kind(EntryKind.TERMINAL)),
             len(tagdb.of_kind(EntryKind.PARAMETER)))
    # The PVM registration log (HMI wireframe sheet 7.6): every class,
    # every counted exception, and the overrides line that must read 0.
    try:
        from azeo_control_trainer.core.hmi.pvms import (
            registry as pvm_registry,
        )
        pvm_registry.log_registration()
    except Exception as error:                      # noqa: BLE001
        log.warning("PVM registry unavailable: %s", error)

    # Attach a process behind the store, unless asked not to. This is the
    # other side of the D2 boundary: nothing in the control half imports it,
    # and with --no-plant the trainer runs exactly as it did before — every
    # input Bad, which is what a controller with no I/O should report.
    driver = None
    if "--no-plant" not in sys.argv:
        # A Modbus area brings its own I/O; only fall back to a simulated
        # process when the area does not declare a device.
        driver = _attach_field_io(store, area)
        if driver is None:
            driver = _attach_plant(store, area)

    window = ControlDesignerWindow(store=store, plugin=AreaContext(area))
    window.setWindowTitle(
        f"Azeo Control Designer — {area.name} — Azeo Control Trainer")
    window.plant_driver = driver

    # The Explorer is the engineering shell. A dedicated station is different: it starts with no
    # engineering window visible. Its hidden Control Designer host still owns
    # the controller runtime and can be raised later by a faceplate action.
    explorer = None
    simulation_window = None
    if startup_surface == "control":
        window.show()
    elif startup_surface == "explorer":
        from azeo_control_trainer.azeo_explorer import create_window

        explorer = create_window(store=store, area=area, designer=window)
        window.explorer = explorer
        explorer.show()
    else:
        # These products use the hidden Control Designer host for the one
        # controller runtime. Keep Qt alive until their primary window has
        # been constructed.
        app.setQuitOnLastWindowClosed(False)

    # Open the area's modules, one tab each, per the Azeo per-loop
    # convention. Without this Control Designer came up on an empty canvas even
    # though an area was selected. Modules are loaded but deliberately NOT
    # brought online: with no plant attached every input would immediately
    # publish Bad, which is correct but is not a useful thing to open onto.
    if window.designer.auto_load_project():
        log.info("Opened the modules of area '%s'", area.name)
    else:
        log.info("Area '%s' has no _project.json — opening empty", area.name)

    if startup_surface == "graphics":
        # Control Designer retains the product window so repeat activation can
        # raise the same project session instead of rebuilding it.
        window._pvm_studio()
        app.setQuitOnLastWindowClosed(True)
    elif startup_surface == "simulation":
        if driver is None:
            # A project without a simulation provider still needs a useful
            # primary surface. Explorer explains the missing provider and
            # provides the configuration tools instead of exiting silently.
            from azeo_control_trainer.azeo_explorer import create_window

            explorer = create_window(store=store, area=area, designer=window)
            window.explorer = explorer
            explorer.show()
            explorer.open_simulation_workbench()
        else:
            from azeo_control_trainer.azeo_simulation_workbench import (
                create_window,
            )

            simulation_window = create_window(
                store,
                driver,
                area,
                executive=window.designer.controller_executive(),
                plant_only="--simulator" in sys.argv,
            )
            simulation_window.show()
        app.setQuitOnLastWindowClosed(True)

    # --online downloads the area straight away, which is what you want when
    # the point is to look at the running plant rather than to configure it.
    # Left off by default: a module going on scan is an action an engineer
    # should take deliberately, and the Download dialog is where they see
    # what is about to happen.
    #
    # `--station` implies it. A console opened over modules that are not
    # scanning shows a dash in every device field — correct, and a confusing
    # thing to hand someone who asked to see the plant running.
    station = startup_surface == "station"
    simulator = startup_surface == "simulation" and "--simulator" in sys.argv
    if "--online" in sys.argv or station or simulator:
        from PySide6.QtCore import QTimer

        def _go_online():
            if controller_state == "decommissioned":
                log.warning(
                    "Controller %s remains decommissioned — --online did "
                    "not put its modules on scan",
                    store.controller.name)
            else:
                window.designer.auto_go_online()
            if explorer is not None:
                explorer.refresh()
            if controller_state != "decommissioned":
                online_count = len(window.designer.controller_executive()
                                   .online_runtimes())
                log.info(
                    "Area downloaded — %d module(s) on scan",
                    online_count)
                audit_event("control_designer", "area.download",
                            area=area.name, modules=online_count)
            if station:
                # After the download, not before: the console reads the live
                # graphs, and opening it first would build a client over
                # modules that had nothing on their terminals yet.
                window._live_station(dedicated=True)
                app.setQuitOnLastWindowClosed(True)
                log.info("Operator station open — click any dynamo for its "
                         "faceplate; its Control Designer action opens the "
                         "associated module")
        def _prepare_station_io():
            """Attach manual Virtual I/O before a dedicated station boots.

            ``startup_mode=manual`` keeps an ordinary engineering session
            passive. ``--station`` is different: it is an explicit request
            for an operating console, and opening that console over an
            offline provider produced a convincing screen containing no live
            values. Provider startup can load a large plant snapshot, so it
            stays off the Qt thread and the download follows only when the
            result is known.
            """
            start = getattr(driver, "start", None)
            if driver is None or getattr(driver, "running", False) \
                    or not callable(start):
                _go_online()
                return

            import threading
            import time

            result = {"done": False, "ok": False, "error": ""}

            def attach_provider():
                try:
                    result["ok"] = bool(start())
                except Exception as error:          # noqa: BLE001
                    result["error"] = str(error)
                finally:
                    result["done"] = True

            worker = threading.Thread(
                target=attach_provider, name="runtime-virtual-io-start",
                daemon=True)
            worker.start()
            deadline = time.monotonic() + 120.0

            def provider_ready():
                if not result["done"] and time.monotonic() < deadline:
                    QTimer.singleShot(100, provider_ready)
                    return
                if not result["ok"]:
                    reason = result["error"] or getattr(
                        driver, "last_error", "") or "startup timed out"
                    log.error(
                        "Runtime Virtual I/O did not start: %s",
                        reason)
                else:
                    log.info(
                        "Runtime Virtual I/O is running; "
                        "downloading control modules")
                _go_online()

            QTimer.singleShot(100, provider_ready)

        QTimer.singleShot(
            600, _prepare_station_io if station or simulator else _go_online)

    code = app.exec()
    # QApplication.quit() stops the event loop without closing its windows.
    # Finish operator evidence while the provider still supplies its clock.
    live_station = getattr(window, "_live_station_window", None)
    if live_station is not None:
        while live_station.close() is False:
            log.warning("Waiting for operator evidence to finish before runtime shutdown")
    discovery = getattr(store, "controller_discovery", None)
    if discovery is not None:
        discovery.stop()
    for attr in ("modbus_server", "opcua_server"):
        server = getattr(store, attr, None)
        if server is not None:
            server.stop()
    driver_stop = getattr(driver, "stop", None)
    if callable(driver_stop):
        driver_stop()
    audit_event(product.log_name, "session.stop", area=area.name,
                exit_code=code)
    shutdown_logging()
    return code


if __name__ == "__main__":
    sys.exit(main())
