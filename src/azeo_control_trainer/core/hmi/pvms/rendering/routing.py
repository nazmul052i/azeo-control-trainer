"""Deterministic, Qt-free orthogonal routing for process connectors.

The Graphics Designer and the operator viewer must derive the same pipe path
from the same document.  This module therefore owns geometry only: no scene,
widget, painter, or mutable display object crosses the boundary.  Automatic
paths remain derived state; only explicitly authored manual waypoints need to
be persisted by the caller.
"""
from __future__ import annotations

from dataclasses import dataclass
import heapq
import math
from typing import Iterable, Sequence


DEFAULT_GRID = 8.0
DEFAULT_CLEARANCE = 12.0
DEFAULT_BEND_PENALTY = 24.0
DEFAULT_CROSSING_PENALTY = 16.0

CARDINAL_NORMALS = {
    "n": (0, -1),
    "e": (1, 0),
    "s": (0, 1),
    "w": (-1, 0),
}
_DIRECTIONS = ((1, 0, "e"), (0, 1, "s"), (-1, 0, "w"),
               (0, -1, "n"))
_DIRECTION_INDEX = {name: index for index, (*_delta, name)
                    in enumerate(_DIRECTIONS)}


@dataclass(frozen=True, order=True)
class Point:
    """One finite scene-space point."""

    x: float
    y: float

    @classmethod
    def from_value(cls, value) -> "Point":
        if isinstance(value, cls):
            return value
        if isinstance(value, dict):
            return cls(float(value["x"]), float(value["y"]))
        return cls(float(value[0]), float(value[1]))

    @property
    def finite(self) -> bool:
        return math.isfinite(self.x) and math.isfinite(self.y)

    def moved(self, dx: float, dy: float) -> "Point":
        return Point(self.x + dx, self.y + dy)

    def to_list(self) -> list[float]:
        return [self.x, self.y]


@dataclass(frozen=True)
class Box:
    """Axis-aligned scene-space bounds."""

    left: float
    top: float
    right: float
    bottom: float

    def __post_init__(self) -> None:
        if self.right < self.left or self.bottom < self.top:
            raise ValueError("box edges are reversed")
        if not all(math.isfinite(value) for value in (
                self.left, self.top, self.right, self.bottom)):
            raise ValueError("box edges must be finite")

    @classmethod
    def from_xywh(cls, x: float, y: float,
                  width: float, height: float) -> "Box":
        return cls(float(x), float(y), float(x) + float(width),
                   float(y) + float(height))

    def inflated(self, amount: float) -> "Box":
        amount = max(0.0, float(amount))
        return Box(self.left - amount, self.top - amount,
                   self.right + amount, self.bottom + amount)

    def contains(self, point: Point, *, strict: bool = False) -> bool:
        if strict:
            return (self.left < point.x < self.right
                    and self.top < point.y < self.bottom)
        return (self.left <= point.x <= self.right
                and self.top <= point.y <= self.bottom)

    def clipped(self, point: Point) -> Point:
        return Point(min(self.right, max(self.left, point.x)),
                     min(self.bottom, max(self.top, point.y)))


@dataclass(frozen=True)
class PortAnchor:
    """Exact endpoint and the direction in which the pipe leaves it."""

    point: Point
    normal: str
    name: str = ""

    def __post_init__(self) -> None:
        if self.normal not in CARDINAL_NORMALS:
            raise ValueError(f"invalid port normal {self.normal!r}")
        if not self.point.finite:
            raise ValueError("port point must be finite")

    def stub(self, distance: float) -> Point:
        dx, dy = CARDINAL_NORMALS[self.normal]
        return self.point.moved(dx * distance, dy * distance)


@dataclass(frozen=True)
class Obstacle:
    """A routed-around display object."""

    ident: str
    bounds: Box


@dataclass(frozen=True)
class Segment:
    """An existing open stroke which a new route may cross."""

    start: Point
    end: Point
    owner: str = ""


@dataclass(frozen=True)
class RouteIssue:
    severity: str
    message: str
    index: int = -1


@dataclass(frozen=True)
class RouteRequest:
    start: PortAnchor
    end: PortAnchor
    obstacles: tuple[Obstacle, ...] = ()
    vias: tuple[Point, ...] = ()
    bounds: Box | None = None
    existing_segments: tuple[Segment, ...] = ()
    grid: float = DEFAULT_GRID
    clearance: float = DEFAULT_CLEARANCE
    line_width: float = 2.0
    bend_penalty: float = DEFAULT_BEND_PENALTY
    crossing_penalty: float = DEFAULT_CROSSING_PENALTY
    max_nodes: int = 120_000


@dataclass(frozen=True)
class RouteResult:
    points: tuple[Point, ...]
    status: str = "ok"
    collisions: tuple[str, ...] = ()
    message: str = ""
    visited_nodes: int = 0
    used_fallback: bool = False

    @property
    def bends(self) -> int:
        return bend_count(self.points)

    @property
    def length(self) -> float:
        return polyline_length(self.points)


@dataclass(frozen=True)
class CrossoverCandidate:
    """Stable ordering information for one crossing participant."""

    ident: str
    mode: str = ""
    z: float = 0.0
    stack: int = 0

    @property
    def explicit(self) -> bool:
        return self.mode in ("gap", "jump")


def crossover_owner(first: CrossoverCandidate,
                    second: CrossoverCandidate) -> str | None:
    """Return the sole stroke which owns a gap/jump at a crossing.

    An explicit effect wins over a continuous stroke.  If both request an
    effect, the visually upper stroke wins; stable identity breaks the final
    tie so Studio and operator never draw two bridges at the same place.
    """
    if not first.explicit and not second.explicit:
        return None
    if first.explicit != second.explicit:
        return first.ident if first.explicit else second.ident
    ranked = sorted((first, second),
                    key=lambda item: (item.z, item.stack, item.ident))
    return ranked[-1].ident


def infer_port_normal(x: float, y: float) -> str:
    """Infer a normalized port's nearest outward edge deterministically."""
    distances = ((abs(float(x)), "w"), (abs(1.0 - float(x)), "e"),
                 (abs(float(y)), "n"), (abs(1.0 - float(y)), "s"))
    return min(distances, key=lambda row: (row[0], "wens".index(row[1])))[1]


def polyline_length(points: Sequence[Point]) -> float:
    return sum(abs(after.x - before.x) + abs(after.y - before.y)
               for before, after in zip(points, points[1:]))


def bend_count(points: Sequence[Point]) -> int:
    total = 0
    for before, point, after in zip(points, points[1:], points[2:]):
        incoming = (point.x - before.x, point.y - before.y)
        outgoing = (after.x - point.x, after.y - point.y)
        if bool(incoming[0]) != bool(outgoing[0]):
            total += 1
    return total


def simplify_orthogonal(points: Iterable[Point]) -> tuple[Point, ...]:
    """Remove duplicate and collinear points without moving any bend."""
    clean: list[Point] = []
    for raw in points:
        point = Point.from_value(raw)
        if clean and _same_point(clean[-1], point):
            continue
        clean.append(point)
        while len(clean) >= 3 and _collinear(
                clean[-3], clean[-2], clean[-1]):
            clean.pop(-2)
    return tuple(clean)


def validate_manual_waypoints(values, *, bounds: Box | None = None,
                              obstacles: Sequence[Obstacle] = ()) \
        -> tuple[RouteIssue, ...]:
    """Validate persisted manual waypoint values without raising."""
    issues: list[RouteIssue] = []
    previous: Point | None = None
    for index, value in enumerate(values or ()):
        try:
            point = Point.from_value(value)
        except (KeyError, TypeError, ValueError, IndexError):
            issues.append(RouteIssue(
                "error", "waypoint must contain numeric x and y", index))
            continue
        if not point.finite:
            issues.append(RouteIssue(
                "error", "waypoint coordinates must be finite", index))
            continue
        if bounds is not None and not bounds.contains(point):
            issues.append(RouteIssue(
                "error", "waypoint is outside the display page", index))
        for obstacle in obstacles:
            if obstacle.bounds.contains(point, strict=True):
                issues.append(RouteIssue(
                    "error", f"waypoint is inside {obstacle.ident}", index))
        if previous is not None and _same_point(previous, point):
            issues.append(RouteIssue(
                "warning", "waypoint duplicates the previous point", index))
        previous = point
    return tuple(issues)


def repair_manual_waypoints(values, *, grid: float = 0.0,
                            bounds: Box | None = None) -> tuple[Point, ...]:
    """Drop malformed points, clamp/snap valid ones, and simplify them."""
    repaired = []
    step = max(0.0, float(grid))
    for value in values or ():
        try:
            point = Point.from_value(value)
        except (KeyError, TypeError, ValueError, IndexError):
            continue
        if not point.finite:
            continue
        if bounds is not None:
            point = bounds.clipped(point)
        if step > 0:
            point = Point(round(point.x / step) * step,
                          round(point.y / step) * step)
        repaired.append(point)
    return simplify_orthogonal(repaired)


def move_orthogonal_segment(points: Sequence[Point], index: int,
                            coordinate: float, stub: float = 12.0) -> tuple[Point, ...]:
    """Slide a run across its axis, retaining both attached endpoints.

    End runs need a short departure leg: moving the first bend alone would
    pull the pipe off its nozzle or route it through the equipment body.
    """
    if not 0 <= index < len(points) - 1 or not math.isfinite(coordinate):
        raise ValueError("invalid segment or coordinate")
    a, b = points[index:index + 2]
    horizontal = abs(a.y - b.y) < 1e-6
    if not horizontal and abs(a.x - b.x) >= 1e-6:
        raise ValueError("only orthogonal runs can be slid")
    if a == b:
        return tuple(points)
    if abs(coordinate - (a.y if horizontal else a.x)) < 1e-6:
        return tuple(points)
    distance = abs(b.x - a.x) if horizontal else abs(b.y - a.y)
    offset = min(max(1.0, stub), distance / 3.0)
    dx = (1 if b.x > a.x else -1) * offset if horizontal else 0
    dy = (1 if b.y > a.y else -1) * offset if not horizontal else 0
    before, after = list(points[:index]), list(points[index + 2:])
    if index == 0:
        before = [a, a.moved(dx, dy)]
        a = before[-1]
    if index == len(points) - 2:
        after = [b.moved(-dx, -dy), b]
        b = after[0]
    shifted = (Point(a.x, coordinate), Point(b.x, coordinate)) if horizontal else (
        Point(coordinate, a.y), Point(coordinate, b.y))
    return simplify_orthogonal((*before, *shifted, *after))


class OrthogonalRouter:
    """Grid Manhattan A* with stable scoring and an honest fallback."""

    def route(self, request: RouteRequest) -> RouteResult:
        issue = self._request_issue(request)
        inflated = tuple(Obstacle(
            obstacle.ident,
            obstacle.bounds.inflated(
                max(0.0, request.clearance) + request.line_width / 2.0))
            for obstacle in request.obstacles)
        stub_distance = max(request.grid, request.clearance)
        opposite = {"n": "s", "e": "w", "s": "n", "w": "e"}
        start_dx, start_dy = CARDINAL_NORMALS[request.start.normal]
        gap_x = request.end.point.x - request.start.point.x
        gap_y = request.end.point.y - request.start.point.y
        if request.end.normal == opposite[request.start.normal]:
            axial_gap = gap_x * start_dx + gap_y * start_dy
            if axial_gap > 0:
                # Two nearby facing nozzles can be closer than two ordinary
                # stubs. Let them retain a short outward run instead of
                # crossing stubs and routing a six-segment loop between
                # equipment that should have one straight connection.
                stub_distance = min(stub_distance, axial_gap / 3.0)
        start_stub = request.start.stub(stub_distance)
        end_stub = request.end.stub(stub_distance)
        controls = (start_stub, *request.vias, end_stub)
        if issue:
            return self._fallback(request, controls, inflated, issue)
        waypoint_issues = validate_manual_waypoints(
            request.vias, bounds=request.bounds, obstacles=inflated)
        errors = [one.message for one in waypoint_issues
                  if one.severity == "error"]
        if errors:
            return self._fallback(
                request, controls, inflated, "; ".join(errors))

        path = [request.start.point, start_stub]
        visited = 0
        entering_direction = request.start.normal
        for index, (before, after) in enumerate(
                zip(controls, controls[1:])):
            leaving_direction = request.end.normal if index == \
                len(controls) - 2 else ""
            leg, leg_visited = self._route_leg(
                before, after, request, inflated,
                entering_direction=entering_direction,
                leaving_direction=leaving_direction,
                require_entering=index == 0,
                require_leaving=index == len(controls) - 2)
            visited += leg_visited
            if not leg:
                return self._fallback(
                    request, controls, inflated,
                    "automatic router found no clear orthogonal path",
                    visited)
            path.extend(leg[1:])
            entering_direction = _last_direction(leg)
        path.append(request.end.point)
        points = simplify_orthogonal(path)
        collisions = _collisions(points, inflated)
        if collisions:
            return self._fallback(
                request, controls, inflated,
                "automatic route intersects an obstacle", visited)
        return RouteResult(points, visited_nodes=visited)

    @staticmethod
    def _request_issue(request: RouteRequest) -> str:
        if request.start.point == request.end.point:
            return "pipe endpoints are identical"
        if not math.isfinite(request.grid) or request.grid <= 0:
            return "routing grid must be a positive finite number"
        if not math.isfinite(request.clearance) or request.clearance < 0:
            return "routing clearance must be a non-negative finite number"
        if not math.isfinite(request.line_width) or request.line_width < 0:
            return "line width must be a non-negative finite number"
        if request.max_nodes < 16:
            return "routing search budget is too small"
        return ""

    def _route_leg(self, start: Point, goal: Point, request: RouteRequest,
                   obstacles: Sequence[Obstacle], *,
                   entering_direction: str = "",
                   leaving_direction: str = "",
                   require_entering: bool = False,
                   require_leaving: bool = False) \
            -> tuple[tuple[Point, ...], int]:
        direct, candidates = [], []
        # Facing, offset nozzles need two elbows. Put the transverse run in
        # the free span instead of defaulting to a 12 px jog at one nozzle.
        if (entering_direction, leaving_direction) in (("e", "w"), ("w", "e")) \
                and (goal.x - start.x) * CARDINAL_NORMALS[entering_direction][0] > 0:
            middle = (start.x + goal.x) / 2.0
            candidates.append((start, Point(middle, start.y), Point(middle, goal.y), goal))
        elif (entering_direction, leaving_direction) in (("s", "n"), ("n", "s")) \
                and (goal.y - start.y) * CARDINAL_NORMALS[entering_direction][1] > 0:
            middle = (start.y + goal.y) / 2.0
            candidates.append((start, Point(start.x, middle), Point(goal.x, middle), goal))
        for horizontal_first in (True, False):
            candidates.append(_orthogonal_join(start, goal, horizontal_first=horizontal_first))
        for order, raw in enumerate(candidates):
            candidate = simplify_orthogonal(raw)
            if _collisions(candidate, obstacles) or not _port_directions_match(
                    candidate,
                    entering_direction if require_entering else "",
                    leaving_direction if require_leaving else ""):
                continue
            crossing_total = sum(
                _proper_cross(a, b, segment.start, segment.end)
                for a, b in zip(candidate, candidate[1:])
                for segment in request.existing_segments)
            direct.append((
                polyline_length(candidate)
                + bend_count(candidate) * request.bend_penalty
                + crossing_total * request.crossing_penalty
                # Score the first segment at departure, and the last at
                # arrival. Swapping those ends chose a three-elbow stair
                # step even when one clean elbow joined the exact ports.
                + _leaving_penalty(tuple(reversed(candidate)), entering_direction,
                                   request.bend_penalty)
                + _leaving_penalty(candidate, leaving_direction,
                                   request.bend_penalty),
                order, candidate))
        if direct:
            return min(direct, key=lambda row: (row[0], row[1]))[2], 0
        bounds = request.bounds or _derived_bounds(
            (start, goal, *request.vias), obstacles,
            max(request.grid * 6.0, request.clearance * 6.0, 96.0))
        gateways_a = _gateway_options(start, request.grid, bounds, obstacles)
        gateways_b = _gateway_options(goal, request.grid, bounds, obstacles)
        visited_total = 0
        # Gateways differ by at most one grid cell.  Running a complete A*
        # for every Cartesian pair made one connector drag perform sixteen
        # equivalent searches.  Try the shortest tails first and stop at the
        # first clear path; the path search itself still optimises length,
        # bends and crossings.  Eight retries cover a gateway boxed in by an
        # obstacle without turning pointer movement into batch processing.
        pairs = sorted(
            ((gateway_a, tail_a, gateway_b, tail_b)
             for gateway_a, tail_a in gateways_a
             for gateway_b, tail_b in gateways_b),
            key=lambda row: (
                polyline_length(row[1]) + polyline_length(row[3]),
                bend_count(row[1]) + bend_count(row[3]),
                _leaving_penalty(tuple(reversed(row[3])),
                                 leaving_direction,
                                 request.bend_penalty),
                row[0].x, row[0].y, row[2].x, row[2].y,
                tuple((point.x, point.y) for point in row[1]),
                tuple((point.x, point.y) for point in row[3])))
        eligible = []
        expected_end = {"n": "s", "e": "w", "s": "n", "w": "e"}.get(
            leaving_direction, "")
        for gateway_a, tail_a, gateway_b, tail_b_goal_first in pairs:
            tail_b = tuple(reversed(tail_b_goal_first))
            if require_entering and len(tail_a) > 1 \
                    and _last_direction(tail_a[:2]) != entering_direction:
                continue
            if require_leaving and len(tail_b) > 1 \
                    and _last_direction(tail_b) != expected_end:
                continue
            eligible.append((gateway_a, tail_a, gateway_b, tail_b))
        for gateway_a, tail_a, gateway_b, tail_b in eligible[:8]:
            grid_path, visited = _grid_astar(
                gateway_a, gateway_b, bounds, obstacles,
                request.existing_segments, request.grid,
                request.bend_penalty, request.crossing_penalty,
                request.max_nodes,
                entering_direction or _last_direction(tail_a),
                required_start_direction=(entering_direction
                                          if require_entering
                                          and len(tail_a) < 2 else ""),
                required_end_direction=(expected_end
                                        if require_leaving
                                        and len(tail_b) < 2 else ""))
            visited_total += visited
            if not grid_path:
                continue
            candidate = simplify_orthogonal(
                (*tail_a, *grid_path[1:], *tail_b[1:]))
            if not candidate or not _same_point(candidate[0], start) \
                    or not _same_point(candidate[-1], goal):
                continue
            if _collisions(candidate, obstacles) or not _port_directions_match(
                    candidate,
                    entering_direction if require_entering else "",
                    leaving_direction if require_leaving else ""):
                continue
            return candidate, visited_total
        return (), visited_total

    @staticmethod
    def _fallback(request: RouteRequest, controls: Sequence[Point],
                  obstacles: Sequence[Obstacle], message: str,
                  visited: int = 0) -> RouteResult:
        points = [request.start.point]
        for point in controls:
            points.extend(_orthogonal_join(points[-1], point)[1:])
        points.extend(_orthogonal_join(
            points[-1], request.end.point, horizontal_first=False)[1:])
        route = simplify_orthogonal(points)
        return RouteResult(
            route, status="blocked", collisions=_collisions(route, obstacles),
            message=message, visited_nodes=visited, used_fallback=True)


def _grid_astar(start: Point, goal: Point, bounds: Box,
                obstacles: Sequence[Obstacle],
                existing_segments: Sequence[Segment], grid: float,
                bend_penalty: float, crossing_penalty: float,
                max_nodes: int, entering_direction: str = "",
                required_start_direction: str = "",
                required_end_direction: str = "") \
        -> tuple[tuple[Point, ...], int]:
    origin_x = math.floor(bounds.left / grid) * grid
    origin_y = math.floor(bounds.top / grid) * grid
    min_ix = math.ceil((bounds.left - origin_x) / grid)
    max_ix = math.floor((bounds.right - origin_x) / grid)
    min_iy = math.ceil((bounds.top - origin_y) / grid)
    max_iy = math.floor((bounds.bottom - origin_y) / grid)

    def index(point: Point) -> tuple[int, int]:
        return (round((point.x - origin_x) / grid),
                round((point.y - origin_y) / grid))

    def point(ix: int, iy: int) -> Point:
        return Point(origin_x + ix * grid, origin_y + iy * grid)

    start_xy, goal_xy = index(start), index(goal)
    if start_xy != goal_xy and (
            not _same_point(point(*start_xy), start)
            or not _same_point(point(*goal_xy), goal)):
        return (), 0
    blocked = set()
    blocked_edges = set()
    for obstacle in obstacles:
        left = max(min_ix, math.floor(
            (obstacle.bounds.left - origin_x) / grid) - 1)
        right = min(max_ix, math.ceil(
            (obstacle.bounds.right - origin_x) / grid) + 1)
        top = max(min_iy, math.floor(
            (obstacle.bounds.top - origin_y) / grid) - 1)
        bottom = min(max_iy, math.ceil(
            (obstacle.bounds.bottom - origin_y) / grid) + 1)
        for ix in range(left, right + 1):
            for iy in range(top, bottom + 1):
                if obstacle.bounds.contains(point(ix, iy), strict=True):
                    blocked.add((ix, iy))
                # A thin object can fit between two clear grid nodes. The
                # old search crossed it on every retry, then rejected the
                # finished path and incorrectly reported no route available.
                if obstacle.bounds.right - obstacle.bounds.left <= grid \
                        and ix < max_ix and _segment_hits_box(
                            point(ix, iy), point(ix + 1, iy), obstacle.bounds):
                    blocked_edges.add((ix, iy, 0))
                if obstacle.bounds.bottom - obstacle.bounds.top <= grid \
                        and iy < max_iy and _segment_hits_box(
                            point(ix, iy), point(ix, iy + 1), obstacle.bounds):
                    blocked_edges.add((ix, iy, 1))
    blocked.discard(start_xy)
    blocked.discard(goal_xy)

    initial_direction = _DIRECTION_INDEX.get(entering_direction, -1)
    start_state = (start_xy[0], start_xy[1], initial_direction)
    queue = [(0.0, 0.0, 0, start_xy[0], start_xy[1],
              initial_direction, start_state)]
    costs = {start_state: 0.0}
    bends = {start_state: 0}
    parents = {}
    visited = 0
    final = None
    while queue and visited < max_nodes:
        _f, cost, turns, ix, iy, direction, state = heapq.heappop(queue)
        if cost != costs.get(state) or turns != bends.get(state):
            continue
        visited += 1
        if (ix, iy) == goal_xy and (
                not required_end_direction
                or direction == _DIRECTION_INDEX[required_end_direction]):
            final = state
            break
        for next_direction, (dx, dy, _name) in enumerate(_DIRECTIONS):
            if state == start_state and required_start_direction \
                    and next_direction != _DIRECTION_INDEX[
                        required_start_direction]:
                continue
            nx, ny = ix + dx, iy + dy
            if not (min_ix <= nx <= max_ix and min_iy <= ny <= max_iy):
                continue
            if (nx, ny) in blocked:
                continue
            if (min(ix, nx), min(iy, ny), int(dx == 0)) in blocked_edges:
                continue
            turn = int(direction >= 0 and direction != next_direction)
            if existing_segments:
                here, there = point(ix, iy), point(nx, ny)
                crossings = sum(
                    _proper_cross(here, there, segment.start, segment.end)
                    for segment in existing_segments)
            else:
                crossings = 0
            next_cost = (cost + grid + turn * bend_penalty
                         + crossings * crossing_penalty)
            next_turns = turns + turn
            next_state = (nx, ny, next_direction)
            old = (costs.get(next_state, math.inf),
                   bends.get(next_state, 1 << 30))
            if (next_cost, next_turns) >= old:
                continue
            costs[next_state] = next_cost
            bends[next_state] = next_turns
            parents[next_state] = state
            heuristic = (abs(goal_xy[0] - nx)
                         + abs(goal_xy[1] - ny)) * grid
            heapq.heappush(queue, (
                next_cost + heuristic, next_cost, next_turns,
                nx, ny, next_direction, next_state))
    if final is None:
        return (), visited
    indices = []
    state = final
    while True:
        indices.append((state[0], state[1]))
        if state == start_state:
            break
        state = parents[state]
    indices.reverse()
    return simplify_orthogonal(point(ix, iy) for ix, iy in indices), visited


def _gateway_options(point: Point, grid: float, bounds: Box,
                     obstacles: Sequence[Obstacle]) \
        -> tuple[tuple[Point, tuple[Point, ...]], ...]:
    floor_x = math.floor(point.x / grid) * grid
    ceil_x = math.ceil(point.x / grid) * grid
    floor_y = math.floor(point.y / grid) * grid
    ceil_y = math.ceil(point.y / grid) * grid
    # Include the adjacent grid ring. A port can sit exactly on one grid axis
    # but between lines on the other; nearest-corner gateways then approach it
    # only vertically (or only horizontally), making its authored normal
    # impossible to honor even though one extra grid cell gives a clear route.
    xs = (floor_x - grid, floor_x, ceil_x, ceil_x + grid)
    ys = (floor_y - grid, floor_y, ceil_y, ceil_y + grid)
    gateways = sorted({Point(x, y) for x in xs for y in ys})
    options = []
    for gateway in gateways:
        if not bounds.contains(gateway) or any(
                obstacle.bounds.contains(gateway, strict=True)
                for obstacle in obstacles):
            continue
        joins = (_orthogonal_join(point, gateway),
                 _orthogonal_join(point, gateway,
                                  horizontal_first=False))
        for join in joins:
            if _collisions(join, obstacles):
                continue
            options.append((gateway, join))
    unique = {(gateway, join): (gateway, join)
              for gateway, join in options}
    return tuple(unique[key] for key in sorted(
        unique, key=lambda row: (
            polyline_length(row[1]), bend_count(row[1]),
            row[0].x, row[0].y,
            tuple((one.x, one.y) for one in row[1]))))


def _derived_bounds(points: Sequence[Point], obstacles: Sequence[Obstacle],
                    padding: float) -> Box:
    xs = [point.x for point in points]
    ys = [point.y for point in points]
    for obstacle in obstacles:
        xs.extend((obstacle.bounds.left, obstacle.bounds.right))
        ys.extend((obstacle.bounds.top, obstacle.bounds.bottom))
    return Box(min(xs) - padding, min(ys) - padding,
               max(xs) + padding, max(ys) + padding)


def _orthogonal_join(start: Point, end: Point, *,
                     horizontal_first: bool = True) -> tuple[Point, ...]:
    if _same_point(start, end):
        return (start,)
    if math.isclose(start.x, end.x, abs_tol=1e-9) \
            or math.isclose(start.y, end.y, abs_tol=1e-9):
        return (start, end)
    elbow = Point(end.x, start.y) if horizontal_first \
        else Point(start.x, end.y)
    return (start, elbow, end)


def _collisions(points: Sequence[Point],
                obstacles: Sequence[Obstacle]) -> tuple[str, ...]:
    if not points:
        return ()
    route_left = min(point.x for point in points)
    route_right = max(point.x for point in points)
    route_top = min(point.y for point in points)
    route_bottom = max(point.y for point in points)
    collided = set()
    for obstacle in obstacles:
        box = obstacle.bounds
        # Most display objects are nowhere near one connector.  Reject them
        # by extent before exact point/segment tests; without this a drag over
        # 120 objects spent most of its frame testing 118 impossible hits.
        if box.right <= route_left or box.left >= route_right \
                or box.bottom <= route_top or box.top >= route_bottom:
            continue
        if any(box.contains(point, strict=True)
               for point in points) or any(
                   _segment_hits_box(start, end, box)
                   for start, end in zip(points, points[1:])):
            collided.add(obstacle.ident)
    return tuple(sorted(collided))


def _segment_collisions(start: Point, end: Point,
                        obstacles: Sequence[Obstacle]) -> bool:
    return any(_segment_hits_box(start, end, obstacle.bounds)
               for obstacle in obstacles)


def _segment_hits_box(start: Point, end: Point, box: Box) -> bool:
    """Whether an orthogonal segment enters a box's open interior."""
    if math.isclose(start.x, end.x, abs_tol=1e-9):
        if not box.left < start.x < box.right:
            return False
        low, high = sorted((start.y, end.y))
        return max(low, box.top) < min(high, box.bottom)
    if math.isclose(start.y, end.y, abs_tol=1e-9):
        if not box.top < start.y < box.bottom:
            return False
        low, high = sorted((start.x, end.x))
        return max(low, box.left) < min(high, box.right)
    # A malformed manual segment should never be accepted as clear.
    return True


def _proper_cross(a1: Point, a2: Point, b1: Point, b2: Point) -> int:
    adx, ady = a2.x - a1.x, a2.y - a1.y
    bdx, bdy = b2.x - b1.x, b2.y - b1.y
    denominator = adx * bdy - ady * bdx
    if abs(denominator) < 1e-9:
        return 0
    bx, by = b1.x - a1.x, b1.y - a1.y
    t = (bx * bdy - by * bdx) / denominator
    u = (bx * ady - by * adx) / denominator
    return int(1e-6 < t < 1 - 1e-6 and 1e-6 < u < 1 - 1e-6)


def _crossing_total(points: Sequence[Point],
                    segments: Sequence[Segment]) -> int:
    return sum(_proper_cross(start, end, segment.start, segment.end)
               for start, end in zip(points, points[1:])
               for segment in segments)


def _leaving_penalty(points: Sequence[Point], normal: str,
                     bend_penalty: float) -> float:
    if not normal or len(points) < 2:
        return 0.0
    # The route approaches an endpoint stub opposite its outward normal.
    expected = {"n": "s", "e": "w", "s": "n", "w": "e"}[normal]
    return 0.0 if _last_direction(points) == expected else bend_penalty


def _port_directions_match(points: Sequence[Point], start_normal: str = "",
                           end_normal: str = "") -> bool:
    """A routed leg must not fold a nozzle stub back through its equipment."""
    if len(points) < 2:
        return not start_normal and not end_normal
    if start_normal and _last_direction(points[:2]) != start_normal:
        return False
    if end_normal:
        expected = {"n": "s", "e": "w", "s": "n", "w": "e"}[end_normal]
        if _last_direction(points) != expected:
            return False
    return True


def _last_direction(points: Sequence[Point]) -> str:
    if len(points) < 2:
        return ""
    start, end = points[-2], points[-1]
    if abs(end.x - start.x) >= abs(end.y - start.y):
        return "e" if end.x >= start.x else "w"
    return "s" if end.y >= start.y else "n"


def _same_point(first: Point, second: Point) -> bool:
    return (math.isclose(first.x, second.x, abs_tol=1e-9)
            and math.isclose(first.y, second.y, abs_tol=1e-9))


def _collinear(first: Point, middle: Point, last: Point) -> bool:
    return ((math.isclose(first.x, middle.x, abs_tol=1e-9)
             and math.isclose(middle.x, last.x, abs_tol=1e-9))
            or (math.isclose(first.y, middle.y, abs_tol=1e-9)
                and math.isclose(middle.y, last.y, abs_tol=1e-9)))


__all__ = [
    "Box", "CARDINAL_NORMALS", "CrossoverCandidate", "DEFAULT_CLEARANCE",
    "DEFAULT_GRID", "Obstacle", "OrthogonalRouter", "Point", "PortAnchor",
    "RouteIssue", "RouteRequest", "RouteResult", "Segment", "bend_count",
    "crossover_owner", "infer_port_normal", "polyline_length",
    "move_orthogonal_segment", "repair_manual_waypoints", "simplify_orthogonal",
    "validate_manual_waypoints",
]
