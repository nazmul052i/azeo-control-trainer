from __future__ import annotations

import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from azeo_control_trainer.core.hmi.pvms.rendering.routing import (  # noqa: E402
    Box, CrossoverCandidate, Obstacle, OrthogonalRouter, Point, PortAnchor,
    RouteRequest, crossover_owner, repair_manual_waypoints,
    validate_manual_waypoints,
)
from azeo_control_trainer.core.hmi.pvms.publishing import display_diff  # noqa: E402


@pytest.mark.parametrize("turns", range(4))
@pytest.mark.parametrize("reverse", [False, True])
def test_clear_elbow_does_not_grow_two_extra_bends_at_a_nozzle(turns, reverse):
    def rotate(point):
        x, y = point
        for _ in range(turns):
            x, y = -y, x
        return Point(x, y)

    normals = "eswn"
    first = PortAnchor(rotate((0, 0)), normals[turns])
    last = PortAnchor(rotate((140, -13)), normals[(turns + 1) % 4])
    expected = (first.point, rotate((140, 0)), last.point)
    if reverse:
        first, last, expected = last, first, tuple(reversed(expected))
    result = OrthogonalRouter().route(RouteRequest(first, last))
    assert result.status == "ok"
    assert result.points == expected


@pytest.mark.parametrize("vertical", [False, True])
def test_offset_facing_ports_use_a_balanced_run_not_a_nozzle_dogleg(vertical):
    start, end = (Point(0, 0), Point(32, 240)) if vertical else (Point(0, 0), Point(240, 32))
    result = OrthogonalRouter().route(RouteRequest(
        PortAnchor(start, "s" if vertical else "e"),
        PortAnchor(end, "n" if vertical else "w")))
    expected = (start, Point(0, 120), Point(32, 120), end) if vertical else (
        start, Point(120, 0), Point(120, 32), end)
    assert result.status == "ok" and result.points == expected


def test_router_never_folds_an_outward_stub_back_through_equipment():
    start = PortAnchor(Point(303.36, 184.21), "n")
    end = PortAnchor(Point(503.13, 217.98), "w")
    result = OrthogonalRouter().route(RouteRequest(start, end))
    assert result.status == "ok", result.message
    assert result.points[1].y < start.point.y
    assert result.points[-2].x < end.point.x


def test_router_preserves_exact_ports_and_avoids_inflated_obstacle():
    obstacle = Obstacle("vessel", Box(100, 20, 200, 80))
    result = OrthogonalRouter().route(RouteRequest(
        PortAnchor(Point(0, 50), "e"), PortAnchor(Point(300, 50), "w"),
        obstacles=(obstacle,), bounds=Box(-20, -80, 320, 140),
    ))
    assert result.status == "ok"
    assert result.points[0] == Point(0, 50)
    assert result.points[-1] == Point(300, 50)
    assert not result.collisions
    assert all(a.x == b.x or a.y == b.y
               for a, b in zip(result.points, result.points[1:]))


def test_blocked_route_is_visible_and_honest():
    result = OrthogonalRouter().route(RouteRequest(
        PortAnchor(Point(0, 10), "e"), PortAnchor(Point(30, 10), "w"),
        obstacles=(Obstacle("wall", Box(12, -100, 18, 100)),),
        bounds=Box(0, 0, 30, 20), max_nodes=1000,
    ))
    assert result.status == "blocked"
    assert result.used_fallback
    assert result.message
    assert result.points[0] == Point(0, 10)
    assert result.points[-1] == Point(30, 10)


def test_manual_waypoint_validation_and_repair_are_non_destructive():
    values = [[11.2, 18.7], [11.2, 18.7], [999, 999], ["bad", 4]]
    issues = validate_manual_waypoints(values, bounds=Box(0, 0, 100, 100))
    assert {issue.severity for issue in issues} == {"error", "warning"}
    assert repair_manual_waypoints(values, grid=10, bounds=Box(0, 0, 100, 100)) \
        == (Point(10, 20), Point(100, 100))


def test_crossover_has_exactly_one_stable_owner():
    low = CrossoverCandidate("a", "jump", z=0)
    high = CrossoverCandidate("b", "gap", z=1)
    assert crossover_owner(low, high) == "b"
    assert crossover_owner(high, low) == "b"
    assert crossover_owner(CrossoverCandidate("a"),
                           CrossoverCandidate("b")) is None


def test_publish_diff_names_a_manual_reroute():
    before = {"items": [{"id": "p1", "kind": "pipe",
                          "a": "a", "b": "b"}]}
    after = {"items": [{"id": "p1", "kind": "pipe",
                         "a": "a", "b": "b", "route_mode": "manual",
                         "route_points": [[40, 20]]}]}
    assert display_diff(before, after) == ["~ rerouted p1"]


def test_router_checks_edges_between_grid_nodes_for_thin_obstacles():
    result = OrthogonalRouter().route(RouteRequest(
        PortAnchor(Point(10, 150), "e"), PortAnchor(Point(390, 150), "w"),
        obstacles=(Obstacle("thin-wall", Box(200, 80, 201, 220)),),
        bounds=Box(0, 0, 400, 300), clearance=0, line_width=0,
    ))
    assert result.status == "ok", result.message
    assert not result.collisions


def test_moving_an_unconnected_obstacle_rechecks_blocked_and_clear_pipes():
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QGraphicsScene
    from azeo_control_trainer.core.hmi.pvms.rendering.items import PipeItem, StaticItem
    from azeo_control_trainer.core.hmi.pvms.rendering.pipe_scene import route_scene_pipes
    from azeo_control_trainer.core.hmi.theme.tokens import THEMES

    app = QApplication.instance() or QApplication([])
    scene = QGraphicsScene()
    from types import SimpleNamespace
    scene.display = SimpleNamespace(width=400, height=300)
    colors = THEMES["silver"]
    wall = StaticItem(dict(id="wall", kind="rect", x=180, y=-50, w=40, h=400), colors)
    pipe = PipeItem(dict(id="pipe", kind="pipe", a_point=[20, 150],
                         b_point=[380, 150]), colors)
    scene.addItem(wall)
    scene.addItem(pipe)
    route_scene_pipes(scene)
    assert pipe.route_status == "blocked"
    wall.setPos(500, -50)
    route_scene_pipes(scene, only_for=wall)
    assert pipe.route_status == "ok"
    wall.setPos(180, -50)
    route_scene_pipes(scene, only_for=wall)
    assert pipe.route_status == "blocked"
    assert "wall" in pipe.toolTip()
    scene.clear()
    app.processEvents()


def test_text_obstacle_uses_visible_text_instead_of_empty_layout_box():
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QGraphicsScene
    from azeo_control_trainer.core.hmi.pvms.rendering.items import StaticItem
    from azeo_control_trainer.core.hmi.pvms.rendering.pipe_scene import scene_obstacles
    from azeo_control_trainer.core.hmi.theme.tokens import THEMES

    app = QApplication.instance() or QApplication([])
    scene = QGraphicsScene()
    text = StaticItem(dict(id="label", kind="text", x=0, y=0, w=400, h=200,
                           text="PUMP", text_valign="top"), THEMES["silver"])
    scene.addItem(text)
    box = scene_obstacles(scene)[0].bounds
    assert box.right < 150
    assert box.bottom < 60
    text.data["text"] = ""
    assert not scene_obstacles(scene)
    scene.clear()
    app.processEvents()
