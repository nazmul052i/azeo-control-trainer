"""Extract portable standalone P&ID symbols from the bundled draw.io SVGs.

The sources are editable diagrams.net SVGs.  Their rendered groups are already
vector geometry, but each document also carries draw.io metadata, adaptive CSS,
HTML ``foreignObject`` labels, and bitmap fallbacks.  This tool keeps each
top-level rendered symbol, crops it to its source cell, removes browser-specific
pieces, restores native text, and adds portable theme and connection metadata.
"""

from __future__ import annotations

import base64
import copy
import json
import math
import re
from dataclasses import dataclass
from pathlib import Path
from xml.etree import ElementTree as ET

SVG_NS = "http://www.w3.org/2000/svg"
SVG = f"{{{SVG_NS}}}"

THEME_CSS = """\
/* Edit these values to restyle this symbol. */
.symbol-line {
  stroke: #000000;
  stroke-width: 1;
  stroke-opacity: 1;
}
.symbol-fill {
  fill: #ffffff;
  fill-opacity: 1;
}
.symbol-solid {
  fill: #000000;
  fill-opacity: 1;
}
.symbol-text {
  fill: #000000;
  fill-opacity: 1;
}
.connection-port {
  display: none;
  fill: #ffffff;
  stroke: #2563eb;
  stroke-width: 1.5;
  pointer-events: all;
  cursor: crosshair;
}
"""

THEME_CLASS_ORDER = (
    "symbol-fill",
    "symbol-solid",
    "symbol-line",
    "symbol-text",
)


@dataclass(frozen=True, slots=True)
class SymbolSpec:
    cell_id: str
    slug: str
    title: str
    description: str


@dataclass(frozen=True, slots=True)
class CollectionSource:
    filename: str
    folder: str
    expected_count: int


COLLECTION_SOURCES = (
    CollectionSource("Exchanger&Pumps.svg", "exchangers_pumps", 19),
    CollectionSource("motor.svg", "motors_engines", 6),
    CollectionSource("Valves.svg", "valves", 35),
    CollectionSource("Vessels&Towers.svg", "vessels_towers", 29),
)


ACTUATOR_NAMES = {
    "angBlow": "angle_blow",
    "balDiaph": "balanced_diaphragm",
    "dblActing": "double_acting",
    "diaph": "diaphragm",
    "digital": "digital",
    "elHyd": "electro_hydraulic",
    "key": "key",
    "man": "manual",
    "motor": "motor",
    "pilot": "pilot",
    "powered": "powered",
    "singActing": "single_acting",
    "solenoid": "solenoid",
    "solenoidManRes": "solenoid_manual_reset",
    "weight": "weight",
}


SYMBOLS = (
    SymbolSpec(
        "2",
        "pid_discrete_instrument",
        "P&ID discrete instrument",
        "Circular instrument bubble with TI and tag-number placeholders.",
    ),
    SymbolSpec(
        "3",
        "pid_shared_control_display",
        "P&ID shared control display",
        "Instrument bubble inside a shared-control display square.",
    ),
    SymbolSpec(
        "4",
        "pid_controller_indicator",
        "P&ID controller indicator",
        "Instrument bubble inside a square with a downward connection stem.",
    ),
    SymbolSpec(
        "5",
        "pid_tray_column",
        "P&ID tray column",
        "Vertical process column containing dashed tray lines.",
    ),
    SymbolSpec(
        "6",
        "pid_fixed_bed_column",
        "P&ID fixed-bed column",
        "Vertical process column containing two fixed beds.",
    ),
    SymbolSpec(
        "7",
        "pid_baffle_column",
        "P&ID baffle column",
        "Vertical process column containing alternating baffles.",
    ),
    SymbolSpec(
        "8",
        "pid_valve_tray_column",
        "P&ID valve-tray column",
        "Vertical process column containing valve-tray internals.",
    ),
    SymbolSpec(
        "9",
        "pid_air_separator",
        "P&ID air separator",
        "Air-separation equipment symbol.",
    ),
    SymbolSpec(
        "10",
        "pid_belt_conveyor",
        "P&ID belt conveyor",
        "Horizontal belt-conveyor material-handling symbol.",
    ),
    SymbolSpec(
        "11",
        "pid_bucket_elevator",
        "P&ID bucket elevator",
        "Vertical bucket-elevator material-handling symbol.",
    ),
    SymbolSpec("12", "pid_fan", "P&ID fan", "Inline process fan symbol."),
    SymbolSpec("13", "pid_gate_valve", "P&ID gate valve", "Normally open gate-valve symbol."),
    SymbolSpec(
        "14",
        "pid_gate_valve_closed",
        "P&ID closed gate valve",
        "Gate-valve symbol shown in its closed state.",
    ),
    SymbolSpec(
        "15",
        "pid_ball_valve_closed",
        "P&ID closed ball valve",
        "Ball-valve symbol shown in its closed state.",
    ),
    SymbolSpec("16", "pid_globe_valve", "P&ID globe valve", "Globe-valve symbol."),
    SymbolSpec(
        "17",
        "pid_diaphragm_actuated_gate_valve",
        "P&ID diaphragm-actuated gate valve",
        "Gate valve with a diaphragm actuator.",
    ),
    SymbolSpec(
        "18",
        "pid_powered_actuated_gate_valve",
        "P&ID powered-actuated gate valve",
        "Gate valve with a powered rectangular actuator.",
    ),
    SymbolSpec(
        "20",
        "pid_motor_actuated_gate_valve",
        "P&ID motor-actuated gate valve",
        "Gate valve with a motor actuator.",
    ),
    SymbolSpec(
        "21",
        "pid_electric_motor",
        "P&ID electric motor",
        "Circular electric-motor symbol labelled M.",
    ),
    SymbolSpec(
        "22",
        "pid_flow_nozzle",
        "P&ID flow nozzle",
        "Inline differential-pressure flow-nozzle symbol.",
    ),
    SymbolSpec("23", "pid_condenser", "P&ID condenser", "Condenser heat-exchanger symbol."),
    SymbolSpec(
        "24",
        "pid_fixed_straight_tube_heat_exchanger",
        "P&ID fixed straight-tube heat exchanger",
        "Fixed straight-tube heat-exchanger symbol.",
    ),
    SymbolSpec("25", "pid_reboiler", "P&ID reboiler", "Reboiler heat-exchanger symbol."),
    SymbolSpec(
        "26",
        "pid_shell_and_tube_heat_exchanger_1",
        "P&ID shell-and-tube heat exchanger 1",
        "Shell-and-tube heat-exchanger variant 1.",
    ),
    SymbolSpec(
        "27",
        "pid_shell_and_tube_heat_exchanger_2",
        "P&ID shell-and-tube heat exchanger 2",
        "Shell-and-tube heat-exchanger variant 2.",
    ),
    SymbolSpec(
        "28",
        "pid_shell_and_tube_heat_exchanger_3",
        "P&ID shell-and-tube heat exchanger 3",
        "Shell-and-tube heat-exchanger variant 3.",
    ),
    SymbolSpec("29", "pid_heater", "P&ID heater", "Process-heater heat-exchanger symbol."),
    SymbolSpec(
        "30",
        "pid_straight_tube_heat_exchanger",
        "P&ID straight-tube heat exchanger",
        "Horizontal straight-tube heat-exchanger symbol.",
    ),
    SymbolSpec(
        "31",
        "pid_floating_head_heat_exchanger",
        "P&ID floating-head heat exchanger",
        "Horizontal floating-head heat-exchanger symbol.",
    ),
    SymbolSpec(
        "32",
        "pid_centrifugal_pump_1",
        "P&ID centrifugal pump 1",
        "Centrifugal-pump symbol variant 1.",
    ),
    SymbolSpec(
        "33",
        "pid_centrifugal_pump_2",
        "P&ID centrifugal pump 2",
        "Centrifugal-pump symbol variant 2.",
    ),
    SymbolSpec(
        "34",
        "pid_centrifugal_pump_3",
        "P&ID centrifugal pump 3",
        "Centrifugal-pump symbol variant 3.",
    ),
    SymbolSpec(
        "35",
        "pid_dome_vessel",
        "P&ID dome vessel",
        "Horizontal vessel with a domed top profile.",
    ),
    SymbolSpec("36", "pid_pit_vessel", "P&ID pit vessel", "Open pit-vessel symbol."),
    SymbolSpec(
        "37",
        "pid_different_diameter_vessel",
        "P&ID different-diameter vessel",
        "Vertical vessel with sections of different diameters.",
    ),
    SymbolSpec(
        "38",
        "pid_pressurized_vessel",
        "P&ID pressurized vessel",
        "Vertical pressurized-vessel symbol.",
    ),
    SymbolSpec(
        "39",
        "pid_drum_or_condenser",
        "P&ID drum or condenser",
        "Horizontal drum or condenser vessel symbol.",
    ),
    SymbolSpec("40", "pid_spray_drier", "P&ID spray drier", "Spray-drier vessel symbol."),
    SymbolSpec("41", "pid_tank_vessel", "P&ID tank vessel", "General tank or vessel symbol."),
    SymbolSpec(
        "42",
        "pid_dished_roof_conical_bottom_tank",
        "P&ID dished-roof conical-bottom tank",
        "Tank with a dished roof and conical bottom.",
    ),
    SymbolSpec("43", "pid_reactor", "P&ID reactor", "Vertical process-reactor symbol."),
    SymbolSpec("44", "pid_furnace", "P&ID furnace", "Process-furnace symbol."),
    SymbolSpec(
        "45",
        "pid_induced_draft_cooling_tower",
        "P&ID induced-draft cooling tower",
        "Induced-draft cooling-tower symbol.",
    ),
    SymbolSpec("46", "pid_air_cooler", "P&ID air cooler", "Horizontal air-cooler symbol."),
    SymbolSpec(
        "47",
        "pid_stack_chimney",
        "P&ID stack or chimney",
        "Vertical stack or chimney symbol.",
    ),
)


def _number(value: str) -> float:
    return float(value)


def _format(value: float) -> str:
    return str(int(value)) if value.is_integer() else f"{value:g}"


def _style_properties(style: str) -> dict[str, str]:
    properties: dict[str, str] = {}
    for item in style.split(";"):
        if "=" not in item:
            continue
        key, value = item.split("=", 1)
        properties[key] = value
    return properties


def _slugify(value: str) -> str:
    value = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", value)
    value = re.sub(r"[^A-Za-z0-9]+", "_", value)
    return value.strip("_").lower()


def _collection_symbol_spec(
    cell: ET.Element,
    *,
    source: CollectionSource,
    duplicate_counts: dict[str, int],
) -> SymbolSpec:
    properties = _style_properties(cell.attrib.get("style", ""))
    shape = properties.get("shape")
    if not shape:
        raise ValueError(f"{source.filename} cell {cell.attrib.get('id')} has no shape metadata")

    base = shape.rsplit(".", 1)[-1]
    valve_type = properties.get("valveType")
    column_type = properties.get("columnType")
    fan_type = properties.get("fanType")
    if valve_type:
        base = f"{valve_type}_valve"
    elif column_type:
        base = f"{column_type}_column"
    elif fan_type and fan_type != "common":
        base = f"{fan_type}_fan"

    slug_parts = [_slugify(base)]
    state = properties.get("defState")
    if state:
        slug_parts.append(_slugify(state))
    actuator = properties.get("actuator")
    if actuator and actuator != "none":
        actuator_name = ACTUATOR_NAMES.get(actuator, _slugify(actuator))
        slug_parts.append(f"{actuator_name}_actuator")

    base_slug = "pid_" + "_".join(part for part in slug_parts if part)
    duplicate_counts[base_slug] = duplicate_counts.get(base_slug, 0) + 1
    duplicate_number = duplicate_counts[base_slug]
    slug = base_slug if duplicate_number == 1 else f"{base_slug}_{duplicate_number}"
    display_name = slug.removeprefix("pid_").replace("_", " ")
    title = f"P&ID {display_name}"
    description = f"Portable {display_name} symbol extracted from {source.filename}."
    return SymbolSpec(cell.attrib["id"], slug, title, description)


def _group_translation(group: ET.Element) -> tuple[float, float]:
    translated = next(
        (node for node in group.iter(f"{SVG}g") if node is not group),
        None,
    )
    transform = "" if translated is None else translated.attrib.get("transform", "")
    match = re.fullmatch(r"translate\(([-\d.]+),([-\d.]+)\)", transform)
    if not match:
        return 0.0, 0.0
    return float(match.group(1)), float(match.group(2))


def _line_segment(
    start: tuple[float, float],
    end: tuple[float, float],
) -> tuple[tuple[float, float], tuple[float, float]]:
    return start, end


def _path_segments(
    path_data: str,
    *,
    translate_x: float,
    translate_y: float,
) -> list[tuple[tuple[float, float], tuple[float, float]]]:
    """Approximate the source's absolute M/L/C/Z path geometry as segments."""
    tokens = re.findall(
        r"[A-Za-z]|[-+]?(?:\d*\.\d+|\d+\.?\d*)(?:[eE][-+]?\d+)?",
        path_data,
    )
    segments: list[tuple[tuple[float, float], tuple[float, float]]] = []
    current = (0.0, 0.0)
    subpath_start = current
    command: str | None = None
    index = 0

    def point(offset: int) -> tuple[float, float]:
        return float(tokens[index + offset]), float(tokens[index + offset + 1])

    while index < len(tokens):
        token = tokens[index]
        if token.isalpha():
            command = token
            index += 1
        if command is None:
            raise ValueError(f"path data has coordinates without a command: {path_data}")
        if command not in {"M", "L", "C", "Z"}:
            raise ValueError(f"unsupported path command {command!r}")

        if command == "M":
            current = point(0)
            subpath_start = current
            index += 2
            command = "L"
        elif command == "L":
            end = point(0)
            segments.append(_line_segment(current, end))
            current = end
            index += 2
        elif command == "C":
            control_1 = point(0)
            control_2 = point(2)
            end = point(4)
            previous = current
            for sample in range(1, 25):
                t = sample / 24
                inverse = 1 - t
                sampled = (
                    inverse**3 * current[0]
                    + 3 * inverse**2 * t * control_1[0]
                    + 3 * inverse * t**2 * control_2[0]
                    + t**3 * end[0],
                    inverse**3 * current[1]
                    + 3 * inverse**2 * t * control_1[1]
                    + 3 * inverse * t**2 * control_2[1]
                    + t**3 * end[1],
                )
                segments.append(_line_segment(previous, sampled))
                previous = sampled
            current = end
            index += 6
        else:
            segments.append(_line_segment(current, subpath_start))
            current = subpath_start
            command = None

    return [
        (
            (start[0] + translate_x, start[1] + translate_y),
            (end[0] + translate_x, end[1] + translate_y),
        )
        for start, end in segments
    ]


def _geometry_segments(
    root: ET.Element,
    *,
    offset_x: float,
    offset_y: float,
) -> list[tuple[tuple[float, float], tuple[float, float]]]:
    """Return visible vector boundaries in standalone-SVG coordinates."""
    segments: list[tuple[tuple[float, float], tuple[float, float]]] = []

    def visit(element: ET.Element, translate_x: float, translate_y: float) -> None:
        local_translate_x, local_translate_y = _group_translation(element)
        translate_x += local_translate_x
        translate_y += local_translate_y
        local_name = element.tag.rsplit("}", 1)[-1]
        fill = element.attrib.get("fill", "#000000").lower()
        classes = element.attrib.get("class", "").split()
        stroke = element.attrib.get(
            "stroke", "#000000" if "symbol-line" in classes else "none"
        ).lower()
        visible = fill != "none" or stroke != "none"

        if visible and local_name == "rect":
            x = _number(element.attrib.get("x", "0")) + translate_x
            y = _number(element.attrib.get("y", "0")) + translate_y
            width = _number(element.attrib.get("width", "0"))
            height = _number(element.attrib.get("height", "0"))
            corners = (
                (x, y),
                (x + width, y),
                (x + width, y + height),
                (x, y + height),
            )
            segments.extend(
                _line_segment(corners[index], corners[(index + 1) % 4]) for index in range(4)
            )
        elif visible and local_name == "ellipse":
            center_x = _number(element.attrib["cx"]) + translate_x
            center_y = _number(element.attrib["cy"]) + translate_y
            radius_x = _number(element.attrib["rx"])
            radius_y = _number(element.attrib["ry"])
            points = [
                (
                    center_x + radius_x * math.cos(2 * math.pi * index / 96),
                    center_y + radius_y * math.sin(2 * math.pi * index / 96),
                )
                for index in range(96)
            ]
            segments.extend(
                _line_segment(points[index], points[(index + 1) % len(points)])
                for index in range(len(points))
            )
        elif visible and local_name == "path":
            segments.extend(
                _path_segments(
                    element.attrib.get("d", ""),
                    translate_x=translate_x,
                    translate_y=translate_y,
                )
            )

        for child in element:
            visit(child, translate_x, translate_y)

    visit(root, offset_x, offset_y)
    return segments


def _closest_point_on_segment(
    target: tuple[float, float],
    segment: tuple[tuple[float, float], tuple[float, float]],
) -> tuple[float, float]:
    start, end = segment
    delta_x = end[0] - start[0]
    delta_y = end[1] - start[1]
    length_squared = delta_x**2 + delta_y**2
    if not length_squared:
        return start
    ratio = ((target[0] - start[0]) * delta_x + (target[1] - start[1]) * delta_y) / length_squared
    ratio = min(1.0, max(0.0, ratio))
    return start[0] + ratio * delta_x, start[1] + ratio * delta_y


def _axis_anchor(
    segments: list[tuple[tuple[float, float], tuple[float, float]]],
    side: str,
    coordinate: float,
) -> tuple[float, float] | None:
    """Intersect a cardinal ray with ink without shifting the pipe's axis."""
    vertical = side in {"top", "bottom"}
    across = 0 if vertical else 1
    along = 1 - across
    hits = []
    for start, end in segments:
        delta = end[across] - start[across]
        if abs(delta) < 1e-9:
            if abs(start[across] - coordinate) < 1e-8:
                hits.extend((start[along], end[along]))
        else:
            fraction = (coordinate - start[across]) / delta
            if -1e-9 <= fraction <= 1 + 1e-9:
                hits.append(start[along] + fraction * (end[along] - start[along]))
    if not hits:
        return None
    reach = min(hits) if side in {"top", "left"} else max(hits)
    return (coordinate, reach) if vertical else (reach, coordinate)


def _geometry_anchors(
    segments: list[tuple[tuple[float, float], tuple[float, float]]],
    *,
    width: float,
    height: float,
) -> dict[str, tuple[float, float]]:
    if not segments:
        raise ValueError("rendered symbol has no visible vector geometry")
    xs = [point[0] for segment in segments for point in segment]
    ys = [point[1] for segment in segments for point in segment]
    center_x, center_y = (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2
    targets = {
        "top": (center_x, 0.0),
        "right": (width, center_y),
        "bottom": (center_x, height),
        "left": (0.0, center_y),
    }
    anchors: dict[str, tuple[float, float]] = {}
    for side, target in targets.items():
        axis = center_x if side in {"top", "bottom"} else center_y
        intersection = _axis_anchor(segments, side, axis)
        if intersection is not None:
            anchors[side] = intersection
            continue
        # Disconnected artwork may have no ink on the central ray. The
        # nearest-boundary fallback remains available for that case only.
        candidates = [_closest_point_on_segment(target, segment) for segment in segments]
        anchors[side] = min(
            candidates,
            key=lambda point: (point[0] - target[0]) ** 2 + (point[1] - target[1]) ** 2,
        )
    return anchors


def _rendered_geometry_origin(
    group: ET.Element,
    geometry: ET.Element,
) -> tuple[float, float]:
    """Locate the source cell's top-left point in rendered SVG coordinates."""
    width = _number(geometry.attrib["width"])
    height = _number(geometry.attrib["height"])
    translate_x, translate_y = _group_translation(group)
    tolerance = 0.01

    for rect in group.iter(f"{SVG}rect"):
        if (
            abs(_number(rect.attrib.get("width", "0")) - width) <= tolerance
            and abs(_number(rect.attrib.get("height", "0")) - height) <= tolerance
        ):
            return (
                _number(rect.attrib.get("x", "0")) + translate_x,
                _number(rect.attrib.get("y", "0")) + translate_y,
            )

    for ellipse in group.iter(f"{SVG}ellipse"):
        radius_x = _number(ellipse.attrib.get("rx", "0"))
        radius_y = _number(ellipse.attrib.get("ry", "0"))
        if abs(radius_x * 2 - width) <= tolerance and abs(radius_y * 2 - height) <= tolerance:
            return (
                _number(ellipse.attrib["cx"]) - radius_x + translate_x,
                _number(ellipse.attrib["cy"]) - radius_y + translate_y,
            )

    # The first valve has no invisible geometry rectangle. Its path consists
    # only of absolute M/L coordinate pairs and spans the complete source cell.
    for path in group.iter(f"{SVG}path"):
        commands = set(re.findall(r"[A-Za-z]", path.attrib.get("d", "")))
        if not commands.issubset({"M", "L", "Z"}):
            continue
        numbers = [
            float(value) for value in re.findall(r"[-+]?(?:\d*\.\d+|\d+\.?\d*)", path.attrib["d"])
        ]
        if len(numbers) < 4 or len(numbers) % 2:
            continue
        x_values = numbers[0::2]
        y_values = numbers[1::2]
        if (
            abs(max(x_values) - min(x_values) - width) <= tolerance
            and abs(max(y_values) - min(y_values) - height) <= tolerance
        ):
            return min(x_values) + translate_x, min(y_values) + translate_y

    raise ValueError("cannot derive rendered geometry origin from the first symbol")


def _clear_suction_support(element: ET.Element, spec: SymbolSpec) -> None:
    """Leave an open pipe lane between the vertical pump's two support feet."""
    if spec.slug != "pid_centrifugal_pump_2":
        return
    support, casing, *_ = list(element.iter(f"{SVG}path"))
    feet = _path_segments(support.attrib["d"], translate_x=0, translate_y=0)
    body = _path_segments(casing.attrib["d"], translate_x=0, translate_y=0)
    if len(feet) != 3:
        raise ValueError("vertical pump support geometry changed")
    shoulder_l, base_l = feet[0]
    base_r, shoulder_r = feet[2]
    mouth_l, mouth_r = body[0][0], body[-1][1]

    def polygon(points: tuple[tuple[float, float], ...]) -> str:
        return "M " + " L ".join(f"{_format(x)} {_format(y)}" for x, y in points) + " Z"

    # The original continuous filled base hides the suction mouth, so a
    # correctly located pipe appears to terminate inside solid equipment.
    support.set(
        "d",
        polygon((shoulder_l, base_l, (mouth_l[0], base_l[1]), mouth_l))
        + " "
        + polygon((mouth_r, (mouth_r[0], base_r[1]), base_r, shoulder_r)),
    )


def _remove_nonportable_content(element: ET.Element) -> None:
    """Remove draw.io/browser-only content and presentation overrides."""
    for child in list(element):
        local_name = child.tag.rsplit("}", 1)[-1]
        if local_name in {"switch", "foreignObject", "image"}:
            element.remove(child)
            continue
        _remove_nonportable_content(child)

    for attribute in (
        "data-cell-id",
        "id",
        "pointer-events",
        "requiredFeatures",
        "style",
    ):
        element.attrib.pop(attribute, None)

    # Prune grouping shells left empty after foreignObject removal.
    for child in list(element):
        if child.tag == f"{SVG}g" and not list(child) and not (child.text or "").strip():
            element.remove(child)

    local_name = element.tag.rsplit("}", 1)[-1]
    classes = set(element.attrib.get("class", "").split())

    fill = element.attrib.get("fill", "").lower()
    if fill == "#ffffff":
        classes.add("symbol-fill")
        element.attrib.pop("fill")
    elif fill == "#000000":
        has_text = local_name == "text" or any(
            descendant.tag == f"{SVG}text" for descendant in element.iter()
        )
        classes.add("symbol-text" if has_text else "symbol-solid")
        element.attrib.pop("fill")

    if element.attrib.get("stroke", "").lower() == "#000000":
        classes.add("symbol-line")
        element.attrib.pop("stroke")
        # Line thickness is intentionally controlled by the theme block.
        element.attrib.pop("stroke-width", None)

    if classes:
        ordered_classes = [name for name in THEME_CLASS_ORDER if name in classes]
        ordered_classes.extend(sorted(classes.difference(THEME_CLASS_ORDER)))
        element.set("class", " ".join(ordered_classes))


def _add_text(
    parent: ET.Element,
    text: str,
    *,
    x: float,
    y: float,
    font_size: int,
) -> None:
    node = ET.SubElement(
        parent,
        f"{SVG}text",
        {
            "x": _format(x),
            "y": _format(y),
            "class": "symbol-text",
            "font-family": "Arial",
            "font-size": str(font_size),
            "text-anchor": "middle",
        },
    )
    node.text = text


def _add_connection_points(
    parent: ET.Element,
    *,
    anchors: dict[str, tuple[float, float]],
    width: float,
    height: float,
) -> None:
    """Add portable cardinal connection ports for diagram-canvas integrations."""
    radius = 3.0
    group = ET.SubElement(
        parent,
        f"{SVG}g",
        {
            "class": "connection-points",
            "data-role": "connection-points",
            "aria-hidden": "true",
        },
    )
    for side in ("top", "right", "bottom", "left"):
        x, y = anchors[side]
        ET.SubElement(
            group,
            f"{SVG}circle",
            {
                "class": "connection-port",
                "data-connection-side": side,
                "data-anchor-x": _format(x / width),
                "data-anchor-y": _format(y / height),
                "cx": _format(x),
                "cy": _format(y),
                "r": _format(radius),
            },
        )


def _connection_anchors(
    spec: SymbolSpec,
    *,
    width: float,
    height: float,
    padding: float,
    geometry_segments: list[tuple[tuple[float, float], tuple[float, float]]] | None = None,
) -> dict[str, tuple[float, float]]:
    """Return connection points on visible geometry rather than viewport edges."""
    # diagrams.net wraps rendered geometry in translate(0.5, 0.5). Account for
    # that half-pixel offset as well as the extraction padding so connectors
    # terminate on the actual outline with no visible gap.
    left = padding + 0.5
    top = padding + 0.5
    right = width - padding + 0.5
    bottom = height - padding + 0.5
    if geometry_segments:
        # Collection and primary sheets have different half-pixel origins.
        # Their real vector bounds, not the padding formula, own the nozzles.
        xs = [point[0] for segment in geometry_segments for point in segment]
        ys = [point[1] for segment in geometry_segments for point in segment]
        left, right, top, bottom = min(xs), max(xs), min(ys), max(ys)
    center_x = (left + right) / 2
    center_y = (top + bottom) / 2
    anchors = (
        _geometry_anchors(geometry_segments, width=width, height=height)
        if geometry_segments is not None
        else {
            "top": (center_x, top),
            "right": (right, center_y),
            "bottom": (center_x, bottom),
            "left": (left, center_y),
        }
    )

    if "valve" in spec.slug and geometry_segments:
        # Actuators enlarge the viewport above the bow tie. The nearest ink
        # to its midpoint is on the valve edge, but above its flow axis. Use
        # the matching vertical faces of the body, excluding stem/actuator
        # edges, so all extracted gate-valve variants share the real axis.
        faces: dict[tuple[float, float], list[float]] = {}
        for start, end in geometry_segments:
            if abs(start[0] - end[0]) > 1e-6 or abs(start[1] - end[1]) < height * 0.15:
                continue
            low, high = sorted((start[1], end[1]))
            faces.setdefault((round(low, 5), round(high, 5)), []).append(start[0])
        spans = [
            (max(xs) - min(xs), min(xs), max(xs), (low + high) / 2)
            for (low, high), xs in faces.items()
            if len(xs) >= 2
        ]
        paired_body = False
        if spans:
            span, inlet_x, outlet_x, flow_y = max(spans)
            if span > width * 0.5:
                paired_body = True
                anchors["left"], anchors["right"] = (inlet_x, flow_y), (outlet_x, flow_y)
                # A branch/bleed or actuator changes the overall silhouette,
                # but the stem remains over the middle of the valve body.
                for side in ("top", "bottom"):
                    point = _axis_anchor(geometry_segments, side, (inlet_x + outlet_x) / 2)
                    if point is not None:
                        anchors[side] = point

        if "angle" in spec.slug and not paired_body:
            # Angle valves have one vertical process face and one horizontal
            # face. Their two nozzles need not share the viewport midpoint.
            verticals = [
                (a[0], (a[1] + b[1]) / 2)
                for a, b in geometry_segments
                if abs(a[0] - b[0]) < 1e-8 and abs(a[1] - b[1]) > height * 0.15
            ]
            horizontals = [
                ((a[0] + b[0]) / 2, a[1])
                for a, b in geometry_segments
                if abs(a[1] - b[1]) < 1e-8 and abs(a[0] - b[0]) > width * 0.2
            ]
            if verticals and horizontals:
                anchors["right"] = max(verticals, key=lambda p: p[0])
                anchors["bottom"] = max(horizontals, key=lambda p: p[1])
                for side, axis in (("top", anchors["bottom"][0]), ("left", anchors["right"][1])):
                    point = _axis_anchor(geometry_segments, side, axis)
                    if point is not None:
                        anchors[side] = point

    if spec.slug in {
        "pid_condenser",
        "pid_heater",
        "pid_shell_and_tube_heat_exchanger_1",
        "pid_shell_and_tube_heat_exchanger_2",
    }:
        # Sampling the circular outline independently gave each port a
        # slightly different axis. These are the source ellipse's four
        # exact cardinal intersections (radius 30 in each source cell).
        anchors.update(
            left=(center_x - 30, center_y),
            right=(center_x + 30, center_y),
            top=(center_x, center_y - 30),
            bottom=(center_x, center_y + 30),
        )

    if spec.slug == "pid_reboiler":
        # The tube head and shell have different vertical centers. The
        # viewport-midpoint projection hit the head's curved upper lip.
        anchors["left"] = (left, top + 22.5)
        anchors["right"] = (right, top + 15)

    # Centrifugal-pump discharge nozzles are offset from the bounding-box
    # midpoint. These anchors land at the center of the actual nozzle opening.
    if spec.slug == "pid_centrifugal_pump_1":
        anchors["right"] = (right, top + 10)
        # The inlet opening spans y=top+25 .. top+35. Nearest-outline
        # projection selected its lower lip, visibly off the suction axis.
        anchors["left"] = (left + 0.6, top + 30)
    elif spec.slug == "pid_centrifugal_pump_2":
        anchors["top"] = (left + 9.985, top)
        anchors["bottom"] = (left + 29.955, top + 69.4)
    elif spec.slug == "pid_centrifugal_pump_3":
        anchors["left"] = (left, top + 10)
        anchors["right"] = (right - 0.6, top + 30)
    elif spec.slug == "pid_centrifugal_compressor":
        anchors["left"] = (left + 0.6, top + 31.345)
        anchors["right"] = (right, top + 10.45)
    elif spec.slug == "pid_gas_blower":
        anchors["left"] = (left, top + 9.875)
        if geometry_segments:
            point = _axis_anchor(geometry_segments, "right", top + 30)
            if point is not None:
                anchors["right"] = point
    elif spec.slug == "pid_air_separator":
        anchors["left"] = (left, top + 83)
        anchors["right"] = (right, top + 23)
    elif spec.slug == "pid_furnace":
        anchors["left"] = (left, top + 54.5)
        anchors["right"] = (right, top + 79.5)
    elif spec.slug == "pid_hairpin_exchanger":
        # Two tube mouths are on the left. The cardinal left port uses the
        # lower mouth; the upper mouth can be selected on the perimeter.
        anchors["left"] = (left, top + 21.5)
    elif (
        spec.slug in {"pid_dome_vessel", "pid_vessel_dome", "pid_pit_vessel", "pid_vessel_pit"}
        and geometry_segments
    ):
        # The neck is offset from the horizontal drum's axis. Centering on
        # the entire silhouette attached a vertical pipe beside the neck.
        dome = "dome" in spec.slug
        points = [p for segment in geometry_segments for p in segment]
        anchors["top" if dome else "bottom"] = (
            min(points, key=lambda p: p[1]) if dome else max(points, key=lambda p: p[1])
        )
        flow_y = top + (35.02 if dome else 20)
        for side in ("left", "right"):
            point = _axis_anchor(geometry_segments, side, flow_y)
            if point is not None:
                anchors[side] = point
    elif spec.slug == "pid_forced_flow_air_cooler":
        # The fan and its legs extend below the exchanger, shifting the
        # silhouette midpoint down onto the bottom of the tube bundle.
        anchors["left"] = (left, top + 7.5)
        anchors["right"] = (right, top + 7.5)
    return anchors


def write_connection_manifest(asset_dirs: list[Path], target: Path) -> None:
    """Write browser-ready normalized port metadata for all standalone SVGs."""
    manifest: dict[str, dict[str, dict[str, float]]] = {}
    for asset_dir in asset_dirs:
        for path in sorted(asset_dir.rglob("*.svg")):
            root = ET.parse(path).getroot()
            ports: dict[str, dict[str, float]] = {}
            for node in root.iter(f"{SVG}circle"):
                if "connection-port" not in node.attrib.get("class", "").split():
                    continue
                side = node.attrib["data-connection-side"]
                ports[side] = {
                    "x": float(node.attrib["data-anchor-x"]),
                    "y": float(node.attrib["data-anchor-y"]),
                }
            if set(ports) != {"top", "right", "bottom", "left"}:
                raise ValueError(f"{path} does not define four cardinal connection ports")
            manifest[path.relative_to(target.parent).as_posix()] = ports

    payload = json.dumps(manifest, indent=2, sort_keys=True)
    target.write_text(
        "// Generated by tools/extract_svg_symbols.py.\n"
        f"window.HMI_SVG_CONNECTIONS = Object.freeze({payload});\n",
        encoding="utf-8",
    )


def write_symbol_data_manifest(asset_dirs: list[Path], target: Path) -> None:
    """Write self-contained SVG data URLs for offline image export."""
    manifest: dict[str, str] = {}
    for asset_dir in asset_dirs:
        for path in sorted(asset_dir.rglob("*.svg")):
            payload = base64.b64encode(path.read_bytes()).decode("ascii")
            key = path.relative_to(target.parent).as_posix()
            manifest[key] = f"data:image/svg+xml;base64,{payload}"

    payload = json.dumps(manifest, indent=2, sort_keys=True)
    target.write_text(
        "// Generated by tools/extract_svg_symbols.py.\n"
        f"window.HMI_SVG_DATA = Object.freeze({payload});\n",
        encoding="utf-8",
    )


def extract(source: Path, output_dir: Path) -> list[Path]:
    """Extract the configured symbols and return their output paths."""
    ET.register_namespace("", SVG_NS)
    source_root = ET.parse(source).getroot()
    drawio = source_root.attrib.get("content")
    if not drawio:
        raise ValueError(f"{source} has no embedded draw.io graph")

    graph_root = ET.fromstring(drawio)
    cells = [
        cell
        for cell in graph_root.findall(".//mxCell")
        if cell.attrib.get("vertex") == "1" and cell.attrib.get("parent") == "1"
    ]
    rendered_parent = next(
        group for group in source_root.iter(f"{SVG}g") if group.attrib.get("data-cell-id") == "1"
    )
    rendered_groups = [child for child in rendered_parent if child.tag == f"{SVG}g"]
    if len(cells) != len(rendered_groups) or len(cells) != len(SYMBOLS):
        raise ValueError(
            "source structure changed: expected "
            f"{len(SYMBOLS)} symbols, found {len(cells)} cells and "
            f"{len(rendered_groups)} rendered groups"
        )

    spec_by_id = {spec.cell_id: spec for spec in SYMBOLS}
    if set(spec_by_id) != {cell.attrib["id"] for cell in cells}:
        raise ValueError("symbol manifest does not match the source cell IDs")

    # diagrams.net exported this page with a fixed 6.5/16.5 offset between
    # its editable mxGeometry coordinates and the rendered SVG coordinates.
    # Derive it from the first instrument bubble instead of hard-coding it.
    first_geometry = cells[0].find("mxGeometry")
    first_ellipse = next(rendered_groups[0].iter(f"{SVG}ellipse"))
    if first_geometry is None:
        raise ValueError("first symbol has no geometry")
    translate = next(rendered_groups[0].iter(f"{SVG}g")).attrib.get("transform", "")
    match = re.fullmatch(r"translate\(([-\d.]+),([-\d.]+)\)", translate)
    translate_x, translate_y = (
        (float(match.group(1)), float(match.group(2))) if match else (0.0, 0.0)
    )
    offset_x = (
        _number(first_ellipse.attrib["cx"])
        - _number(first_ellipse.attrib["rx"])
        + translate_x
        - _number(first_geometry.attrib["x"])
    )
    offset_y = (
        _number(first_ellipse.attrib["cy"])
        - _number(first_ellipse.attrib["ry"])
        + translate_y
        - _number(first_geometry.attrib["y"])
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    outputs: list[Path] = []
    padding = 4.0
    for cell, rendered_group in zip(cells, rendered_groups, strict=True):
        spec = spec_by_id[cell.attrib["id"]]
        geometry = cell.find("mxGeometry")
        if geometry is None:
            raise ValueError(f"cell {spec.cell_id} has no geometry")
        x = _number(geometry.attrib.get("x", "0")) + offset_x
        y = _number(geometry.attrib.get("y", "0")) + offset_y
        width = _number(geometry.attrib["width"])
        height = _number(geometry.attrib["height"])
        view_x, view_y = x - padding, y - padding
        canvas_width, canvas_height = width + 2 * padding, height + 2 * padding

        root = ET.Element(
            f"{SVG}svg",
            {
                "width": _format(canvas_width),
                "height": _format(canvas_height),
                "viewBox": f"0 0 {_format(canvas_width)} {_format(canvas_height)}",
                "role": "img",
                "aria-labelledby": "title desc",
            },
        )
        title = ET.SubElement(root, f"{SVG}title", {"id": "title"})
        title.text = spec.title
        description = ET.SubElement(root, f"{SVG}desc", {"id": "desc"})
        description.text = spec.description
        theme = ET.SubElement(
            root,
            f"{SVG}style",
            {"id": "symbol-theme", "type": "text/css"},
        )
        theme.text = THEME_CSS
        wrapper = ET.SubElement(
            root,
            f"{SVG}g",
            {
                "transform": f"translate({_format(-view_x)} {_format(-view_y)})",
                "shape-rendering": "geometricPrecision",
            },
        )
        clean_group = copy.deepcopy(rendered_group)
        _remove_nonportable_content(clean_group)
        _clear_suction_support(clean_group, spec)
        wrapper.append(clean_group)

        if spec.cell_id in {"2", "3", "4"}:
            bubble_center_x = x + width / 2
            bubble_center_y = y + 25
            _add_text(
                wrapper,
                "TI",
                x=bubble_center_x,
                y=bubble_center_y - 5,
                font_size=12,
            )
            _add_text(
                wrapper,
                "##",
                x=bubble_center_x,
                y=bubble_center_y + 15,
                font_size=12,
            )
        elif spec.cell_id == "21":
            _add_text(
                wrapper,
                "M",
                x=x + width / 2,
                y=y + height / 2 + 15,
                font_size=45,
            )

        anchors = _connection_anchors(
            spec,
            width=canvas_width,
            height=canvas_height,
            padding=padding,
            geometry_segments=_geometry_segments(
                clean_group,
                offset_x=-view_x,
                offset_y=-view_y,
            ),
        )
        _add_connection_points(
            root,
            anchors=anchors,
            width=canvas_width,
            height=canvas_height,
        )

        ET.indent(root, space="  ")
        target = output_dir / f"{spec.slug}.svg"
        ET.ElementTree(root).write(
            target,
            encoding="utf-8",
            xml_declaration=True,
            short_empty_elements=True,
        )
        outputs.append(target)
    return outputs


def extract_collections(source_dir: Path, output_root: Path) -> list[Path]:
    """Extract every top-level symbol from the equipment collection sheets."""
    ET.register_namespace("", SVG_NS)
    outputs: list[Path] = []
    padding = 4.0

    for collection in COLLECTION_SOURCES:
        source_path = source_dir / collection.filename
        source_root = ET.parse(source_path).getroot()
        drawio = source_root.attrib.get("content")
        if not drawio:
            raise ValueError(f"{source_path} has no embedded draw.io graph")

        graph_root = ET.fromstring(drawio)
        cells = [
            cell
            for cell in graph_root.findall(".//mxCell")
            if cell.attrib.get("vertex") == "1" and cell.attrib.get("parent") == "1"
        ]
        rendered_parent = next(
            group
            for group in source_root.iter(f"{SVG}g")
            if group.attrib.get("data-cell-id") == "1"
        )
        rendered_groups = [child for child in rendered_parent if child.tag == f"{SVG}g"]
        if (
            len(cells) != collection.expected_count
            or len(rendered_groups) != collection.expected_count
        ):
            raise ValueError(
                f"{collection.filename} structure changed: expected "
                f"{collection.expected_count} symbols, found {len(cells)} cells "
                f"and {len(rendered_groups)} rendered groups"
            )

        first_geometry = cells[0].find("mxGeometry")
        if first_geometry is None:
            raise ValueError(f"{collection.filename} first cell has no geometry")
        rendered_x, rendered_y = _rendered_geometry_origin(
            rendered_groups[0],
            first_geometry,
        )
        offset_x = rendered_x - _number(first_geometry.attrib.get("x", "0"))
        offset_y = rendered_y - _number(first_geometry.attrib.get("y", "0"))

        output_dir = output_root / collection.folder
        output_dir.mkdir(parents=True, exist_ok=True)
        for stale_path in output_dir.glob("*.svg"):
            stale_path.unlink()

        duplicate_counts: dict[str, int] = {}
        for cell, rendered_group in zip(cells, rendered_groups, strict=True):
            spec = _collection_symbol_spec(
                cell,
                source=collection,
                duplicate_counts=duplicate_counts,
            )
            geometry = cell.find("mxGeometry")
            if geometry is None:
                raise ValueError(f"cell {spec.cell_id} has no geometry")
            x = _number(geometry.attrib.get("x", "0")) + offset_x
            y = _number(geometry.attrib.get("y", "0")) + offset_y
            width = _number(geometry.attrib["width"])
            height = _number(geometry.attrib["height"])
            view_x, view_y = x - padding, y - padding
            canvas_width, canvas_height = width + 2 * padding, height + 2 * padding

            root = ET.Element(
                f"{SVG}svg",
                {
                    "width": _format(canvas_width),
                    "height": _format(canvas_height),
                    "viewBox": (f"0 0 {_format(canvas_width)} {_format(canvas_height)}"),
                    "role": "img",
                    "aria-labelledby": "title desc",
                },
            )
            title = ET.SubElement(root, f"{SVG}title", {"id": "title"})
            title.text = spec.title
            description = ET.SubElement(root, f"{SVG}desc", {"id": "desc"})
            description.text = spec.description
            theme = ET.SubElement(
                root,
                f"{SVG}style",
                {"id": "symbol-theme", "type": "text/css"},
            )
            theme.text = THEME_CSS
            wrapper = ET.SubElement(
                root,
                f"{SVG}g",
                {
                    "transform": f"translate({_format(-view_x)} {_format(-view_y)})",
                    "shape-rendering": "geometricPrecision",
                },
            )
            clean_group = copy.deepcopy(rendered_group)
            _remove_nonportable_content(clean_group)
            _clear_suction_support(clean_group, spec)
            wrapper.append(clean_group)

            cell_text = re.sub(r"<[^>]+>", "", cell.attrib.get("value", "")).strip()
            if cell_text:
                properties = _style_properties(cell.attrib.get("style", ""))
                font_size = int(float(properties.get("fontSize", "14")))
                _add_text(
                    wrapper,
                    cell_text,
                    x=x + width / 2,
                    y=y + height / 2 + font_size / 3,
                    font_size=font_size,
                )

            anchors = _connection_anchors(
                spec,
                width=canvas_width,
                height=canvas_height,
                padding=padding,
                geometry_segments=_geometry_segments(
                    clean_group,
                    offset_x=-view_x,
                    offset_y=-view_y,
                ),
            )
            _add_connection_points(
                root,
                anchors=anchors,
                width=canvas_width,
                height=canvas_height,
            )

            ET.indent(root, space="  ")
            target = output_dir / f"{spec.slug}.svg"
            ET.ElementTree(root).write(
                target,
                encoding="utf-8",
                xml_declaration=True,
                short_empty_elements=True,
            )
            outputs.append(target)

    return outputs


def main() -> int:
    repository = Path(__file__).resolve().parents[1]
    source = repository / "source" / "SVGSymbols.svg"
    output_dir = repository / "symbols" / "pid"
    outputs = extract(source, output_dir)
    collection_outputs = extract_collections(
        repository / "source" / "collections",
        repository / "symbols" / "collections",
    )
    asset_dirs = [
        repository / "symbols" / "isa",
        output_dir,
        repository / "symbols" / "collections",
    ]
    manifest = repository / "connection-manifest.js"
    data_manifest = repository / "symbol-data-manifest.js"
    write_connection_manifest(
        asset_dirs,
        manifest,
    )
    write_symbol_data_manifest(asset_dirs, data_manifest)
    print(f"Extracted {len(outputs)} symbols to {output_dir}")
    print(
        f"Extracted {len(collection_outputs)} collection symbols to "
        f"{repository / 'symbols' / 'collections'}"
    )
    print(f"Wrote connection metadata to {manifest}")
    print(f"Wrote embedded symbol data to {data_manifest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
