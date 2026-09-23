"""P&ID scenes and the view that shows them.

One :class:`PidScene` per unit plus an overview. Each scene is built once from
static geometry and then refreshed at the display rate from a tag snapshot, so
panning and zooming never rebuild anything.

Layout is hand-placed rather than auto-routed. Auto-routing produces drawings
that are technically correct and unreadable; a P&ID is read by shape and
position, and both need to be stable between sessions.
"""

from __future__ import annotations

import logging
from typing import Callable, Dict, List, Optional

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QBrush, QPainter, QWheelEvent
from PySide6.QtWidgets import QGraphicsScene, QGraphicsView

from . import theme
from .pid_items import (FiredHeaterItem, InstrumentBubble, LabelItem,
                        LegendItem, PidItem, PipeItem, PumpItem, StatusLamp,
                        ValueBox, ValveItem, VesselItem)

log = logging.getLogger(__name__)


class PidScene(QGraphicsScene):
    """A single drawing. ``refresh`` pushes a snapshot into every live item."""

    def __init__(self, code: str, title: str) -> None:
        super().__init__()
        self.code = code
        self.title = title
        self.setBackgroundBrush(QBrush(theme.BACKGROUND))
        self.live: List[PidItem] = []
        self.on_tag_clicked: Optional[Callable[[str], None]] = None

    def add_live(self, item: PidItem) -> PidItem:
        item.clicked = self._clicked
        self.addItem(item)
        self.live.append(item)
        return item

    def add_static(self, item) -> None:
        self.addItem(item)

    def _clicked(self, tag: str) -> None:
        if self.on_tag_clicked:
            self.on_tag_clicked(tag)

    def refresh(self, snap: Dict) -> None:
        for item in self.live:
            item.refresh(snap)

    def frame(self, w: float, h: float) -> None:
        self.setSceneRect(QRectF(0, 0, w, h))
        # Centred bold title, drafting style, with the project line beneath it.
        self.add_static(LabelItem(0, 30, self.title, 20, True, theme.NORMAL_TEXT,
                                  align=Qt.AlignHCenter, width=w))
        self.add_static(LabelItem(0, 64, "AzeoPlant open loop simulator  ·  "
                                  "all control resides in the DCS", 9, False,
                                  theme.MUTED_TEXT, align=Qt.AlignHCenter,
                                  width=w))


# --------------------------------------------------------------------- U100
def build_u100() -> PidScene:
    s = PidScene("U100", "U100  Feed surge drum D1 and charge pumps")
    s.frame(1200, 700)

    s.add_static(PipeItem([(60, 150), (156, 150)]))
    s.add_static(LabelItem(58, 134, "Fresh feed", 8))
    s.add_static(PipeItem([(60, 258), (112, 258), (112, 282), (156, 282)]))
    s.add_static(LabelItem(58, 232, "T1 bottoms recycle", 8))

    s.add_live(VesselItem(215, 300, 118, 250, "D1", "LT-1001",
                          sub="Feed surge drum"))
    s.add_live(InstrumentBubble(316, 250, "LT-1001", leader_to=(-44, 0)))
    s.add_live(InstrumentBubble(316, 196, "PT-1001", leader_to=(-46, -12)))
    s.add_live(ValueBox(410, 250, "LT-1001", "%", 1, 108, "D1 level"))
    s.add_live(ValueBox(410, 196, "PT-1001", "barg", 2, 108, "D1 pressure"))

    # suction header splitting to the duty and standby trains
    s.add_static(PipeItem([(215, 428), (215, 510), (300, 510)], arrow_at=None))
    s.add_static(PipeItem([(300, 510), (300, 455), (364, 455)], arrow_at=None))
    s.add_static(PipeItem([(300, 510), (300, 596), (364, 596)], arrow_at=None))

    s.add_live(ValveItem(385, 455, "MOV-1001A", "mov", zso_tag="ZSO-MOV1001A",
                         zsc_tag="ZSC-MOV1001A", fault_tag="XS-MOV1001A-TRQ", size=18))
    s.add_live(ValveItem(385, 596, "MOV-1001B", "mov", zso_tag="ZSO-MOV1001B",
                         zsc_tag="ZSC-MOV1001B", fault_tag="XS-MOV1001B-TRQ", size=18))

    s.add_static(PipeItem([(406, 455), (474, 455)], arrow_at=None))
    s.add_static(PipeItem([(406, 596), (474, 596)], arrow_at=None))
    s.add_live(PumpItem(492, 455, "P-101A", "XS-P101A-RUN", "XS-P101A-FLT", "ST-1001"))
    s.add_live(PumpItem(492, 596, "P-101B", "XS-P101B-RUN", "XS-P101B-FLT"))

    s.add_static(PipeItem([(516, 438), (566, 438), (566, 380)], arrow_at=None))
    s.add_static(PipeItem([(516, 579), (566, 579), (566, 438)], arrow_at=None))
    s.add_static(PipeItem([(566, 380), (566, 300), (722, 300)], arrow_at=0.8))

    s.add_live(ValveItem(762, 300, "FCV-1001", "control", position_tag="ZT-1001",
                         size=20))
    s.add_static(PipeItem([(782, 300), (932, 300)], arrow_at=None))
    s.add_live(ValveItem(956, 300, "XV-1001", "sdv", zso_tag="ZSO-XV1001",
                         zsc_tag="ZSC-XV1001", size=17))
    s.add_static(PipeItem([(973, 300), (1150, 300)], arrow_at=0.75))
    s.add_static(LabelItem(1000, 272, "to H1 charge heater", 8))

    # minimum flow protection returning to the drum
    s.add_static(PipeItem([(660, 300), (660, 120), (300, 120), (300, 196),
                           (274, 196)], arrow_at=0.42))
    s.add_live(ValveItem(470, 120, "FCV-1002", "control", size=17))
    s.add_static(LabelItem(506, 96, "minimum flow recycle", 8))

    # field manual drain on the drum bottom nozzle
    s.add_static(PipeItem([(160, 420), (160, 470)], arrow_at=None))
    s.add_live(ValveItem(160, 494, "HV-1001", "manual", zso_tag="ZSO-HV1001",
                         zsc_tag="ZSC-HV1001", size=15))
    s.add_static(LabelItem(70, 556, "field manual valve, no DCS command", 8,
                           width=240))

    s.add_live(InstrumentBubble(700, 232, "FT-1001", leader_to=(0, 56)))
    s.add_live(InstrumentBubble(612, 232, "PT-1002", leader_to=(-38, 56)))
    s.add_live(InstrumentBubble(452, 386, "PT-1003", leader_to=(24, 62)))

    s.add_static(LabelItem(1000, 372, "Key readings", 9, True, theme.NORMAL_TEXT))
    for i, (tag, eu, dp, cap) in enumerate([
            ("FT-1001", "m3/h", 1, "Charge flow"),
            ("PT-1002", "barg", 2, "Discharge pressure"),
            ("PT-1003", "barg", 2, "P-101A suction"),
            ("FT-1003", "m3/h", 1, "Minimum flow"),
            ("IT-1001", "A", 0, "P-101A current"),
            ("VT-1001", "um", 1, "P-101A vibration")]):
        s.add_live(ValueBox(1076, 400 + i * 38, tag, eu, dp, 150, cap))

    s.add_static(LabelItem(1000, 152, "Equipment status", 9, True, theme.NORMAL_TEXT))
    for i, (tag, cap, on, off, alarm) in enumerate([
            ("XS-P101A-RUN", "P-101A", "RUNNING", "STOPPED", False),
            ("XS-P101B-RUN", "P-101B", "RUNNING", "STOPPED", False),
            ("LSLL-1001", "D1 level LL", "TRIPPED", "NORMAL", True),
            ("LSHH-1001", "D1 level HH", "TRIPPED", "NORMAL", True)]):
        s.add_live(StatusLamp(1076, 178 + i * 22, tag, cap, on, off, alarm))
    return s


# --------------------------------------------------------------------- U300
def build_u300() -> PidScene:
    s = PidScene("U300", "U300  Fired heater H1")
    s.frame(1200, 700)

    s.add_static(PipeItem([(60, 300), (150, 300)]))
    s.add_static(LabelItem(30, 274, "charge from D1 via E5", 8, width=130))
    # E5 feed/product exchanger: bypass valve on the charge side, the
    # hot side is the D3 liquid on its way to T1
    s.add_live(ValveItem(96, 300, "TCV-3002", "control", size=14,
                         label_pos="left"))
    s.add_static(LabelItem(30, 330, "E5 bypass", 7))
    s.add_live(InstrumentBubble(150, 254, "TT-3007", leader_to=(0, 40)))

    s.add_static(PipeItem([(150, 300), (150, 232), (228, 232)], arrow_at=None))
    s.add_static(PipeItem([(150, 300), (150, 368), (228, 368)], arrow_at=None))
    s.add_live(ValveItem(248, 232, "FCV-3005", "control", size=16))
    s.add_live(ValveItem(248, 368, "FCV-3006", "control", size=16))
    s.add_static(LabelItem(216, 194, "pass 1", 8))
    s.add_static(LabelItem(216, 404, "pass 2", 8))
    s.add_static(PipeItem([(268, 232), (350, 232), (350, 300)], arrow_at=None))
    s.add_static(PipeItem([(268, 368), (350, 368), (350, 300)], arrow_at=None))

    s.add_live(FiredHeaterItem(500, 300, 210, 210, "H1", flame_tag="BS-3001"))
    s.add_static(PipeItem([(350, 300), (395, 300)], arrow_at=None))
    s.add_static(PipeItem([(605, 300), (790, 300)], arrow_at=0.7))
    s.add_static(LabelItem(660, 274, "to reactor R1", 8))

    # fuel gas train, offset from the centre so nothing sits under the firebox text
    s.add_static(PipeItem([(550, 660), (550, 556)], arrow_at=None))
    s.add_static(LabelItem(486, 674, "fuel gas from header", 8))
    s.add_live(ValveItem(550, 532, "XV-3001", "sdv", zso_tag="ZSO-XV3001",
                         zsc_tag="ZSC-XV3001", size=16, label_pos="right"))
    s.add_static(PipeItem([(550, 516), (550, 486)], arrow_at=None))
    s.add_live(ValveItem(550, 462, "FCV-3001", "control", position_tag="ZT-3001",
                         size=18, label_pos="right"))
    s.add_static(PipeItem([(550, 442), (550, 405)], arrow_at=None))

    # fuel oil, split range, entering the opposite burner
    s.add_static(PipeItem([(230, 660), (330, 660), (330, 470), (430, 470),
                           (430, 405)], arrow_at=0.72))
    s.add_static(LabelItem(196, 674, "fuel oil", 8))
    s.add_live(ValveItem(330, 606, "HV-3001", "manual", zso_tag="ZSO-HV3001",
                         zsc_tag="ZSC-HV3001", size=15, label_pos="left"))
    s.add_live(ValveItem(330, 534, "FCV-3002", "control", position_tag="ZT-3002",
                         size=16, label_pos="left"))
    s.add_static(LabelItem(160, 492, "split range 0-50 %", 8, width=150))

    # combustion air into the firebox side
    s.add_static(PipeItem([(790, 560), (686, 560), (686, 380), (605, 380)],
                          arrow_at=0.78))
    s.add_static(LabelItem(700, 592, "combustion air", 8))
    s.add_live(ValveItem(686, 480, "FCV-3004", "control", size=17,
                         label_pos="right"))

    stack_x, stack_y = 571, 152
    s.add_live(InstrumentBubble(640, 106, "TT-3004",
                                leader_to=(stack_x - 640, stack_y - 106)))
    s.add_live(InstrumentBubble(722, 152, "AT-3001",
                                leader_to=(stack_x - 722, 0)))
    s.add_live(InstrumentBubble(700, 208, "TT-3001", leader_to=(-40, 92)))

    s.add_static(LabelItem(884, 168, "Key readings", 9, True, theme.NORMAL_TEXT))
    for i, (tag, eu, dp, cap) in enumerate([
            ("TT-3001", "degC", 1, "Heater outlet"),
            ("TT-3002", "degC", 1, "Pass 1 outlet"),
            ("TT-3003", "degC", 1, "Pass 2 outlet"),
            ("TT-3005", "degC", 0, "Pass 1 tube skin"),
            ("TT-3004", "degC", 0, "Stack temperature"),
            ("FT-3001", "Nm3/h", 0, "Fuel gas flow"),
            ("FT-3003", "kNm3/h", 2, "Combustion air"),
            ("PT-3001", "barg", 2, "Burner pressure"),
            ("AT-3001", "mol%", 2, "Flue gas oxygen"),
            ("AT-3002", "ppm", 0, "Flue gas CO")]):
        s.add_live(ValueBox(960, 196 + i * 38, tag, eu, dp, 150, cap))

    s.add_static(LabelItem(884, 596, "Burner status", 9, True, theme.NORMAL_TEXT))
    for i, (tag, cap, on, off, alarm) in enumerate([
            ("BS-3001", "Main flame", "PROVED", "NO FLAME", False),
            ("BS-3002", "Pilot flame", "PROVED", "NO FLAME", False),
            ("PSLL-3001", "Fuel gas LL", "TRIPPED", "NORMAL", True),
            ("XS-ID301-RUN", "ID fan", "RUNNING", "STOPPED", False)]):
        s.add_live(StatusLamp(960, 622 + i * 22, tag, cap, on, off, alarm))
    return s


# --------------------------------------------------------------------- U010
def build_u010() -> PidScene:
    s = PidScene("U010", "U010  Fuel gas header and utilities")
    s.frame(1200, 640)

    s.add_static(PipeItem([(60, 200), (170, 200)]))
    s.add_static(LabelItem(58, 174, "natural gas import", 8))
    s.add_live(ValveItem(190, 200, "PCV-0101", "control", size=19))
    s.add_static(PipeItem([(211, 200), (360, 200), (360, 300)], arrow_at=None))

    s.add_static(PipeItem([(360, 300), (900, 300)], width=3.4, arrow_at=None))
    s.add_static(LabelItem(742, 272, "FUEL GAS HEADER", 10, True, theme.NORMAL_TEXT))

    s.add_static(PipeItem([(170, 440), (300, 440), (300, 300)], arrow_at=0.66))
    s.add_static(LabelItem(58, 414, "D3 off-gas", 8))
    s.add_live(ValveItem(230, 440, "FCV-0101", "control", size=17))

    s.add_static(PipeItem([(520, 300), (520, 148)], arrow_at=0.62))
    s.add_live(ValveItem(520, 226, "PCV-0102", "control", size=17,
                         label_pos="right"))
    s.add_static(LabelItem(538, 132, "to flare", 8))

    s.add_static(PipeItem([(700, 300), (700, 470)], arrow_at=0.66))
    s.add_live(ValveItem(700, 386, "PCV-0103", "control", size=17,
                         label_pos="right"))
    s.add_static(LabelItem(636, 492, "to H1 burner header", 8, width=180))

    s.add_static(PipeItem([(870, 300), (870, 470)], arrow_at=0.66))
    s.add_live(ValveItem(870, 386, "PCV-0104", "control", size=17,
                         label_pos="right"))
    s.add_static(LabelItem(826, 492, "to B1 boiler", 8, width=180))

    s.add_live(ValveItem(410, 300, "MOV-0101", "mov", size=17))
    s.add_live(InstrumentBubble(600, 242, "PT-0101", leader_to=(0, 50)))

    s.add_static(LabelItem(980, 100, "Key readings", 9, True, theme.NORMAL_TEXT))
    for i, (tag, eu, dp, cap) in enumerate([
            ("PT-0101", "barg", 2, "Header pressure"),
            ("FT-0101", "Nm3/h", 0, "Import flow"),
            ("FT-0102", "Nm3/h", 0, "Off-gas to header"),
            ("AT-0101", "MJ/Nm3", 2, "Heating value"),
            ("PT-0102", "barg", 2, "Supply to H1"),
            ("PT-0103", "barg", 2, "Supply to B1"),
            ("PT-0104", "barg", 2, "Instrument air")]):
        s.add_live(ValueBox(1056, 128 + i * 38, tag, eu, dp, 150, cap))

    s.add_static(LabelItem(980, 414, "Trip status", 9, True, theme.NORMAL_TEXT))
    for i, (tag, cap, on, off, alarm) in enumerate([
            ("PSLL-0101", "Header LL", "TRIPPED", "NORMAL", True),
            ("PSHH-0101", "Header HH", "TRIPPED", "NORMAL", True),
            ("PSL-0102", "Air pressure", "TRIPPED", "NORMAL", True)]):
        s.add_live(StatusLamp(1056, 440 + i * 22, tag, cap, on, off, alarm))

    s.add_static(LabelItem(150, 566,
                           "H1 and B1 share this header. Firing one harder lowers "
                           "the pressure available to the other, which is the "
                           "coupling most plant-wide exercises are built on.", 8,
                           False, theme.MUTED_TEXT, width=1000))
    return s


# ----------------------------------------------------------------- overview
def build_overview() -> PidScene:
    s = PidScene("OVERVIEW", "Plant overview")
    s.frame(1320, 700)

    s.add_static(PipeItem([(120, 120), (1220, 120)], width=3.2, arrow_at=None))
    s.add_static(LabelItem(470, 92, "NATURAL GAS HEADER  U010", 10, True,
                           theme.NORMAL_TEXT))

    s.add_live(VesselItem(160, 360, 88, 190, "D1", "LT-1001", sub="surge drum"))
    s.add_static(PipeItem([(160, 455), (160, 486)], arrow_at=None))
    s.add_live(PumpItem(160, 506, "P-101A", "XS-P101A-RUN", "XS-P101A-FLT"))
    s.add_static(PipeItem([(184, 489), (250, 489), (250, 360), (355, 360)],
                          arrow_at=0.75))

    s.add_live(FiredHeaterItem(430, 360, 150, 150, "H1", flame_tag="BS-3001"))
    s.add_static(PipeItem([(430, 120), (430, 285)], arrow_at=0.6, dashed=True))
    s.add_static(PipeItem([(505, 360), (622, 360)], arrow_at=0.6))

    s.add_live(VesselItem(660, 360, 76, 170, "R1", sub="reactor"))
    s.add_live(PumpItem(300, 200, "C1", "XS-C1-RUN", "XS-C1-FLT", radius=16))
    s.add_static(LabelItem(258, 152, "C1 recycle", 7))
    s.add_static(PipeItem([(698, 360), (779, 360)], arrow_at=0.6))
    s.add_live(VesselItem(810, 360, 62, 120, "D3", sub="separator"))
    s.add_static(PipeItem([(810, 300), (810, 120)], arrow_at=0.5, dashed=True))
    s.add_static(LabelItem(820, 206, "off-gas", 7))

    s.add_static(PipeItem([(841, 392), (917, 392)], arrow_at=0.6))
    s.add_live(VesselItem(950, 400, 66, 190, "T1", "LT-5001", sub="column"))
    # T1 distillate feeds T2 off the top; T1 bottoms recycle to D1.
    s.add_static(PipeItem([(983, 330), (1040, 330), (1040, 352), (1069, 352)],
                          arrow_at=0.6))
    s.add_live(VesselItem(1100, 430, 62, 180, "T2", "LT-6001", sub="column"))

    s.add_static(PipeItem([(950, 498), (950, 600), (160, 600), (160, 455)],
                          arrow_at=0.55))
    s.add_static(LabelItem(520, 578, "T1 bottoms recycle to D1", 8))

    s.add_live(VesselItem(1000, 182, 116, 62, "B1", sub="boiler", horizontal=True))
    s.add_static(PipeItem([(1000, 120), (1000, 151)], arrow_at=None, dashed=True))

    s.add_static(LabelItem(120, 636, "Key plant readings", 9, True,
                           theme.NORMAL_TEXT))
    for i, (tag, eu, dp, cap) in enumerate([
            ("PT-0101", "barg", 2, "Fuel header"),
            ("LT-1001", "%", 1, "D1 level"),
            ("FT-1001", "m3/h", 1, "Charge flow"),
            ("TT-3001", "degC", 1, "H1 outlet"),
            ("XI-4001", "%", 1, "R1 conversion"),
            ("PT-7002", "barg", 2, "MP steam")]):
        s.add_live(ValueBox(196 + i * 162, 672, tag, eu, dp, 152, cap))

    s.add_static(LabelItem(660, 636,
                           "All ten units are modelled. Open a unit tab for its "
                           "full drawing.", 8, False, theme.MUTED_TEXT, width=620))
    s.add_static(LegendItem(70, 132))
    return s


# --------------------------------------------------------------------- U200
def build_u200() -> PidScene:
    s = PidScene("U200", "U200  Recycle gas compressor C1")
    s.frame(1200, 700)

    s.add_static(PipeItem([(60, 400), (150, 400)]))
    s.add_static(LabelItem(58, 374, "off-gas from D3", 8))
    s.add_live(VesselItem(220, 400, 80, 150, "V-201", "LT-2001", sub="suction KO"))
    s.add_static(PipeItem([(150, 400), (180, 400)], arrow_at=None))
    s.add_static(PipeItem([(220, 475), (220, 540), (300, 540)], arrow_at=None))
    s.add_live(ValveItem(330, 540, "LCV-2001", "control", size=15))
    s.add_static(LabelItem(360, 556, "condensate", 7))

    s.add_static(PipeItem([(260, 360), (330, 360)], arrow_at=None))
    s.add_live(ValveItem(360, 360, "XV-2001", "sdv", zso_tag="ZSO-XV2001",
                         zsc_tag="ZSC-XV2001", size=16))
    s.add_static(PipeItem([(378, 360), (430, 360)], arrow_at=None))
    s.add_live(ValveItem(460, 360, "FCV-2002", "control", size=17))
    s.add_static(PipeItem([(480, 360), (540, 360)], arrow_at=None))

    s.add_live(PumpItem(580, 360, "C1", "XS-C1-RUN", "XS-C1-FLT", "ST-2001",
                        radius=28, speed_eu="rpm"))
    s.add_static(PipeItem([(612, 332), (700, 332), (700, 360), (790, 360)],
                          arrow_at=0.7))
    s.add_static(LabelItem(800, 342, "to H1 feed mix", 8, width=180))
    s.add_static(PipeItem([(700, 360), (700, 430), (790, 430)], arrow_at=0.7))
    s.add_static(LabelItem(800, 444, "to reactor quench", 8, width=180))

    # H2 makeup from the battery-limit header into the suction drum
    s.add_static(PipeItem([(60, 300), (130, 300), (130, 340), (196, 340)],
                          arrow_at=0.8))
    s.add_static(LabelItem(58, 274, "H2 makeup header", 8))
    s.add_live(ValveItem(100, 300, "PCV-2003", "control", size=15))

    # anti-surge recycle
    s.add_static(PipeItem([(700, 332), (700, 200), (300, 200), (300, 340)],
                          arrow_at=0.45))
    s.add_live(ValveItem(500, 200, "FCV-2001", "control", position_tag="ZT-2001",
                         size=18))
    s.add_static(LabelItem(540, 172, "anti-surge recycle", 8))

    s.add_live(PumpItem(430, 560, "P-201", "XS-P201-RUN", "XS-P201-FLT", radius=14))
    s.add_live(PumpItem(510, 560, "P-202", "XS-P202-RUN", "XS-P202-FLT", radius=14))
    s.add_static(LabelItem(400, 604, "lube oil pumps", 7))

    s.add_live(InstrumentBubble(520, 300, "PT-2001", leader_to=(0, 52)))
    s.add_live(InstrumentBubble(700, 430, "PT-2002", leader_to=(0, -62)))

    s.add_static(LabelItem(880, 108, "Key readings", 9, True, theme.NORMAL_TEXT))
    for i, (tag, eu, dp, cap) in enumerate([
            ("PT-2001", "barg", 2, "Suction pressure"),
            ("PT-2002", "barg", 2, "Discharge pressure"),
            ("UY-2001", "%", 1, "Surge margin"),
            ("FT-2001", "kNm3/h", 2, "Suction flow"),
            ("FT-2002", "kNm3/h", 2, "Anti-surge recycle"),
            ("FT-2003", "kNm3/h", 2, "H2 makeup"),
            ("AT-2001", "kg/kmol", 1, "Gas MW"),
            ("GT-2001", "deg", 1, "Guide vanes"),
            ("JT-2001", "MW", 2, "Shaft power"),
            ("ST-2001", "rpm", 0, "Speed"),
            ("TT-2002", "degC", 1, "Discharge temperature"),
            ("VT-2001", "um", 1, "Vibration"),
            ("PT-2003", "barg", 2, "Lube oil pressure"),
            ("LT-2001", "%", 1, "V-201 level")]):
        s.add_live(ValueBox(960, 136 + i * 33, tag, eu, dp, 152, cap))

    s.add_static(LabelItem(880, 585, "Machine status", 9, True, theme.NORMAL_TEXT))
    for i, (tag, cap, on, off, alarm) in enumerate([
            ("XS-C1-RUN", "C1", "RUNNING", "STOPPED", False),
            ("UA-2001", "Surge", "SURGING", "NORMAL", True),
            ("VSHH-2001", "Vibration HH", "TRIPPED", "NORMAL", True),
            ("PSL-2001", "Lube oil low", "TRIPPED", "NORMAL", True)]):
        s.add_live(StatusLamp(960, 610 + i * 22, tag, cap, on, off, alarm))
    return s


# --------------------------------------------------------------------- U400
def build_u400() -> PidScene:
    s = PidScene("U400", "U400  Reactor R1 and separator D3")
    s.frame(1200, 680)

    s.add_static(PipeItem([(60, 200), (140, 200)]))
    s.add_static(LabelItem(58, 174, "charge from H1", 8))
    s.add_live(ValveItem(170, 200, "XV-4001", "sdv", zso_tag="ZSO-XV4001",
                         zsc_tag="ZSC-XV4001", size=16))
    s.add_static(PipeItem([(188, 200), (260, 200), (260, 240)], arrow_at=None))

    s.add_live(VesselItem(260, 380, 110, 280, "R1", sub="two catalyst beds"))
    s.add_static(LabelItem(232, 320, "bed 1", 8))
    s.add_static(LabelItem(232, 430, "bed 2", 8))

    # additive injection: the quality handle, ratioed to the charge
    s.add_static(PipeItem([(60, 140), (110, 140), (110, 200)], arrow_at=0.8))
    s.add_static(LabelItem(58, 114, "additive", 8))
    s.add_live(ValveItem(84, 140, "FCV-4003", "control", size=13,
                         label_pos="left"))

    # quench between the beds
    s.add_static(PipeItem([(60, 380), (150, 380), (150, 380), (204, 380)],
                          arrow_at=0.7))
    s.add_static(LabelItem(58, 354, "quench gas from C1", 8))
    s.add_live(ValveItem(170, 380, "FCV-4001", "control", size=16))

    s.add_static(PipeItem([(260, 522), (260, 570), (440, 570), (440, 470)],
                          arrow_at=0.5))
    s.add_live(ValveItem(370, 570, "FCV-4002", "control", size=15))
    s.add_static(LabelItem(330, 600, "effluent cooler", 7))

    s.add_live(VesselItem(500, 380, 130, 190, "D3", "LT-4001", horizontal=True,
                          sub="separator"))
    s.add_static(PipeItem([(440, 470), (440, 400), (436, 400)], arrow_at=None))

    s.add_static(PipeItem([(500, 286), (500, 180), (640, 180)], arrow_at=0.6))
    s.add_live(ValveItem(580, 180, "XV-4002", "sdv", zso_tag="ZSO-XV4002",
                         zsc_tag="ZSC-XV4002", size=15))
    s.add_static(LabelItem(650, 154, "off-gas to header and C1", 8))
    s.add_live(ValveItem(500, 240, "PCV-4001", "control", size=15,
                         label_pos="left"))

    s.add_static(PipeItem([(566, 400), (640, 400)], arrow_at=None))
    s.add_live(ValveItem(670, 400, "LCV-4001", "control", size=16))
    s.add_static(PipeItem([(688, 400), (790, 400)], arrow_at=0.6))
    s.add_static(LabelItem(700, 374, "liquid to T1", 8))

    s.add_static(PipeItem([(500, 470), (500, 540), (620, 540)], arrow_at=None))
    s.add_live(ValveItem(650, 540, "LCV-4002", "control", size=15))
    s.add_static(PipeItem([(668, 540), (760, 540)], arrow_at=0.6))
    s.add_static(LabelItem(700, 512, "sour water", 8))

    s.add_live(InstrumentBubble(346, 330, "TT-4002", leader_to=(-30, 0)))
    s.add_live(InstrumentBubble(346, 430, "TT-4003", leader_to=(-30, 0)))
    s.add_live(InstrumentBubble(346, 260, "AT-4001", leader_to=(-40, -20)))

    s.add_static(LabelItem(880, 120, "Key readings", 9, True, theme.NORMAL_TEXT))
    for i, (tag, eu, dp, cap) in enumerate([
            ("TT-4001", "degC", 1, "R1 inlet"),
            ("TT-4002", "degC", 1, "Bed 1 temperature"),
            ("TT-4003", "degC", 1, "Bed 2 temperature"),
            ("XI-4001", "%", 1, "Conversion"),
            ("AT-4001", "ppm", 0, "Product impurity"),
            ("FT-4001", "kNm3/h", 2, "Quench gas"),
            ("PT-4001", "barg", 1, "R1 pressure"),
            ("PDT-4001", "bar", 2, "Bed differential"),
            ("LT-4001", "%", 1, "D3 level"),
            ("LT-4002", "%", 1, "D3 interface"),
            ("FT-4002", "Nm3/h", 0, "Off-gas"),
            ("FT-4003", "m3/h", 1, "Liquid to T1")]):
        s.add_live(ValueBox(960, 148 + i * 38, tag, eu, dp, 152, cap))

    s.add_static(LabelItem(880, 618, "Trip status", 9, True, theme.NORMAL_TEXT))
    for i, (tag, cap) in enumerate([("TSHH-4001", "Bed temp HH"),
                                    ("PSHH-4001", "R1 press HH")]):
        s.add_live(StatusLamp(960, 644 + i * 22, tag, cap, "TRIPPED", "NORMAL", True))
    return s


def _column_scene(code: str, title: str, n: int, col: str,
                  dist_label: str, bot_label: str, boot: bool) -> PidScene:
    """T1 and T2 are the same drawing with different tag numbers."""
    s = PidScene(code, title)
    s.frame(1220, 720)

    s.add_static(PipeItem([(60, 400), (150, 400)]))
    s.add_static(LabelItem(58, 374, "feed", 8))
    s.add_live(ValveItem(180, 400, f"XV-{n}001", "sdv", zso_tag=f"ZSO-XV{n}001",
                         zsc_tag=f"ZSC-XV{n}001", size=15))
    s.add_static(PipeItem([(196, 400), (260, 400)], arrow_at=None))

    s.add_live(VesselItem(320, 380, 110, 400, col, sub="column"))

    # overhead to condenser and reflux drum
    s.add_static(PipeItem([(320, 178), (320, 130), (560, 130)], arrow_at=0.6))
    s.add_static(LabelItem(380, 104, "overhead vapour", 8))
    s.add_live(ValveItem(470, 130, f"PCV-{n}001", "control", size=14))
    s.add_live(VesselItem(660, 160, 150, 80, f"V-{n}01", f"LT-{n}001",
                          horizontal=True, sub="reflux drum"))
    s.add_static(PipeItem([(560, 130), (600, 130), (600, 150)], arrow_at=None))

    # The suction valve sits on the horizontal run with the pump well clear of
    # it, so neither caption lands on the other.
    s.add_static(PipeItem([(660, 200), (660, 250), (668, 250)], arrow_at=None))
    s.add_live(ValveItem(690, 250, f"MOV-{n}001A", "mov",
                         zso_tag=f"ZSO-MOV{n}001A", zsc_tag=f"ZSC-MOV{n}001A",
                         fault_tag=f"XS-MOV{n}001A-TRQ", size=13))
    s.add_static(PipeItem([(712, 250), (780, 250)], arrow_at=None))
    s.add_live(PumpItem(800, 250, f"P-{n}01A", f"XS-P{n}01A-RUN",
                        f"XS-P{n}01A-FLT", radius=15))

    # reflux back to the column and product away
    s.add_static(PipeItem([(816, 235), (860, 235), (860, 300), (240, 300),
                           (240, 250), (280, 250)], arrow_at=0.55))
    s.add_live(ValveItem(520, 300, f"FCV-{n}001", "control",
                         position_tag=f"ZT-{n}001", size=16))
    s.add_static(LabelItem(556, 274, "reflux", 8))

    s.add_static(PipeItem([(860, 300), (860, 360), (990, 360)], arrow_at=0.6))
    s.add_live(ValveItem(920, 360, f"FCV-{n}002", "control", size=15))
    s.add_static(LabelItem(866, 390, dist_label, 8, width=210))

    # reboiler and bottoms
    s.add_static(PipeItem([(320, 582), (320, 646), (470, 646)], arrow_at=None))
    s.add_live(ValveItem(510, 646, f"FCV-{n}004", "control", size=16))
    s.add_static(LabelItem(560, 690, "MP steam to reboiler", 8, width=200))

    s.add_static(PipeItem([(376, 520), (428, 520)], arrow_at=None))
    s.add_live(ValveItem(450, 520, f"MOV-{n}002A", "mov",
                         zso_tag=f"ZSO-MOV{n}002A", zsc_tag=f"ZSC-MOV{n}002A",
                         fault_tag=f"XS-MOV{n}002A-TRQ", size=13))
    s.add_static(PipeItem([(472, 520), (600, 520)], arrow_at=None))
    s.add_live(PumpItem(620, 520, f"P-{n}02A", f"XS-P{n}02A-RUN",
                        f"XS-P{n}02A-FLT", radius=15))
    s.add_static(PipeItem([(636, 505), (700, 505), (700, 470), (730, 470)],
                          arrow_at=None))
    s.add_live(ValveItem(762, 470, f"FCV-{n}003", "control", size=15))
    s.add_static(PipeItem([(794, 470), (900, 470)], arrow_at=0.6))
    s.add_static(LabelItem(836, 440, bot_label, 8, width=190))

    s.add_live(InstrumentBubble(410, 300, f"TT-{n}002", leader_to=(-36, 0)))
    s.add_live(InstrumentBubble(410, 400, f"TT-{n}003", leader_to=(-36, 0)))
    s.add_live(InstrumentBubble(410, 500, f"TT-{n}004", leader_to=(-36, 0)))
    s.add_live(InstrumentBubble(232, 400, f"PDT-{n}001", leader_to=(52, 0)))

    rows = [(f"PT-{n}001", "barg", 2, "Overhead pressure"),
            (f"PDT-{n}001", "mbar", 0, "Column differential"),
            (f"LT-{n}001", "%", 1, "Reflux drum level"),
            (f"LT-{n}002", "%", 1, "Column bottom level"),
            (f"FT-{n}002", "m3/h", 1, "Reflux flow"),
            (f"FT-{n}003", "m3/h", 1, "Distillate"),
            (f"FT-{n}004", "m3/h", 1, "Bottoms"),
            (f"FT-{n}005", "t/h", 2, "Reboiler steam"),
            (f"TT-{n}003", "degC", 1, "Middle tray"),
            (f"AT-{n}001", "mol%", 2, "Distillate heavy key"),
            (f"AT-{n}002", "mol%", 2, "Bottoms light key")]
    s.add_static(LabelItem(1010, 96, "Key readings", 9, True, theme.NORMAL_TEXT))
    for i, (tag, eu, dp, cap) in enumerate(rows):
        s.add_live(ValueBox(1090, 124 + i * 38, tag, eu, dp, 152, cap))

    s.add_static(LabelItem(1010, 552, "Status", 9, True, theme.NORMAL_TEXT))
    for i, (tag, cap) in enumerate([(f"UA-{n}001", "Flooding"),
                                    (f"LSLL-{n}001", "Drum level LL"),
                                    (f"PSHH-{n}001", "Pressure HH")]):
        s.add_live(StatusLamp(1090, 578 + i * 22, tag, cap, "ACTIVE", "NORMAL", True))
    return s


def build_u500() -> PidScene:
    return _column_scene("U500", "U500  Distillation column T1", 5, "T1",
                         "distillate to storage / T2", "bottoms recycle to D1",
                         True)


def build_u600() -> PidScene:
    return _column_scene("U600", "U600  Distillation column T2", 6, "T2",
                         "R2 product to blender", "heavy product rundown",
                         False)


# --------------------------------------------------------------------- U700
def build_u700() -> PidScene:
    s = PidScene("U700", "U700  Steam boiler B1 and MP steam header")
    s.frame(1200, 760)

    s.add_live(VesselItem(430, 200, 190, 90, "B1 drum", "LT-7001",
                          horizontal=True, sub="steam drum"))
    s.add_live(FiredHeaterItem(430, 460, 200, 170, "B1", flame_tag="BS-7001"))
    s.add_static(PipeItem([(430, 375), (430, 245)], arrow_at=None))

    s.add_static(PipeItem([(525, 190), (700, 190)], arrow_at=0.45))
    s.add_static(LabelItem(536, 162, "superheated steam", 8))
    s.add_live(ValveItem(640, 190, "TCV-7001", "control", size=15))
    s.add_static(PipeItem([(700, 190), (700, 300), (920, 300)], width=3.4,
                          arrow_at=None))
    s.add_static(LabelItem(706, 266, "MP STEAM HEADER", 10, True, theme.NORMAL_TEXT,
                           width=200))
    s.add_static(LabelItem(706, 322, "to T1 and T2 reboilers", 8, width=140))

    # Feedwater rises clear of the furnace on the left, then runs across the top
    # into the drum, so the control valve sits on a horizontal run and reads
    # the right way up.
    s.add_static(PipeItem([(60, 620), (140, 620)]))
    s.add_static(LabelItem(58, 594, "deaerator", 8))
    s.add_live(ValveItem(170, 620, "MOV-7001A", "mov", zso_tag="ZSO-MOV7001A",
                         zsc_tag="ZSC-MOV7001A", fault_tag="XS-MOV7001A-TRQ", size=14))
    s.add_static(PipeItem([(188, 620), (215, 620)], arrow_at=None))
    s.add_live(PumpItem(240, 620, "P-701A", "XS-P701A-RUN", "XS-P701A-FLT", radius=15))
    s.add_static(PipeItem([(256, 605), (300, 605), (300, 112), (332, 112)],
                          arrow_at=None))
    s.add_live(ValveItem(364, 112, "FCV-7001", "control", position_tag="ZT-7001",
                         size=15))
    s.add_static(PipeItem([(396, 112), (430, 112), (430, 155)], arrow_at=0.7))
    s.add_static(LabelItem(446, 100, "feedwater to drum", 8, width=180))

    # Fuel and air both arrive on horizontal runs along the bottom.
    s.add_static(LabelItem(58, 622, "", 8))
    s.add_static(PipeItem([(80, 700), (246, 700)], arrow_at=None))
    s.add_static(LabelItem(58, 674, "fuel gas from header", 8, width=180))
    s.add_live(ValveItem(276, 700, "XV-7001", "sdv", zso_tag="ZSO-XV7001",
                         zsc_tag="ZSC-XV7001", size=14))
    s.add_static(PipeItem([(294, 700), (338, 700)], arrow_at=None))
    s.add_live(ValveItem(370, 700, "FCV-7002", "control", size=15))
    s.add_static(PipeItem([(402, 700), (430, 700), (430, 575)], arrow_at=0.7))

    s.add_static(PipeItem([(830, 700), (676, 700)], arrow_at=0.5))
    s.add_static(LabelItem(844, 692, "combustion air", 8, width=160))
    s.add_live(ValveItem(644, 700, "FCV-7003", "control", size=14))
    s.add_static(PipeItem([(612, 700), (505, 700), (505, 575)], arrow_at=0.7))

    s.add_live(InstrumentBubble(870, 356, "PT-7002", leader_to=(0, -56)))
    s.add_live(InstrumentBubble(250, 150, "LT-7001", leader_to=(85, 48)))

    s.add_static(LabelItem(930, 96, "Key readings", 9, True, theme.NORMAL_TEXT))
    for i, (tag, eu, dp, cap) in enumerate([
            ("LT-7001", "mm", 0, "Drum level"),
            ("PT-7001", "barg", 1, "Drum pressure"),
            ("PT-7002", "barg", 2, "MP header pressure"),
            ("FT-7001", "t/h", 2, "Steam flow"),
            ("FT-7002", "t/h", 2, "Feedwater flow"),
            ("FT-7003", "Nm3/h", 0, "Fuel gas flow"),
            ("FT-7004", "kNm3/h", 2, "Combustion air"),
            ("TT-7001", "degC", 1, "Superheat"),
            ("AT-7001", "mol%", 2, "Flue gas oxygen"),
            ("AT-7002", "ppm", 0, "Flue gas CO"),
            ("CT-7001", "uS/cm", 0, "Drum conductivity"),
            ("LT-7002", "%", 1, "Deaerator level")]):
        s.add_live(ValueBox(1010, 126 + i * 40, tag, eu, dp, 152, cap))

    s.add_static(LabelItem(930, 618, "Status", 9, True, theme.NORMAL_TEXT))
    for i, (tag, cap, on, off, alarm) in enumerate([
            ("BS-7001", "Flame", "PROVED", "NO FLAME", False),
            ("LSLL-7001", "Drum level LL", "TRIPPED", "NORMAL", True),
            ("PSHH-7001", "Drum press HH", "TRIPPED", "NORMAL", True),
            ("XS-P701A-RUN", "BFW pump A", "RUNNING", "STOPPED", False)]):
        s.add_live(StatusLamp(1010, 646 + i * 25, tag, cap, on, off, alarm))
    return s


# --------------------------------------------------------------------- U800
def build_u800() -> PidScene:
    s = PidScene("U800", "U800  Effluent treatment and safety system")
    s.frame(1200, 620)

    s.add_static(PipeItem([(60, 260), (200, 260)]))
    s.add_static(LabelItem(58, 234, "sour water from D3 via E4", 8,
                           width=150))
    # E4 effluent cooler: TCV-8001 holds the treatment temperature
    s.add_live(ValveItem(100, 260, "TCV-8001", "control", size=13,
                         label_pos="left"))
    s.add_static(LabelItem(58, 290, "E4 cooling water", 7, width=120))
    s.add_live(InstrumentBubble(160, 214, "TT-8001", leader_to=(0, 40)))
    s.add_live(VesselItem(290, 320, 150, 210, "NT-801", "LT-8001",
                          sub="neutralisation"))
    s.add_static(PipeItem([(200, 260), (250, 260)], arrow_at=None))

    s.add_static(PipeItem([(120, 100), (290, 100), (290, 215)], arrow_at=0.7))
    s.add_static(LabelItem(58, 74, "caustic", 8))
    s.add_live(ValveItem(180, 100, "FCV-8001", "control", size=14))
    s.add_live(ValveItem(240, 100, "FCV-8002", "control", size=14))
    s.add_static(LabelItem(150, 128, "coarse and fine, split range", 7, width=220))

    s.add_static(PipeItem([(120, 470), (200, 470), (200, 400), (240, 400)],
                          arrow_at=0.7))
    s.add_static(LabelItem(58, 494, "acid", 8))
    s.add_live(ValveItem(160, 470, "FCV-8003", "control", size=14))

    s.add_live(PumpItem(300, 500, "M-801", "XS-M801-RUN", "XS-M801-FLT", radius=14))
    s.add_static(LabelItem(258, 542, "agitator", 7))

    s.add_static(PipeItem([(365, 380), (430, 380)], arrow_at=None))
    s.add_live(ValveItem(460, 380, "MOV-8001A", "mov", zso_tag="ZSO-MOV8001A",
                         zsc_tag="ZSC-MOV8001A", fault_tag="XS-MOV8001A-TRQ", size=13))
    s.add_static(PipeItem([(476, 380), (500, 380)], arrow_at=None))
    s.add_live(PumpItem(526, 380, "P-801A", "XS-P801A-RUN", "XS-P801A-FLT", radius=15))
    s.add_static(PipeItem([(542, 365), (600, 365), (600, 300), (680, 300)],
                          arrow_at=0.6))
    s.add_live(ValveItem(650, 300, "FCV-8004", "control", size=15))
    s.add_static(PipeItem([(668, 300), (780, 300)], arrow_at=0.6))
    s.add_static(LabelItem(690, 274, "to discharge", 8))

    s.add_live(InstrumentBubble(400, 230, "AT-8002", leader_to=(-46, 60)))

    s.add_static(LabelItem(850, 96, "Effluent", 9, True, theme.NORMAL_TEXT))
    for i, (tag, eu, dp, cap) in enumerate([
            ("AT-8001", "pH", 2, "Inlet pH"),
            ("AT-8002", "pH", 2, "Outlet pH"),
            ("QY-8001", "%", 1, "Linearised pH"),
            ("AT-8003", "mg/l", 0, "Outlet COD"),
            ("FT-8001", "m3/h", 1, "Discharge flow"),
            ("LT-8001", "%", 1, "NT-801 level")]):
        s.add_live(ValueBox(930, 124 + i * 38, tag, eu, dp, 152, cap))

    s.add_static(LabelItem(850, 372, "Safety system initiators", 9, True,
                           theme.NORMAL_TEXT))
    for i, (tag, cap) in enumerate([
            ("HS-9002", "Control room ESD"), ("HS-9001", "Field ESD"),
            ("GD-9001", "Gas, reactor area"), ("GD-9002", "Gas, compressor"),
            ("GD-9003", "Gas, fired plant"), ("FD-9001", "Flame zone 1"),
            ("ASHH-8001", "pH out of consent")]):
        s.add_live(StatusLamp(944, 398 + i * 24, tag, cap, "ACTIVE", "NORMAL", True))
    s.add_static(LabelItem(60, 566,
                           "The cause and effect logic lives in the DCS. This unit "
                           "supplies the initiators and obeys the ESD outputs.",
                           8, False, theme.MUTED_TEXT, width=560))
    return s


SCENE_BUILDERS = {
    "OVERVIEW": build_overview,
    "U010": build_u010,
    "U100": build_u100,
    "U200": build_u200,
    "U300": build_u300,
    "U400": build_u400,
    "U500": build_u500,
    "U600": build_u600,
    "U700": build_u700,
    "U800": build_u800,
}


class PidView(QGraphicsView):
    """Zoom and pan view. Ctrl+wheel zooms, middle drag pans, Home fits."""

    def __init__(self) -> None:
        super().__init__()
        self.setRenderHints(QPainter.Antialiasing | QPainter.TextAntialiasing)
        self.setDragMode(QGraphicsView.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.setBackgroundBrush(QBrush(theme.BACKGROUND))
        self._zoom = 1.0

    def wheelEvent(self, event: QWheelEvent) -> None:
        if event.modifiers() & Qt.ControlModifier:
            factor = 1.15 if event.angleDelta().y() > 0 else 1 / 1.15
            new = self._zoom * factor
            if 0.25 <= new <= 5.0:
                self._zoom = new
                self.scale(factor, factor)
            event.accept()
        else:
            super().wheelEvent(event)

    def fit(self) -> None:
        if self.scene():
            self.fitInView(self.scene().sceneRect(), Qt.KeepAspectRatio)
            self._zoom = self.transform().m11()

    def reset_zoom(self) -> None:
        self.resetTransform()
        self._zoom = 1.0
