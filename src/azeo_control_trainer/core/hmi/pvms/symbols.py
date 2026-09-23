"""P&ID symbols shared by Graphics Designer and the operator renderer.

The core HMI's 140 silhouettes live in
``core/hmi/assets/symbols``; this module is the curated
window onto them: a small catalog for the DRAWING ITEMS palette, a
shared QSvgRenderer cache, and the block-type → symbol mapping that
makes a device PVM render as its P&ID symbol instead of a value card.

The generated files are read, never copied and never edited (UPSTREAM.md
still governs) — renaming a symbol upstream breaks this catalog loudly
at load, not silently at paint.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
import re
from xml.etree.ElementTree import ParseError

from defusedxml import ElementTree
from defusedxml.common import DefusedXmlException

_SYMBOLS_ROOT = (Path(__file__).resolve().parents[1]
                 / "assets" / "symbols" / "symbols")
SYMBOLS_DIR = _SYMBOLS_ROOT / "collections"

#: The HP-HMI symbol theme, applied at load to the `pid/` set's own
#: CSS classes (.symbol-line/.symbol-fill/.symbol-solid) — the same
#: mechanism the original artwork uses. Grey equipment on a
#: grey ground; colour stays reserved for abnormal.
SYMBOL_LINE = "#46555F"
SYMBOL_FILL = "#C2CED6"

#: name -> (relative svg under symbols/, palette label, caption).
#: The `pid/` set is the richer, themable artwork (45 files, the JS
#: primary palette); `collections/` fills the gaps it lacks.
CATALOG = {
    # ---- vessels & tanks ------------------------------------------
    "vessel": ("pid/pid_pressurized_vessel.svg",
               "Vessel", "EQUIPMENT"),
    "tank": ("pid/pid_tank_vessel.svg", "Tank", "EQUIPMENT"),
    "dished_tank": ("pid/pid_dished_roof_conical_bottom_tank.svg",
                    "Dished tank", "EQUIPMENT"),
    "dome_vessel": ("pid/pid_dome_vessel.svg", "Dome vessel",
                    "EQUIPMENT"),
    "drum": ("pid/pid_drum_or_condenser.svg", "Drum", "EQUIPMENT"),
    "reactor": ("pid/pid_reactor.svg", "Reactor", "EQUIPMENT"),
    "column": ("pid/pid_tray_column.svg", "Tray column",
               "EQUIPMENT"),
    "baffle_column": ("pid/pid_baffle_column.svg", "Baffle column",
                      "EQUIPMENT"),
    "fixed_bed_column": ("pid/pid_fixed_bed_column.svg",
                         "Fixed bed", "EQUIPMENT"),
    "bubble_column": ("collections/vessels_towers/pid_bubble_column"
                      ".svg", "Bubble column", "EQUIPMENT"),
    "stack": ("pid/pid_stack_chimney.svg", "Stack", "EQUIPMENT"),
    "spray_drier": ("pid/pid_spray_drier.svg", "Spray drier",
                    "EQUIPMENT"),
    "air_separator": ("pid/pid_air_separator.svg", "Air separator",
                      "EQUIPMENT"),
    # ---- heat transfer --------------------------------------------
    "furnace": ("pid/pid_furnace.svg", "Furnace", "EQUIPMENT"),
    "heater": ("pid/pid_heater.svg", "Heater", "EQUIPMENT"),
    "exchanger": ("pid/pid_shell_and_tube_heat_exchanger_1.svg",
                  "Exchanger", "EQUIPMENT"),
    "exchanger_2": ("pid/pid_shell_and_tube_heat_exchanger_2.svg",
                    "Exchanger 2", "EQUIPMENT"),
    "straight_tube": ("pid/pid_straight_tube_heat_exchanger.svg",
                      "Straight tube", "EQUIPMENT"),
    "condenser": ("pid/pid_condenser.svg", "Condenser",
                  "EQUIPMENT"),
    "reboiler": ("pid/pid_reboiler.svg", "Reboiler", "EQUIPMENT"),
    "air_cooler": ("pid/pid_air_cooler.svg", "Air cooler",
                   "EQUIPMENT"),
    "cooling_tower": ("pid/pid_induced_draft_cooling_tower.svg",
                      "Cooling tower", "EQUIPMENT"),
    # ---- rotating -------------------------------------------------
    "turbine_tapered": ("isa/turbine_tapered.svg", "Turbine (tapered)", "ROTATING"),
    "compressor_tapered": ("isa/compressor_tapered.svg", "Compressor (tapered)", "ROTATING"),
    "pump": ("pid/pid_centrifugal_pump_1.svg", "Pump", "ROTATING"),
    "pump_2": ("pid/pid_centrifugal_pump_2.svg", "Pump 2",
               "ROTATING"),
    "pump_3": ("pid/pid_centrifugal_pump_3.svg", "Pump 3",
               "ROTATING"),
    "compressor": ("collections/exchangers_pumps/"
                   "pid_centrifugal_compressor.svg",
                   "Compressor", "ROTATING"),
    "motor": ("pid/pid_electric_motor.svg", "Motor", "ROTATING"),
    "fan": ("pid/pid_fan.svg", "Fan", "ROTATING"),
    "blower": ("collections/exchangers_pumps/pid_gas_blower.svg",
               "Blower", "ROTATING"),
    "turbine": ("collections/motors_engines/pid_turbine.svg",
                "Turbine", "ROTATING"),
    # ---- valves ---------------------------------------------------
    "valve": ("pid/pid_gate_valve.svg", "Valve", "MANUAL"),
    "valve_closed": ("pid/pid_gate_valve_closed.svg",
                     "Valve (closed)", "MANUAL"),
    "globe_valve": ("pid/pid_globe_valve.svg", "Globe valve",
                    "MANUAL"),
    "ball_valve": ("pid/pid_ball_valve_closed.svg", "Ball valve",
                   "MANUAL"),
    "control_valve": ("pid/pid_diaphragm_actuated_gate_valve.svg",
                      "Control valve", "ACTUATED"),
    "mov": ("pid/pid_motor_actuated_gate_valve.svg", "MOV",
            "ACTUATED"),
    "actuated_valve": ("pid/pid_powered_actuated_gate_valve.svg",
                       "Actuated valve", "ACTUATED"),
    "butterfly_valve": ("collections/valves/pid_butterfly_valve.svg",
                        "Butterfly", "MANUAL"),
    "angle_valve": ("collections/valves/pid_angle_valve.svg",
                    "Angle valve", "MANUAL"),
    "recirc_valve": ("collections/valves/pid_auto_recirc_valve.svg",
                     "Recirc valve", "ACTUATED"),
    # ---- material handling ----------------------------------------
    "belt_conveyor": ("pid/pid_belt_conveyor.svg", "Belt conveyor",
                      "EQUIPMENT"),
    "bucket_elevator": ("pid/pid_bucket_elevator.svg",
                        "Bucket elevator", "EQUIPMENT"),
}


def _auto_extend_catalog() -> None:
    """Every remaining vendored symbol joins the catalog — the whole
    140-file library on the palette, not a curated fifth of it. The
    curated names above stay canonical; a drawing whose stem is
    already exposed (pid/ re-renders the collections/ art) is not
    added twice — one palette card per drawing, never near-duplicate
    noise."""
    used_rel = {entry[0] for entry in CATALOG.values()}
    used_stems = {Path(entry[0]).stem for entry in CATALOG.values()}
    sources = (("pid", _SYMBOLS_ROOT / "pid"),
               ("isa", _SYMBOLS_ROOT / "isa"),
               ("collections", _SYMBOLS_ROOT / "collections"))
    for prefix, base in sources:
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*.svg")):
            rel = f"{prefix}/{path.relative_to(base).as_posix()}"
            if rel in used_rel or path.stem in used_stems:
                continue
            used_rel.add(rel)
            used_stems.add(path.stem)
            name = path.stem
            for strip in ("pid_", "isa_"):
                if name.startswith(strip) and prefix != "isa":
                    name = name[len(strip):]
            if prefix == "isa" and not name.startswith("isa_"):
                name = f"isa_{name}"
            while name in CATALOG:
                name += "_"
            folder = path.parent.name if prefix == "collections" \
                else prefix
            CATALOG[name] = (
                rel, name.replace("_", " ").strip().title(),
                folder.replace("_", " ").upper())


_auto_extend_catalog()

#: Device PVMs render as their symbol, not a value card. DEVICE_KIND
#: (bound from CONFIG) picks between them for DEVCTL.
SYMBOL_FOR_BLOCK_TYPE = {
    "DEVCTL": {"VALVE": "control_valve", "MOTOR": "motor",
               "": "control_valve"},
    "MOTOR_INTERLOCK": {"": "motor"},
}

#: A status-PVM variant pins its silhouette — DEVICE_KIND only knows
#: MOTOR vs VALVE, so which machine it is lives on the class variant.
SYMBOL_FOR_VARIANT = {
    "pump": "pump", "compressor": "compressor", "fan": "fan",
    "blower": "blower", "turbine": "turbine", "motor": "motor",
    "valve": "control_valve",
    "turbine_tapered": "turbine_tapered", "compressor_tapered": "compressor_tapered",
    "turbine_speed": "turbine_tapered", "compressor_speed": "compressor_tapered",
    "vfd": "motor",
}

_renderers: dict = {}
_connection_ports: dict = {}

#: The catalog as the product ships it, frozen before any project loads.
#: A user symbol may never take one of these names: `symbol_path` prefers
#: user files, so an import called `pump` would repaint every pump on
#: every display in the project with no edit and no warning.
VENDORED_NAMES = frozenset(CATALOG)

#: User-imported SVGs (Graphics Designer ▸ Import SVG…): name → file.
#: Runtime-registered; the studio reloads them from the area's
#: `_library/svg/` at open, so a symbol imported once stays imported.
USER_SYMBOLS: dict = {}

# Published revisions carry the exact prepared SVGs they use. They are
# registered under digest-derived internal names so two open revisions may
# legitimately render different artwork that had the same authoring name.
DOCUMENT_SYMBOLS: dict[str, bytes] = {}
_DOCUMENT_SYMBOL_REFS: dict[str, int] = {}
MAX_DOCUMENT_SYMBOLS = 256
MAX_DOCUMENT_SYMBOL_BYTES = 16 * 1024 * 1024

#: name -> (palette label, stencil category). Imported symbols join
#: CATALOG so the Equipment palette, the properties pane and the
#: renderer all see one catalog rather than a parallel user list.
USER_SYMBOL_META: dict = {}

#: The sidecar beside the imported files that remembers their labels and
#: stencil categories. Its absence is not an error: the files are the
#: symbols, and the sidecar only carries what the palette shows.
USER_SYMBOL_INDEX = "_index.json"

#: The `_library/svg` folder the loaded user symbols came from, so
#: opening a second project replaces its predecessor's symbols instead
#: of leaving them addressable in a display that never declared them.
_user_symbol_root: Path | None = None


def _forget_user_symbol(name: str) -> None:
    USER_SYMBOLS.pop(name, None)
    USER_SYMBOL_META.pop(name, None)
    CATALOG.pop(name, None)
    for cache in (_renderers, _content_boxes, _outline_points,
                  _connection_ports):
        for key in [k for k in cache
                    if k == name or (isinstance(k, tuple) and k[0] == name)]:
            cache.pop(key, None)


def register_user_symbol(name: str, path, *, title: str = "",
                         category: str = "IMPORTED") -> bool:
    """Make an imported SVG a first-class symbol: it renders, tints,
    anchors on its ink and appears in the palette like the vendored
    catalog — one pipeline, not a parallel one.

    A name already used by the vendored artwork is refused rather than
    quietly preferred; `svg_import.safe_symbol_name` is what callers use
    to pick a free one.
    """
    path = Path(path)
    if not path.exists() or not name or name in VENDORED_NAMES:
        return False
    _forget_user_symbol(name)
    USER_SYMBOLS[name] = path
    label = title or name.replace("_", " ").strip().title()
    caption = (category or "IMPORTED").upper()
    USER_SYMBOL_META[name] = (label, caption)
    CATALOG[name] = (path.as_posix(), label, caption)
    return True


def unregister_user_symbol(name: str) -> bool:
    """Drop an imported symbol from the catalog and every cache."""
    if name not in USER_SYMBOLS:
        return False
    _forget_user_symbol(name)
    return True


def load_user_symbols(directory) -> int:
    """Register every SVG in one project's `_library/svg/`.

    Loading is exclusive to that folder: symbols from a previously opened
    project are dropped first, so a display cannot resolve a symbol its
    own project does not contain.
    """
    global _user_symbol_root
    import json

    directory = Path(directory)
    try:
        resolved = directory.resolve()
    except OSError:
        resolved = directory
    if _user_symbol_root is not None and _user_symbol_root != resolved:
        for name in list(USER_SYMBOLS):
            _forget_user_symbol(name)
    _user_symbol_root = resolved
    meta = {}
    index = directory / USER_SYMBOL_INDEX
    if index.is_file():
        try:
            stored = json.loads(index.read_text(encoding="utf-8"))
            meta = stored if isinstance(stored, dict) else {}
        except (OSError, ValueError):
            meta = {}
    present = set()
    count = 0
    if directory.is_dir():
        for path in sorted(directory.glob("*.svg")):
            try:
                from .svg_import import MAX_SVG_BYTES, SvgImportError, prepare
                if path.stat().st_size > MAX_SVG_BYTES:
                    continue
                prepare(path.read_text(encoding="utf-8", errors="replace"))
            except (OSError, SvgImportError):
                # A manually dropped hostile/broken file must not become a
                # clickable palette entry that only fails later at paint.
                continue
            record = meta.get(path.stem) or {}
            if not isinstance(record, dict):
                record = {}
            if register_user_symbol(
                    path.stem, path,
                    title=str(record.get("title", "")),
                    category=str(record.get("category", "IMPORTED"))):
                present.add(path.stem)
                count += 1
    # A file deleted outside the studio must stop being a symbol.
    for name in [n for n in USER_SYMBOLS if n not in present]:
        _forget_user_symbol(name)
    return count


def save_user_symbol_index(directory) -> None:
    """Persist the palette labels and categories beside the files."""
    import json

    directory = Path(directory)
    if not directory.is_dir():
        return
    payload = {}
    for name in sorted(USER_SYMBOLS):
        if USER_SYMBOLS[name].parent == directory:
            label, category = USER_SYMBOL_META.get(name, ("", "IMPORTED"))
            payload[name] = {"title": label, "category": category}
    (directory / USER_SYMBOL_INDEX).write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8")


def symbol_path(name: str) -> Path | None:
    user = USER_SYMBOLS.get(name)
    if user is not None and user.exists():
        return user
    entry = CATALOG.get(name)
    if entry is None:
        return None
    rel = entry[0]
    path = _SYMBOLS_ROOT / rel
    if not path.exists():
        # Legacy un-prefixed entries lived under collections/.
        path = SYMBOLS_DIR / rel
    return path if path.exists() else None


def register_document_symbols(assets: dict | None) -> dict[str, str]:
    """Register revision-pinned SVG text and return authoring-name aliases."""
    aliases = {}
    if not isinstance(assets, dict):
        return aliases
    total = 0
    from .svg_import import SvgImportError, prepare
    for index, (name, text) in enumerate(assets.items()):
        if index >= MAX_DOCUMENT_SYMBOLS:
            break
        if not isinstance(name, str) or not isinstance(text, str):
            continue
        try:
            payload = prepare(text).svg.encode("utf-8")
        except SvgImportError:
            continue
        total += len(payload)
        if total > MAX_DOCUMENT_SYMBOL_BYTES:
            break
        internal = "__document_" + hashlib.sha256(payload).hexdigest()
        DOCUMENT_SYMBOLS[internal] = payload
        _DOCUMENT_SYMBOL_REFS[internal] = _DOCUMENT_SYMBOL_REFS.get(
            internal, 0) + 1
        aliases[name] = internal
    return aliases


def release_document_symbols(aliases: dict | None) -> None:
    """Release one view's pinned assets and their derived render caches."""
    for internal in (aliases or {}).values():
        remaining = _DOCUMENT_SYMBOL_REFS.get(internal, 0) - 1
        if remaining > 0:
            _DOCUMENT_SYMBOL_REFS[internal] = remaining
            continue
        _DOCUMENT_SYMBOL_REFS.pop(internal, None)
        DOCUMENT_SYMBOLS.pop(internal, None)
        for cache in (_renderers, _content_boxes, _outline_points,
                      _connection_ports):
            for key in [candidate for candidate in cache
                        if candidate == internal
                        or (isinstance(candidate, tuple)
                            and candidate[0] == internal)]:
                cache.pop(key, None)


def symbol_exists(name: str) -> bool:
    return name in DOCUMENT_SYMBOLS or symbol_path(name) is not None


def _symbol_source(name: str) -> bytes | None:
    embedded = DOCUMENT_SYMBOLS.get(name)
    if embedded is not None:
        return embedded
    path = symbol_path(name)
    if path is None:
        return None
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
        if name in USER_SYMBOLS:
            # Files copied into the folder outside Graphics Designer still go
            # through the same parser and sanitizer as an interactive import.
            from .svg_import import SvgImportError, prepare
            try:
                text = prepare(text).svg
            except SvgImportError:
                return None
        return text.encode("utf-8")
    except OSError:
        return None


def connection_ports(name: str) -> dict[str, tuple[float, float]]:
    """Return the SVG author's normalized process connection points.

    AzeoPlantSimulator's editor aligns pipes against the ``data-anchor-*``
    metadata embedded in the shared SVG library.  Reading that same contract
    here is important: alpha-outline projection finds *a* visible pixel, but
    only the authored port identifies the intended pump discharge, vessel
    nozzle, or valve centreline.
    """
    if name in _connection_ports:
        return dict(_connection_ports[name])
    source = _symbol_source(name)
    if source is None:
        _connection_ports[name] = {}
        return {}
    try:
        root = ElementTree.fromstring(source)
    except (ParseError, DefusedXmlException):
        _connection_ports[name] = {}
        return {}
    side_names = {"top": "n", "right": "e",
                  "bottom": "s", "left": "w"}
    result = {}
    for element in root.iter():
        side = side_names.get(str(element.attrib.get(
            "data-connection-side", "")).lower())
        if side is None:
            continue
        try:
            x = float(element.attrib["data-anchor-x"])
            y = float(element.attrib["data-anchor-y"])
        except (KeyError, TypeError, ValueError):
            continue
        if 0.0 <= x <= 1.0 and 0.0 <= y <= 1.0:
            result[side] = (x, y)
    _connection_ports[name] = result
    return dict(result)


def legacy_viewport_ports(name: str) -> dict[str, tuple[float, float]]:
    """Return the old padding-box ports used by primary-library SVGs.

    Releases before the geometry-port audit placed the four hidden ports on
    the extraction viewport instead of on visible symbol ink.  Display
    documents persist those coordinates, so recognizing the exact obsolete
    pattern lets the renderer repair an existing exchanger or valve without
    overwriting an engineer-authored custom port.
    """
    svg = renderer(name)
    if svg is None:
        return {}
    view = svg.viewBoxF()
    width, height = view.width(), view.height()
    if width <= 0.0 or height <= 0.0:
        return {}
    # extract_svg_symbols.py used padding=4 plus diagrams.net's half-pixel
    # geometry offset. Keep this formula here as migration knowledge only;
    # newly generated assets derive their ports from visible vectors.
    left, top = 4.5, 4.5
    right, bottom = width - 3.5, height - 3.5
    centre_x = (left + right) / 2.0
    centre_y = (top + bottom) / 2.0
    return {
        "n": (centre_x / width, top / height),
        "e": (right / width, centre_y / height),
        "s": (centre_x / width, bottom / height),
        "w": (left / width, centre_y / height),
    }


def _themed_bytes(path: Path, *, line=SYMBOL_LINE, fill=SYMBOL_FILL, text=None) -> bytes:
    """The pid/ set carries a `symbol-theme` CSS block for exactly
    this: restyle the classes, never the geometry. Files without the
    block load untouched. The generated SVG is read, never written."""
    raw = path.read_text(encoding="utf-8")
    return _themed_source(raw, line=line, fill=fill, text=text)


def _themed_source(raw: str, *, line=SYMBOL_LINE, fill=SYMBOL_FILL,
                   text=None) -> bytes:
    """Theme already-loaded SVG text, including revision-pinned assets."""
    def restyle(match):
        css = match.group(0)
        for name, prop, color in (("line", "stroke", line), ("fill", "fill", fill),
                                   ("solid", "fill", line), ("text", "fill", text or line)):
            # Handcrafted assets use compact CSS; generated assets use
            # multiline CSS. Literal whitespace matching left the former
            # permanently silver in a dark operator display.
            pattern = rf'(\.symbol-{name}\s*\{{[^}}]*?\b{prop}\s*:\s*)[^;}}]+'
            css = re.sub(pattern, lambda rule: rule.group(1) + color, css)
        return css
    raw = re.sub(r'<style\b[^>]*\bid=["\']symbol-theme["\'][^>]*>.*?</style>',
                 restyle, raw, flags=re.DOTALL)
    return raw.encode("utf-8")


def renderer(name: str, *, line=SYMBOL_LINE, fill=SYMBOL_FILL, text=None):
    """Shared QSvgRenderer per symbol; None when unknown/missing.
    Themable symbols load through the HP-HMI symbol theme."""
    key = name if (line, fill, text) == (SYMBOL_LINE, SYMBOL_FILL, None) else (name, line, fill, text)
    if key in _renderers:
        return _renderers[key]
    from PySide6.QtCore import QByteArray
    from PySide6.QtSvg import QSvgRenderer

    source = _symbol_source(name)
    if source is None:
        _renderers[key] = None
        return None
    svg = QSvgRenderer(QByteArray(_themed_source(
        source.decode("utf-8", errors="replace"),
        line=line, fill=fill, text=text)))
    _renderers[key] = svg if svg.isValid() else None
    return _renderers[key]


_content_boxes: dict = {}
_outline_points: dict = {}


def _alpha_geometry(name: str):
    """Rasterise one SVG in view-box proportions for hit geometry.

    QSvgRenderer deliberately keeps the vector implementation private.  A
    small cached alpha raster gives the authoring canvas the information it
    actually needs: where the visible ink is and which pixels form its outer
    edge.  The raster is never used for painting, so operator rendering stays
    vector sharp.
    """
    from PySide6.QtCore import QRectF, Qt
    from PySide6.QtGui import QImage, QPainter

    svg = renderer(name)
    if svg is None:
        return None
    view = svg.viewBoxF()
    ratio = max(1e-3, view.width() / max(view.height(), 1e-3))
    extent = 192
    if ratio >= 1.0:
        width, height = extent, max(24, round(extent / ratio))
    else:
        width, height = max(24, round(extent * ratio)), extent
    image = QImage(width, height, QImage.Format_RGBA8888)
    image.fill(Qt.transparent)
    painter = QPainter(image)
    svg.render(painter, QRectF(0, 0, width, height))
    painter.end()
    # A Qt call per pixel dominated first-time operator call-ups. RGBA8888
    # gives a byte-ordered alpha lane on every platform, with identical ink
    # thresholds and bounds to the former pixel() scan.
    alpha = bytes(image.constBits())[3::4]
    stride = image.bytesPerLine() // 4
    opaque = []
    left, right, top, bottom = width, -1, height, -1
    for y in range(height):
        row = [value > 16 for value in alpha[y * stride:y * stride + width]]
        opaque.append(row)
        if any(row):
            left = min(left, row.index(True))
            right = max(right, width - 1 - row[::-1].index(True))
            top, bottom = min(top, y), y
    return image, opaque, (left, top, right, bottom)


def content_box(name: str) -> tuple:
    """The artwork's true bounds inside the SVG, as fractions
    (fx, fy, fw, fh) of the viewBox.

    The source files carry whitespace padding around the drawing;
    anchoring a pipe to the item RECT leaves a visible gap between the
    pipe end and the symbol. This scans the rendered alpha once per
    symbol and caches where the ink actually is.
    """
    if name in _content_boxes:
        return _content_boxes[name]
    geometry = _alpha_geometry(name)
    if geometry is None:
        _content_boxes[name] = (0.0, 0.0, 1.0, 1.0)
        return _content_boxes[name]
    image, _opaque, (left, top, right, bottom) = geometry
    width, height = image.width(), image.height()
    if right < 0:
        box = (0.0, 0.0, 1.0, 1.0)
    else:
        box = (left / width, top / height,
               (right - left + 1) / width,
               (bottom - top + 1) / height)
    _content_boxes[name] = box
    return box


def outline_points(name: str) -> tuple[tuple[float, float, str], ...]:
    """Visible SVG perimeter as ``(x_fraction, y_fraction, normal)``.

    Fractions are relative to the SVG view box, not to a surrounding PVM.
    Consequently a glued pipe follows the same curve after a placement is
    moved or resized.  Normals are intentionally cardinal because the shared
    process-pipe router is orthogonal.
    """
    if name in _outline_points:
        return _outline_points[name]
    geometry = _alpha_geometry(name)
    if geometry is None:
        result = ((0.5, 0.0, "n"), (1.0, 0.5, "e"),
                  (0.5, 1.0, "s"), (0.0, 0.5, "w"))
        _outline_points[name] = result
        return result
    image, opaque, (_left, _top, right, _bottom) = geometry
    width, height = image.width(), image.height()
    if right < 0:
        result = ((0.5, 0.0, "n"), (1.0, 0.5, "e"),
                  (0.5, 1.0, "s"), (0.0, 0.5, "w"))
        _outline_points[name] = result
        return result
    result = []
    directions = ((0, -1, "n"), (1, 0, "e"),
                  (0, 1, "s"), (-1, 0, "w"))
    for y in range(height):
        for x in range(width):
            if not opaque[y][x]:
                continue
            exposed = []
            for dx, dy, normal in directions:
                nx, ny = x + dx, y + dy
                if nx < 0 or ny < 0 or nx >= width or ny >= height \
                        or not opaque[ny][nx]:
                    exposed.append(normal)
            if not exposed:
                continue
            # Corners expose two sides. Choose the direction in which the
            # point lies furthest from the artwork centre; this makes a pipe
            # leave a curved vessel cap in the visually natural direction.
            normal = max(exposed, key=lambda value: {
                "n": 0.5 - y / max(height - 1, 1),
                "s": y / max(height - 1, 1) - 0.5,
                "w": 0.5 - x / max(width - 1, 1),
                "e": x / max(width - 1, 1) - 0.5,
            }[value])
            result.append((x / max(width - 1, 1),
                           y / max(height - 1, 1), normal))
    # A large SVG can expose thousands of adjacent pixels. About 1,200 points
    # is sub-pixel accurate at ordinary authoring zoom while keeping every
    # pointer move cheap.
    stride = max(1, len(result) // 1200)
    cached = tuple(result[::stride])
    _outline_points[name] = cached
    return cached


def aspect(name: str) -> float:
    """height/width of the symbol's own viewBox — the item that hosts
    it must share this ratio or the selection box lies about size."""
    svg = renderer(name)
    if svg is None:
        return 1.0
    view = svg.viewBoxF()
    if view.width() <= 0:
        return 1.0
    return view.height() / view.width()


def preview_pixmap(name: str, width: int = 96, height: int = 30):
    """A palette-card preview, rendered once."""
    from PySide6.QtCore import QRectF, Qt
    from PySide6.QtGui import QPainter, QPixmap

    svg = renderer(name)
    pixmap = QPixmap(width, height)
    pixmap.fill(Qt.transparent)
    if svg is not None:
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.Antialiasing, True)
        view = svg.viewBoxF()
        scale = min(width / max(view.width(), 1),
                    height / max(view.height(), 1)) * 0.9
        w, h = view.width() * scale, view.height() * scale
        svg.render(painter, QRectF((width - w) / 2, (height - h) / 2,
                                   w, h))
        painter.end()
    return pixmap


_tinted: dict = {}


def tinted_pixmap(name: str, width: int, height: int,
                  colour: str):
    """The symbol silhouette filled with a colour — SVG rendered to a
    pixmap, then recoloured through SourceIn so the shape's own alpha
    masks the fill."""
    key = (name, width, height, colour)
    if key in _tinted:
        return _tinted[key]
    from PySide6.QtCore import QRectF, Qt
    from PySide6.QtGui import QColor, QPainter, QPixmap

    svg = renderer(name)
    if svg is None or width < 2 or height < 2:
        _tinted[key] = None
        return None
    pixmap = QPixmap(width, height)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing, True)
    # Stretched to the box — the item rect IS the drawing.
    svg.render(painter, QRectF(0, 0, width, height))
    painter.setCompositionMode(QPainter.CompositionMode_SourceIn)
    painter.fillRect(pixmap.rect(), QColor(colour))
    painter.end()
    if len(_tinted) > 256:
        _tinted.clear()
    _tinted[key] = pixmap
    return pixmap


def pvm_symbol(block_type: str, device_kind: str = "") -> str | None:
    """The symbol a device PVM draws, or None for value-card types."""
    table = SYMBOL_FOR_BLOCK_TYPE.get(block_type)
    if table is None:
        return None
    return table.get(str(device_kind or "").upper(), table.get(""))
