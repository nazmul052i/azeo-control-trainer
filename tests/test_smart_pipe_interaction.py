"""Real gestures and renderer geometry share one persisted pipe contract."""
import copy
import os
from pathlib import Path
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest
from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QApplication

from azeo_control_trainer.core.hmi.pvms.rendering.items import PvmItem
from azeo_control_trainer.core.hmi.pvms.rendering.renderer import pvm_from_dict
from azeo_control_trainer.core.hmi.pvms.rendering.pipe_scene import port_anchor
from azeo_control_trainer.core.hmi.theme.tokens import THEMES


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


@pytest.mark.parametrize("variant,block,role", [
    ("vessel", "PID", "dynamo_inline"),
    ("valve", "AO", "dynamo_inline"),
    ("compressor_speed", "PID", "dynamo_inline"),
])
@pytest.mark.parametrize("rotation", [90, 180, 270])
def test_inline_pvm_nozzle_rotates_once(app, variant, block, role, rotation):
    data = dict(id="g", **{"class": f"{block}/{role}"}, variant=variant,
                x=120, y=160, w=220, h=260, params={})
    plain = PvmItem(pvm_from_dict(data), None, None, THEMES["silver"])
    turned = PvmItem(pvm_from_dict(dict(data, rot=rotation)), None, None, THEMES["silver"])
    expected = turned.mapToScene(plain.mapFromScene(plain.anchor("e")))
    assert (turned.anchor("e") - expected).manhattanLength() < .01
    assert port_anchor(turned, "e").normal == {90: "s", 180: "w", 270: "n"}[rotation]


@pytest.mark.parametrize("rotation", [90, 180, 270])
def test_compact_pvm_departure_rotates_with_glyph(app, rotation):
    item = PvmItem(pvm_from_dict(dict(id="pump", **{"class": "DEVCTL/dynamo_compact"},
                    variant="pump", rot=rotation, x=100, y=100, w=100, h=120)),
                   None, None, THEMES["silver"])
    assert port_anchor(item, "e").normal == {90: "s", 180: "w", 270: "n"}[rotation]
    assert {"suction", "discharge"} <= set(item.anchor_sides())
    assert item.anchor("discharge") == item.anchor("e")


def mouse(studio, kind, point, modifiers=Qt.NoModifier):
    viewport = studio.canvas.viewport()
    pos = studio.canvas.mapFromScene(QPointF(*point))
    event = QMouseEvent(kind, QPointF(pos), QPointF(viewport.mapToGlobal(pos)),
                        Qt.NoButton if kind == QEvent.MouseMove else Qt.LeftButton,
                        Qt.NoButton if kind == QEvent.MouseButtonRelease else Qt.LeftButton,
                        modifiers)
    QApplication.sendEvent(viewport, event)


@pytest.fixture
def studio(app, tmp_path):
    from azeo_control_trainer.azeo_graphics_designer.studio.assembler import PvmStudio
    widget = PvmStudio(lambda: {}, tmp_path, display_name="Routes")
    widget.resize(1100, 760)
    widget.show()
    widget.enter_edit()
    widget.display.width, widget.display.height = 1400, 900
    widget.apply_display_frame()
    widget.snap_enabled = widget.smart_guides_enabled = False
    app.processEvents()
    widget.set_zoom(100)
    widget.canvas.centerOn(350, 250)
    app.processEvents()
    yield widget
    widget.unsaved = False
    widget.close()
    widget.deleteLater()
    app.processEvents()


@pytest.mark.parametrize("pvm", [False, True])
@pytest.mark.parametrize("rotation", [0, 90])
def test_drag_connected_equipment_snaps_real_nozzles_and_keeps_one_undo(studio, pvm, rotation):
    common = dict(x=100, y=100, w=100, h=110, rot=rotation)
    valve = dict(x=360, y=230, w=86, h=90, rot=rotation)
    doc = dict(display="Routes", width=1400, height=900, level=2, pvms=[], items=[])
    if pvm:
        doc["pvms"] = [
            dict(id="pump", **{"class": "DEVCTL/dynamo_compact"}, variant="pump", params={"path": "TEST/PUMP"}, **common),
            dict(id="valve", **{"class": "AO/dynamo_inline"}, variant="valve", params={"path": "TEST/VALVE"}, **valve)]
    else:
        doc["items"] = [dict(id="pump", kind="symbol", symbol="pump", **common),
                        dict(id="valve", kind="symbol", symbol="control_valve", **valve)]
    doc["items"].append(dict(id="pipe", kind="pipe", a="pump", a_side="e", b="valve", b_side="w", auto=False))
    studio._load_document(doc)
    first, last = studio._endpoint("pump"), studio._endpoint("valve")
    pipe = studio._pipe_items()[0]
    fixed, moving = port_anchor(first, "e"), port_anchor(last, "w")
    assert fixed.normal == ("s" if rotation else "e")
    studio.selection.clear()
    studio.snap_enabled = studio.smart_guides_enabled = True
    before, count = copy.deepcopy(studio._document()), len(studio._undo_stack)
    origin = last.mapToScene(last.rect().center())
    delta = QPointF(fixed.point.x - moving.point.x + 2, 32) if rotation else QPointF(32, fixed.point.y - moving.point.y + 2)
    mouse(studio, QEvent.MouseButtonPress, (origin.x(), origin.y()))
    mouse(studio, QEvent.MouseMove, (origin.x() + delta.x(), origin.y() + delta.y()))
    endpoint = port_anchor(last, "w").point
    assert abs(endpoint.x - fixed.point.x if rotation else endpoint.y - fixed.point.y) < .001
    assert ("x" if rotation else "y", fixed.point.x if rotation else fixed.point.y) in studio.canvas._smart_guides
    mouse(studio, QEvent.MouseButtonRelease, (origin.x() + delta.x(), origin.y() + delta.y()))
    assert len(pipe._points) == 2 and pipe.route_status == "ok"
    assert pipe.data["a_side"] == "e" and pipe.data["b_side"] == "w"
    assert len(studio._undo_stack) == count + 1
    assert not studio.canvas._smart_guides
    assert studio.undo() and studio._document() == before


@pytest.mark.parametrize("vertical", [False, True])
def test_drag_straight_run_keeps_ports_and_one_undo(studio, vertical):
    first = studio.add_static("rect", 100, 100, 60, 60)
    last = studio.add_static("rect", 100 if vertical else 500,
                             500 if vertical else 100, 60, 60)
    pipe = studio.add_pipe(first, "s" if vertical else "e", last, "n" if vertical else "w")
    studio.selection.replace((pipe,))
    endpoints = (QPointF(pipe._points[0]), QPointF(pipe._points[-1]))
    before = copy.deepcopy(studio._document())
    count = len(studio._undo_stack)
    start = (130, 310) if vertical else (310, 130)
    finish = (210, 325) if vertical else (325, 210)
    mouse(studio, QEvent.MouseButtonPress, start)
    mouse(studio, QEvent.MouseMove, finish)
    mouse(studio, QEvent.MouseButtonRelease, finish)
    assert pipe.data.get("route_mode") == "manual"
    assert (pipe._points[0], pipe._points[-1]) == endpoints
    assert any((a.x() == b.x() == 210 if vertical else a.y() == b.y() == 210)
               for a, b in zip(pipe._points, pipe._points[1:]))
    assert len(studio._undo_stack) == count + 1
    assert studio.undo()
    assert studio._document() == before


def test_nozzle_snap_respects_alt_and_guides_switch(studio):
    first = studio.add_static("symbol", 100, 100, 100, 90, symbol="pump")
    last = studio.add_static("symbol", 400, 210, 70, 62, symbol="control_valve")
    studio.add_pipe(first, "e", last, "w")
    studio.snap_enabled = True
    for guides, modifiers in ((True, Qt.AltModifier), (False, Qt.NoModifier)):
        studio.smart_guides_enabled = guides
        studio.selection.clear()
        origin = last.mapToScene(last.rect().center())
        target = first.anchor("e").y()
        destination = origin + QPointF(40, target - last.anchor("w").y() + 3)
        mouse(studio, QEvent.MouseButtonPress, (origin.x(), origin.y()), modifiers)
        mouse(studio, QEvent.MouseMove, (destination.x(), destination.y()), modifiers)
        assert abs(last.anchor("w").y() - target) > .1
        mouse(studio, QEvent.MouseButtonRelease, (destination.x(), destination.y()), modifiers)


def test_nozzle_snap_moves_the_selection_together_and_caches_geometry(studio, monkeypatch):
    first = studio.add_static("symbol", 100, 100, 100, 90, symbol="pump")
    last = studio.add_static("symbol", 400, 210, 70, 62, symbol="control_valve")
    caption = studio.add_static("text", 400, 290, 100, 24, text="Valve")
    pipe = studio.add_pipe(first, "e", last, "w")
    studio.selection.replace((last, caption), primary=last)
    studio.smart_guides_enabled = True
    relative = caption.pos() - last.pos()
    origin = last.mapToScene(last.rect().center())
    target = first.anchor("e").y()
    destination = origin + QPointF(40, target - last.anchor("w").y() + 2)
    mouse(studio, QEvent.MouseButtonPress, (origin.x(), origin.y()))
    mouse(studio, QEvent.MouseMove, (destination.x(), destination.y()))
    assert abs(last.anchor("w").y() - target) < .001
    # Preview routing still resolves actual endpoints, but snap target
    # collection itself must not revisit every object's ports on each move.
    from azeo_control_trainer.azeo_graphics_designer.studio.pointer_drag import PointerDrag
    def unexpected(*_):
        pytest.fail("Nozzle snap targets rebuilt during pointer movement")
    monkeypatch.setattr(PointerDrag, "_prepare_port_targets", unexpected)
    for extra in (3, 5, 7):
        mouse(studio, QEvent.MouseMove, (destination.x(), destination.y() + extra))
        assert abs(last.anchor("w").y() - target) < .001
        assert caption.pos() - last.pos() == relative
    mouse(studio, QEvent.MouseButtonRelease, (destination.x(), destination.y() + 7))
    assert len(pipe._points) == 2


def test_manual_route_snaps_to_its_adjacent_bend_without_replacing_it(studio):
    first = studio.add_static("rect", 100, 100, 60, 60)
    last = studio.add_static("rect", 400, 210, 60, 60)
    pipe = studio.add_pipe(first, "e", last, "w")
    pipe.data.update(route_mode="manual", route_points=[[320, 180]])
    studio.reroute_pipes()
    studio.selection.clear()
    studio.smart_guides_enabled = True
    mouse(studio, QEvent.MouseButtonPress, (430, 240))
    mouse(studio, QEvent.MouseMove, (470, 182))
    assert last.anchor("w").y() == 180
    mouse(studio, QEvent.MouseButtonRelease, (470, 182))
    assert pipe.data["route_points"] == [[320, 180]]
    assert pipe.route_status == "ok"


@pytest.mark.parametrize("target_x,expected_status", [(464, "ok"), (428, "blocked")])
def test_drag_beside_compact_valve_checks_equipment_not_empty_frame(
        studio, target_x, expected_status):
    # The small valve has a 72 px host but a 23 px glyph. A bend at the
    # host's right edge used to be rejected as "inside valve" on release.
    studio._load_document(dict(display="Routes", width=1400, height=900, level=1,
        pvms=[dict(id="compressor", **{"class": "DEVCTL/dynamo_compact"},
                   variant="compressor", x=324, y=192, w=84, h=88, params={"path": ""}),
              dict(id="valve", **{"class": "DEVCTL/dynamo_compact"},
                   variant="hp_b_valve", x=392, y=472.436, w=72, h=47, params={"path": ""})],
        items=[dict(id="furnace", kind="symbol", symbol="furnace",
                    x=504, y=375.682, w=168, h=204.273),
               dict(id="pipe", kind="pipe", a="compressor",
                    a_side="outline:e:0.9581:0.1990", b="furnace",
                    b_side="outline:w:0.0510:0.5550", auto=False,
                    route_mode="manual", route_points=[[480, 204.736], [480, 489.053]])]))
    pipe = studio._pipe_items()[0]
    assert pipe.route_status == "ok", pipe.route_message
    studio.selection.replace((pipe,))
    endpoints = (QPointF(pipe._points[0]), QPointF(pipe._points[-1]))
    before = copy.deepcopy(studio._document())
    undo_count = len(studio._undo_stack)
    mouse(studio, QEvent.MouseButtonPress, (480, 300))
    mouse(studio, QEvent.MouseMove, (target_x, 300))
    mouse(studio, QEvent.MouseButtonRelease, (target_x, 300))
    assert pipe.route_status == expected_status, pipe.route_message
    assert (pipe._points[0], pipe._points[-1]) == endpoints
    assert pipe.data["route_points"][0][0] == target_x
    assert len(studio._undo_stack) == undo_count + 1
    if expected_status == "blocked":
        assert "valve" in pipe.route_collisions
    document = studio._document()
    studio._load_document(document)
    assert studio._document() == document
    assert studio._pipe_items()[0].route_status == expected_status
    assert studio.undo()
    assert studio._document() == before


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
@pytest.mark.parametrize("scale", [0.6, 1.0, 2.0])
def test_compact_equipment_obstacle_rotates_and_scales_with_glyph(app, rotation, scale):
    from PySide6.QtWidgets import QGraphicsScene
    from azeo_control_trainer.core.hmi.pvms.rendering.pipe_scene import scene_obstacles
    scene = QGraphicsScene()
    item = PvmItem(pvm_from_dict(dict(id="pump", **{"class": "DEVCTL/dynamo_compact"},
                    variant="pump", rot=rotation, x=100, y=100,
                    w=180 * scale, h=88 * scale)), None, None, THEMES["silver"])
    scene.addItem(item)
    # The shipped pump SVG is 78:75, centered in a 180 x 64 symbol lane
    # above 24 units of status text. Its host width must not be an obstacle.
    width = 64 * (78 / 75 if rotation in (0, 180) else 75 / 78)
    box = scene_obstacles(scene)[0].bounds
    assert (box.left, box.top, box.right, box.bottom) == pytest.approx(
        (100 + (90 - width / 2) * scale, 100,
         100 + (90 + width / 2) * scale, 100 + 64 * scale))
    scene.clear()


def test_data_pvm_retains_full_routing_footprint(app):
    from PySide6.QtWidgets import QGraphicsScene
    from azeo_control_trainer.core.hmi.pvms.rendering.pipe_scene import scene_obstacles
    scene = QGraphicsScene()
    item = PvmItem(pvm_from_dict(dict(id="ai", **{"class": "AI/dynamo_compact"},
                    x=100, y=200, w=96, h=42)), None, None, THEMES["silver"])
    scene.addItem(item)
    box = scene_obstacles(scene)[0].bounds
    assert (box.left, box.top, box.right, box.bottom) == (100, 200, 196, 242)
    scene.clear()


def test_crossing_bridge_stays_within_a_short_reversed_segment(app):
    from PySide6.QtGui import QTransform
    from PySide6.QtWidgets import QGraphicsScene
    from azeo_control_trainer.core.hmi.pvms.rendering.items import StaticItem
    from azeo_control_trainer.core.hmi.pvms.rendering.crossing import draw_crossed_polyline
    scene = QGraphicsScene()
    pipe = StaticItem(dict(id="a", kind="polyline", x=0, y=0, w=100, h=20,
                           points=[[100, 10], [0, 10]], crossover="jump"), THEMES["silver"])
    other = StaticItem(dict(id="b", kind="polyline", x=0, y=0, w=100, h=30,
                            points=[[98, 0], [98, 30]]), THEMES["silver"])
    scene.addItem(pipe)
    scene.addItem(other)
    class Painter:
        def __init__(self):
            self.extents = []
            self.transform, self.stack = QTransform(), []
        def save(self):
            self.stack.append(QTransform(self.transform))
        def restore(self):
            self.transform = self.stack.pop()
        def translate(self, *args):
            self.transform.translate(*args)
        def rotate(self, degrees):
            self.transform.rotate(degrees)
        def drawLine(self, a, b):
            self.extents.extend([a.x(), b.x()])
        def drawArc(self, rect, *_):
            rect = self.transform.mapRect(rect)
            self.extents.extend([rect.left(), rect.right()])
    painter = Painter()
    draw_crossed_polyline(painter, pipe, [QPointF(100, 10), QPointF(0, 10)], "jump", 2)
    assert painter.extents and min(painter.extents) >= 0 and max(painter.extents) <= 100
    scene.clear()


def test_explicit_branch_junction_persists_and_undoes(studio):
    first = studio.add_static("rect", 100, 100, 60, 60)
    last = studio.add_static("rect", 500, 100, 60, 60)
    pipe = studio.add_pipe(first, "e", last, "w")
    before = copy.deepcopy(studio._document())
    count = len(studio._undo_stack)
    junction = studio.insert_pipe_junction(pipe, QPointF(330, 130))
    assert junction is not None and junction.data["pipe_junction"]
    assert len(studio._pipe_items()) == 2
    assert len(studio._undo_stack) == count + 1
    for one in studio._pipe_items():
        assert junction.data["id"] in (one.data.get("a"), one.data.get("b"))
        assert one.route_status == "ok", one.route_message
    from azeo_control_trainer.core.hmi.pvms.visual_quality import drain
    from azeo_control_trainer.core.hmi.pvms.rendering.crossing import iter_connection_issues
    assert not drain(iter_connection_issues(studio.canvas.scene()))
    document = studio._document()
    studio._load_document(document)
    assert studio._document() == document
    assert studio.undo()
    assert studio._document() == before


def test_drag_escape_restores_geometry_and_does_not_leave_undo(studio):
    from PySide6.QtTest import QTest
    pipe = studio.add_pipe(None, "e", None, "w", point_a=QPointF(100, 180), point_b=QPointF(500, 180))
    studio.selection.replace((pipe,))
    before, count = copy.deepcopy(studio._document()), len(studio._undo_stack)
    mouse(studio, QEvent.MouseButtonPress, (300, 180))
    mouse(studio, QEvent.MouseMove, (300, 260))
    QTest.keyClick(studio.canvas, Qt.Key_Escape)
    assert studio._document() == before
    assert len(studio._undo_stack) == count


def test_collinear_overlaps_are_advisories_and_crossings_are_not(studio):
    from azeo_control_trainer.core.hmi.pvms.visual_quality import drain
    from azeo_control_trainer.core.hmi.pvms.rendering.crossing import iter_connection_issues
    studio.add_pipe(None, "e", None, "w", point_a=QPointF(100, 180), point_b=QPointF(500, 180))
    studio.add_pipe(None, "e", None, "w", point_a=QPointF(200, 180), point_b=QPointF(400, 180))
    issues = drain(iter_connection_issues(studio.canvas.scene()))
    assert len(issues) == 1 and "Overlapping" in issues[0][1]


def test_crossing_owner_uses_actual_equal_z_stacking(app):
    from PySide6.QtWidgets import QGraphicsScene
    from azeo_control_trainer.core.hmi.pvms.rendering.items import StaticItem
    from azeo_control_trainer.core.hmi.pvms.rendering.crossing import _collect_segments, _owns_crossing
    scene = QGraphicsScene()
    lower, upper = [StaticItem(dict(id=ident, kind="line", crossover="jump"), THEMES["dark"])
                    for ident in ("z_lower", "a_upper")]
    scene.addItem(lower)
    scene.addItem(upper)
    _collect_segments(scene)
    assert _owns_crossing(upper, lower)
    assert not _owns_crossing(lower, upper)
    scene.clear()


def test_pipe_click_preserves_heterogeneous_group_selection(studio):
    first = studio.add_static("rect", 100, 100, 60, 60)
    last = studio.add_static("rect", 500, 100, 60, 60)
    pipe = studio.add_pipe(first, "e", last, "w")
    studio.selection.replace((first, last, pipe))
    studio.group_selected()
    studio.selection.clear()
    mouse(studio, QEvent.MouseButtonPress, (310, 130))
    mouse(studio, QEvent.MouseButtonRelease, (310, 130))
    assert set(studio.selection.snapshot().items) == {first, last, pipe}


def test_moving_a_middle_segment_keeps_neighbours_orthogonal():
    from azeo_control_trainer.core.hmi.pvms.rendering.routing import Point, move_orthogonal_segment
    original = (Point(0, 0), Point(40, 0), Point(40, 80), Point(160, 80))
    points = move_orthogonal_segment(original, 1, 100)
    assert points == (Point(0, 0), Point(100, 0), Point(100, 80), Point(160, 80))


def test_segment_drag_does_not_run_global_router_on_move(studio, monkeypatch):
    pipe = studio.add_pipe(None, "e", None, "w", point_a=QPointF(100, 180), point_b=QPointF(500, 180))
    studio.selection.replace((pipe,))
    calls = []
    from azeo_control_trainer.core.hmi.pvms.rendering.routing import OrthogonalRouter
    route = OrthogonalRouter.route
    def counted(self, request):
        calls.append(request)
        return route(self, request)
    monkeypatch.setattr(OrthogonalRouter, "route", counted)
    mouse(studio, QEvent.MouseButtonPress, (300, 180))
    for y in (200, 220, 240):
        mouse(studio, QEvent.MouseMove, (300, y))
    assert not calls
    mouse(studio, QEvent.MouseButtonRelease, (300, 240))
    assert len(calls) == 1


@pytest.mark.parametrize("destination,expected", [((330, 460), "s"), ((680, 130), "e")])
def test_branch_uses_direction_toward_destination_instead_of_coincident_port_order(studio, destination, expected):
    pipe = studio.add_pipe(None, "e", None, "w", point_a=QPointF(100, 130), point_b=QPointF(550, 130))
    junction = studio.insert_pipe_junction(pipe, QPointF(330, 130))
    target = studio.add_static("rect", *destination, 60, 60)
    # All four ports occupy the dot's center; the first magnetic hit is not
    # an engineered nozzle selection and must not force every branch north.
    branch = studio.add_pipe(junction, "n", target, "n" if expected == "s" else "w")
    assert branch.data["a_side"] == expected
    assert branch.route_status == "ok", branch.route_message
    assert port_anchor(junction, branch.data["a_side"]).normal == expected


def test_merged_vertical_bridges_meet_the_straight_run(app):
    from PySide6.QtCore import QRectF
    from PySide6.QtGui import QTransform
    from PySide6.QtWidgets import QGraphicsScene
    from azeo_control_trainer.core.hmi.pvms.rendering.items import StaticItem
    from azeo_control_trainer.core.hmi.pvms.rendering.crossing import draw_crossed_polyline
    import math
    scene = QGraphicsScene()
    pipe = StaticItem(dict(id="a", kind="polyline", x=0, y=0, w=100, h=100,
                           points=[[50, 0], [50, 100]], crossover="jump"), THEMES["silver"])
    scene.addItem(pipe)
    for y in (48, 54):
        scene.addItem(StaticItem(dict(id=str(y), kind="line", x=0, y=y, w=100, h=0), THEMES["silver"]))
    class Painter:
        def __init__(self):
            self.transform, self.stack, self.lines, self.ends = QTransform(), [], [], []
        def save(self):
            self.stack.append(QTransform(self.transform))
        def restore(self):
            self.transform = self.stack.pop()
        def translate(self, *args):
            self.transform.translate(*args)
        def rotate(self, degrees):
            self.transform.rotate(degrees)
        def drawLine(self, a, b):
            self.lines.extend((a, b))
        def drawArc(self, rect, start, span):
            assert isinstance(rect, QRectF)
            for angle in (start / 16, (start + span) / 16):
                radians = math.radians(angle)
                self.ends.append(self.transform.map(rect.center() + QPointF(
                    rect.width() / 2 * math.cos(radians), -rect.height() / 2 * math.sin(radians))))
    painter = Painter()
    draw_crossed_polyline(painter, pipe, [QPointF(50, 0), QPointF(50, 100)], "jump", 2)
    assert len(painter.ends) == 2
    assert all(min((end - line).manhattanLength() for line in painter.lines) < .01 for end in painter.ends)
    scene.clear()
