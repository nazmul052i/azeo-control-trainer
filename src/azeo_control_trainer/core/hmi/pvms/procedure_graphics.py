"""Bounded workflow and history presentations for ordinary authored data elements."""
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainterPath, QPen

from ..theme.roles import Role


def table_geometry(item):
    workflow = item.data.get("presentation") == "workflow"
    header = 28 if workflow else min(24, max(16, float(item.data.get("header_height", 20))))
    height = 96 if workflow else min(36, max(14, float(item.data.get("row_height", 20))))
    count = max(1, int((item.rect().height() - header - 24) // height))
    return header, height, count


def row_at(item, pos):
    header, height, count = table_geometry(item)
    if not header <= pos.y() < header + count * height:
        return None
    index = int((pos.y() - header) // height) + getattr(item, "_table_offset", 0)
    rows = item.table_rows()
    return rows[index] if 0 <= index < len(rows) else None


def paint_workflow(item, painter, rect):
    palette = item._palette
    rows = item.table_rows()
    header, height, count = table_geometry(item)
    offset = min(getattr(item, "_table_offset", 0), max(0, len(rows) - 1))
    painter.save()
    painter.setClipRect(rect)
    painter.setFont(QFont("Segoe UI", 9))
    painter.setPen(QColor(palette[Role.TEXT_DIM]))
    painter.drawText(QRectF(12, 0, rect.width() - 24, header), Qt.AlignVCenter,
                     "Procedure workflow · right-click a step for Block Help")
    centre = rect.center().x()
    for index, row in enumerate(rows[offset:offset + count]):
        y = header + index * height
        active = row.get("state") == "ACTIVE"
        outline = QColor(palette[Role.TEXT] if active else palette[Role.LINE_SOFT])
        painter.setPen(QPen(QColor(palette[Role.LINE]), 1))
        if not row.get("graph"):
            painter.drawLine(QPointF(centre, y), QPointF(centre, y + 12))
            painter.drawLine(QPointF(centre, y + 78), QPointF(centre, y + height))
            painter.drawLine(QPointF(centre - 4, y + 7), QPointF(centre, y + 12))
            painter.drawLine(QPointF(centre + 4, y + 7), QPointF(centre, y + 12))
        card = QRectF(24, y + 12, rect.width() - 48, 66)
        painter.setPen(QPen(outline, 2 if active else 1))
        painter.setBrush(QColor(palette[Role.SURFACE_FIELD]))
        painter.drawRoundedRect(card, 4, 4)
        painter.setPen(QColor(palette[Role.TEXT]))
        painter.setFont(QFont("Segoe UI", 9, QFont.Bold))
        painter.drawText(card.adjusted(10, 3, -115, -42), Qt.AlignVCenter,
                         painter.fontMetrics().elidedText(row.get("heading", row.get("step", "")), Qt.ElideRight, int(card.width() - 125)))
        painter.drawText(card.adjusted(card.width() - 105, 3, -10, -42), Qt.AlignRight | Qt.AlignVCenter,
                         row.get("state", ""))
        painter.setFont(QFont("Segoe UI", 8))
        painter.save()
        painter.setClipRect(card.adjusted(8, 25, -8, -3))
        painter.drawText(card.adjusted(10, 25, -10, -3), Qt.AlignTop | Qt.TextWordWrap,
                         row.get("instruction", ""))
        painter.restore()
        if row.get("graph"):
            painter.setPen(QColor(palette[Role.TEXT_DIM]))
            painter.drawText(QRectF(26, y + 79, rect.width() - 52, 16), Qt.AlignVCenter,
                             painter.fontMetrics().elidedText(row.get("next_paths", ""), Qt.ElideRight, int(rect.width() - 52)))
    if not rows:
        painter.drawText(rect, Qt.AlignCenter, "No procedure steps · configure a Procedure Reference")
    painter.setPen(QColor(palette[Role.ACTION]))
    painter.setFont(QFont("Segoe UI", 9))
    painter.drawText(QRectF(0, rect.bottom() - 24, rect.width(), 24), Qt.AlignCenter,
                     "◀ Previous                    Current step                    Next ▶")
    painter.restore()


def paint_history(item, painter, rect):
    binding = item.bindings.get(item.data.get("series_path", ""))
    snapshot = binding.result.value if binding and binding.result.quality.name == "GOOD" else {}
    snapshot = snapshot if isinstance(snapshot, dict) else {}
    pens = snapshot.get("series", ())
    painter.save()
    painter.setClipRect(rect)
    palette = item._palette
    painter.setFont(QFont("Segoe UI", 8))
    painter.setPen(QColor(palette[Role.TEXT_DIM]))
    painter.drawText(rect.adjusted(8, 4, -8, -4), Qt.AlignTop, snapshot.get("message", "History unavailable"))
    if not pens:
        painter.drawText(rect, Qt.AlignCenter, "No numeric pens · map process or memory tags in PA Designer")
        painter.restore()
        return
    # Separate lanes preserve engineering units and avoid mixing unlike scales.
    graph = rect.adjusted(170, 32, -12, -22)
    start, end = snapshot["start"], snapshot["end"]
    lane_height = graph.height() / len(pens)
    for index, pen in enumerate(pens):
        lane = QRectF(graph.left(), graph.top() + index * lane_height, graph.width(), lane_height - 6)
        painter.setPen(QPen(QColor(palette[Role.LINE_SOFT]), 1))
        painter.drawRect(lane)
        painter.setPen(QColor(palette[Role.TEXT]))
        painter.drawText(QRectF(8, lane.top(), 152, lane.height()), Qt.AlignVCenter | Qt.TextWordWrap, pen["legend"])
        curve = QPainterPath()
        begun = False
        lo, hi = pen["lo"], max(pen["lo"] + .001, pen["hi"])
        for t, value in pen["points"]:
            if value is None:
                begun = False
                continue
            point = QPointF(lane.left() + (t - start) / (end - start) * lane.width(),
                            lane.bottom() - max(0, min(1, (value - lo) / (hi - lo))) * lane.height())
            curve.lineTo(point) if begun else curve.moveTo(point)
            begun = True
        painter.save()
        painter.setClipRect(lane)
        painter.setPen(QPen(QColor(palette[Role.BAR_PV]), 1.5))
        painter.drawPath(curve)
        painter.setPen(QPen(QColor(palette[Role.TEXT_DIM]), 1, Qt.DotLine))
        for threshold in pen.get("thresholds", ()):
            y = lane.bottom() - (threshold["value"] - lo) / (hi - lo) * lane.height()
            painter.drawLine(QPointF(lane.left(), y), QPointF(lane.right(), y))
            painter.drawText(QRectF(lane.left() + 4, max(lane.top(), y - 16), lane.width() - 8, 16),
                             Qt.AlignRight, threshold["label"])
        painter.restore()
    painter.setPen(QPen(QColor(palette[Role.TEXT_DIM]), 1, Qt.DashLine))
    for event in snapshot.get("events", ()):
        x = graph.left() + (event["time"] - start) / (end - start) * graph.width()
        painter.drawLine(QPointF(x, graph.top()), QPointF(x, graph.bottom()))
    painter.drawText(QRectF(graph.left(), rect.bottom() - 20, graph.width(), 20),
                     Qt.AlignCenter, snapshot.get("axis_label", "Wall-clock minutes"))
    painter.restore()
