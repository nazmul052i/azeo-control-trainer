#!/usr/bin/env python3
"""Generate the issued P&ID set, one sheet per process unit.

These are engineering drawings, not operator graphics: the simulator has no
mimic display, the DCS owns that. Each sheet carries the equipment, the piping,
the instruments the tag database actually contains, and the loop functions the
DCS is expected to configure against them.

    python tools/make_pids.py            # every sheet
    python tools/make_pids.py U100       # one sheet
    python tools/make_pids.py --png      # also rasterise for review
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Callable, Dict

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from pid_kit import (INK, MUTED, PAPER,  # noqa: E402
                     STANDARD_LEGEND, Sheet)

OUT = ROOT / "docs" / "PID"

BUILDERS: Dict[str, Callable[[], Sheet]] = {}


def sheet_for(code: str):
    def deco(fn):
        BUILDERS[code] = fn
        return fn
    return deco


# ===========================================================================
# U100  feed surge drum and charge pumps
# ===========================================================================
@sheet_for("U100")
def build_u100() -> Sheet:
    s = Sheet("AP-PID-U100", "Feed surge drum and charge pumps", "U100",
              sheet="1 of 1", rev="C")
    s.frame()

    # D1 first: everything else connects to its measured shell, not to a
    # nominal bounding box.
    d1 = s.place("pressurized_vessel", 400, 430, height=250)
    s.text(370, 283, "D1", 15, True, anchor="end")
    s.text(370, 297, "Feed surge drum", 10, False, "#5A5A5A", anchor="end")
    s.text(322, 656, "D1  ·  1.4 m ID x 5.4 m T/T  ·  140 m3", 9, False,
           "#5A5A5A", anchor="end")

    # --------------------------------------- inlets, all on the shell itself
    s.connector(58, 330, "OFF-SPEC IMPORT", "AP-PID-U000", "right")
    lcv = s.valve("valve_control_diaphragm", 268, 330, "LCV-1001", width=52,
                  label="above", fail="FC")
    s.pipe([(190, 330), lcv.left])
    s.text(198, 318, '3"-P-1003-CS', 9, False, "#111111", anchor="start")
    s.pipe([lcv.right, (d1.x0, 330)], arrow_at=0.7)

    s.connector(58, 390, "FRESH FEED FROM OSBL", "AP-PID-U000", "right")
    s.pipe([(190, 390), (d1.x0, 390)], arrow_at=0.74, number='6"-P-1001-CS')

    s.connector(58, 500, "T2 BOTTOMS RECYCLE", "AP-PID-U600", "right")
    mov2 = s.valve("valve_motor_operated", 248, 500, "MOV-1002", width=56,
                   label="above")
    s.pipe([(190, 500), mov2.left])
    s.pipe([mov2.right, (298, 500), (298, 450), (d1.x0, 450)], arrow_at=0.85,
           number='4"-P-1002-CS', number_at=0.45)
    s.bubble(140, 596, "FT-1002", "dcs", leader=(206, 504))

    # ---------------------------------------- instruments, on the same shell
    s.bubble(566, 330, "PT-1001", "dcs", leader=(d1.x1, 330))
    s.bubble(660, 380, "LSHH-1001", "dcs", r=24, leader=(d1.x1, 380))
    s.bubble(566, 430, "LT-1001", "dcs", leader=(d1.x1, 430))
    s.bubble(660, 480, "LSLL-1001", "dcs", r=24, leader=(d1.x1, 480))
    s.bubble(566, 530, "TT-1001", "dcs", leader=(d1.x1, 530))

    # ------------------------------- field drain, out to the left, to slops
    hv = s.valve("valve_gate", 272, 940, "HV-1001", width=50, label="above")
    s.pipe([(356, d1.y1), (356, 940), hv.right], kind="utility")
    s.connector(70, 940, "TO SLOPS", "AP-PID-U800", "left")
    s.pipe([hv.left, (202, 940)], kind="utility", arrow_at=0.7)

    # ------------------------------------------------------ pump suction bay
    s.pipe([(430, d1.y1), (430, 770)], number='8"-P-1004-CS', number_at=0.36)
    s.strainer(430, 780, "ST-1001", orient="v")
    s.bubble(338, 780, "PDT-1001", "dcs", r=24, leader=(412, 780))
    s.pipe([(430, 790), (430, 826), (528, 826)])

    mova = s.valve("valve_motor_operated", 620, 764, "MOV-1001A", width=56,
                   label="above")
    movb = s.valve("valve_motor_operated", 620, 900, "MOV-1001B", width=56,
                   label="below")
    s.pipe([(528, 826), (528, 764), mova.left])
    s.pipe([(528, 826), (528, 900), movb.left])

    pa = s.place("centrifugal_pump_1", 752, 764, height=60, on_axis=True)
    s.text(752, pa.y0 - 22, "P-101A", 15, True)
    s.text(752, pa.y0 - 8, "Charge pump, duty", 10, False, "#5A5A5A")
    pb = s.place("centrifugal_pump_1", 752, 900, height=60, on_axis=True)
    s.text(752, pb.y1 + 20, "P-101B", 15, True)
    s.text(752, pb.y1 + 34, "Charge pump, standby", 10, False, "#5A5A5A")

    s.pipe([mova.right, pa.left], arrow_at=0.62)
    s.pipe([movb.right, pb.left], arrow_at=0.62)
    s.bubble(686, 676, "PT-1003", "dcs", leader=(686, 764))
    s.bubble(470, 960, "PT-1004", "dcs", leader=(528, 872))

    # each pump discharges on its own nozzle, into a common riser at x=880
    s.pipe([pa.top, (pa.ax, 706), (880, 706)])
    s.pipe([pb.top, (pb.ax, 842), (880, 842)])
    s.pipe([(880, 842), (880, 300)], arrow_at=0.86,
           number='6"-P-1005-CS', number_at=0.24)
    s.bubble(1000, 760, "PT-1002", "dcs", leader=(884, 760))

    # ------------------------------------------- minimum flow recycle, to top
    fcv2 = s.valve("valve_control_diaphragm", 1128, 646, "FCV-1002", width=52,
                   label="below", fail="FO")
    s.pipe([(880, 646), fcv2.left])
    s.pipe([fcv2.right, (1258, 646), (1258, 228), (430, 228), (430, d1.y0)],
           arrow_at=0.72, number='3"-P-1006-CS', number_at=0.22)
    s.bubble(1200, 546, "FT-1003", "dcs", leader=(1200, 638))
    s.text(800, 216, "MINIMUM FLOW RECYCLE", 9.5, True)

    # ------------------------------------------------------------ charge line
    fcv1 = s.valve("valve_control_diaphragm", 1060, 300, "FCV-1001", width=56,
                   label="above", fail="FC")
    xv = s.valve("valve_gate", 1228, 300, "XV-1001", width=52, label="above")
    s.pipe([(880, 300), fcv1.left])
    s.pipe([fcv1.right, xv.left])
    s.text(1228, xv.y1 + 30, "FC  ·  SHUTDOWN VALVE", 8.4, True)
    s.pipe([xv.right, (1366, 300)], arrow_at=0.6)
    s.connector(1366, 300, "TO H1 CHARGE HEATER", "AP-PID-U300", "right")

    s.bubble(940, 150, "FT-1001", "dcs", leader=(940, 300))
    s.bubble(1060, 400, "ZT-1001", "dcs", leader=(1060, fcv1.y1))

    # ------------------------------------------------- DCS control functions
    # Charge flow on remote set point from drum level: the usual averaging
    # level scheme. Both blocks are configured in the DCS, not in the model.
    s.bubble(724, 150, "LIC-1001", "dcs", r=24)
    s.bubble(1060, 150, "FIC-1001", "dcs", r=24)
    s.signal([(590, 430), (724, 430), (724, 174)], kind="software")
    s.signal([(724, 126), (724, 96), (1060, 96), (1060, 126)], kind="software")
    s.signal([(961, 150), (1036, 150)], kind="software")
    s.signal([(1060, 174), (1060, fcv1.y0 - 6)], kind="software")
    s.text(880, 88, "REMOTE SET POINT", 8.6, False, "#5A5A5A")

    # --------------------------------------------------- schedules on sheet
    s.text(1330, 470, "MOTOR AND VALVE CONTROL", 10, True, anchor="start")
    s.seg(1330, 478, 1646, 478, "#111111", 1.0)
    for i, (tag, desc) in enumerate([
            ("MC-P101A", "start / stop, run, fault, available, VFD"),
            ("MC-P101B", "start / stop, run, fault, available"),
            ("XC-MOV1001A", "open / close, both limits, torque, remote"),
            ("XC-MOV1001B", "open / close, both limits, torque, remote"),
            ("XC-MOV1002", "recycle inlet, open / close, both limits, torque"),
            ("XC-XV1001", "charge shutdown valve, open command and limits"),
            ("SC-1001", "P-101A VFD speed reference")]):
        y = 498 + i * 17
        s.text(1330, y, tag, 8.8, True, anchor="start")
        s.text(1444, y, desc, 8.2, False, "#5A5A5A", anchor="start")

    s.text(1330, 616, "MACHINE MONITORING", 10, True, anchor="start")
    s.seg(1330, 624, 1646, 624, "#111111", 1.0)
    for i, (tag, desc) in enumerate([
            ("ST-1001", "P-101A speed feedback"),
            ("IT-1001", "P-101A motor current"),
            ("IT-1002", "P-101B motor current"),
            ("VT-1001", "P-101A vibration"),
            ("TT-1002", "P-101A bearing temperature"),
            ("DT-1001", "charge density")]):
        y = 644 + i * 17
        s.text(1330, y, tag, 8.8, True, anchor="start")
        s.text(1444, y, desc, 8.2, False, "#5A5A5A", anchor="start")

    s.notes([
        "1. All instrument signals are electrical unless noted. Control functions are",
        "    configured in the DCS; the simulator contains no controllers.",
        "2. Valve fail positions: FC fail closed, FO fail open.",
        "3. MOV-1001A/B supply the start permissive for their pump. The permissive is",
        "    a DCS function; the simulator models the hydraulic consequence either way.",
        "4. XV-1001 closes on ESD-1 and ESD-0, see AP-PID-U900.",
        "5. HV-1001 is field operated, with open and closed position switches only.",
        "6. Line numbers read size - service - sequence - material specification.",
    ])
    s.bottom_strip(STANDARD_LEGEND)
    return s


# ===========================================================================
# U200  recycle gas compressor C1
# ===========================================================================
@sheet_for("U200")
def build_u200() -> Sheet:
    s = Sheet("AP-PID-U200", "Recycle gas compressor and anti-surge", "U200",
              sheet="1 of 1", rev="B")
    s.frame()

    # ---------------------------------------------------- V-201 suction drum
    v = s.place("pressurized_vessel", 300, 470, height=220)
    s.text(v.x0 - 14, 496, "V-201", 15, True, anchor="end")
    s.text(v.x0 - 14, 510, "C1 suction KO drum", 10, False, MUTED, anchor="end")

    s.connector(58, 420, "OFF-GAS FROM D3", "AP-PID-U400", "right")
    s.pipe([(190, 420), (v.x0, 420)], arrow_at=0.74, number='10"-G-2001-CS')

    s.bubble(470, 392, "LSHH-2001", "dcs", r=24, leader=(v.x1, 392))
    s.bubble(470, 470, "LT-2001", "dcs", leader=(v.x1, 470))

    # The drain nozzle is offset from the drum centreline so the line does not
    # run through anything hung below the vessel.
    s.pipe([(330, v.y1), (330, 700), (272, 700)], kind="utility")
    lcv = s.valve("valve_control_diaphragm", 230, 700, "LCV-2001", width=52,
                  label="below", fail="FC")
    s.pipe([lcv.left, (190, 700)], kind="utility", arrow_at=0.7)
    s.connector(58, 700, "TO SOUR WATER", "AP-PID-U800", "left")

    # --------------------------------------------------------- suction line
    s.pipe([(v.ax, v.y0), (v.ax, 250), (436, 250)], number='12"-G-2002-CS',
           number_at=0.16)
    s.strainer(470, 250, "F-201")
    s.bubble(470, 152, "PDT-2001", "dcs", r=24, leader=(470, 236))
    s.pipe([(487, 250), (562, 250)])
    s.bubble(620, 152, "FT-2001", "dcs", leader=(620, 250))
    s.bubble(700, 400, "AT-2001", "dcs", leader=(700, 254))
    s.text(700, 438, "molecular weight", 8.6, False, MUTED)

    fcv2 = s.valve("valve_control_diaphragm", 800, 250, "FCV-2002", width=52,
                   label="above", fail="FO")
    s.pipe([(562, 250), fcv2.left])
    s.bubble(900, 400, "PT-2001", "dcs", leader=(900, 254))
    s.bubble(900, 470, "TT-2001", "dcs", leader=(900, 421))

    # ------------------------------------------------------------ C1 machine
    # Tag below the machine: the speed reference drops onto the top of the
    # casing and must not be read through the equipment name.
    c1 = s.place("compressor", 990, 250, height=112, on_axis=True)
    s.text(990, c1.y0 - 22, "C1", 15, True)
    s.text(990, c1.y0 - 8, "Recycle gas compressor, VFD", 10, False, MUTED)
    s.pipe([fcv2.right, c1.left], arrow_at=0.72)

    s.pipe([c1.right, (1192, 250)])
    s.bubble(1060, 380, "PT-2002", "dcs", leader=(1060, 254))
    s.bubble(1060, 450, "TT-2002", "dcs", leader=(1060, 401))

    # ------------------------------------------------ E-201 discharge cooler
    e = s.place("shell_and_tube_1", 1230, 250, height=76, on_axis=True)
    s.text(1230, e.y0 - 22, "E-201", 15, True)
    s.text(1230, e.y0 - 8, "C1 discharge cooler", 10, False, MUTED)

    s.connector(1000, 520, "CW SUPPLY", "AP-PID-U800", "right")
    fcv3 = s.valve("valve_control_diaphragm", 1180, 520, "FCV-2003", width=48,
                   label="above", fail="FO")
    s.pipe([(1132, 520), fcv3.left], kind="utility")
    s.pipe([fcv3.right, (1214, 520), (1214, e.y1)], kind="utility",
           arrow_at=0.86)
    s.pipe([(1246, e.y1), (1246, 600), (1180, 600)], kind="utility",
           arrow_at=0.86)
    s.connector(1048, 600, "CW RETURN", "AP-PID-U800", "left")

    xv = s.valve("valve_gate", 1380, 250, "XV-2001", width=52, label="above")
    s.pipe([e.right, xv.left])
    s.text(1380, xv.y1 + 15, "FC", 9, True)
    s.pipe([xv.right, (1500, 250)], arrow_at=0.6)
    s.connector(1500, 250, "TO REACTOR LOOP", "AP-PID-U400", "right")

    # --------------------------------------------------- anti-surge recycle
    # The one loop that must never be left in manual: the machine is protected
    # by opening this valve, so it fails open and the DCS holds a margin.
    fcv1 = s.valve("valve_control_diaphragm", 840, 690, "FCV-2001", width=56,
                   label="below", fail="FO")
    s.pipe([(1320, 250), (1320, 690), fcv1.right], number='8"-G-2003-CS',
           number_at=0.46)
    s.pipe([fcv1.left, (400, 690), (400, 545), (v.x1, 545)], arrow_at=0.92)
    s.bubble(700, 580, "FT-2002", "dcs", leader=(700, 690))
    s.text(1200, 666, "ANTI-SURGE RECYCLE", 9.5, True)

    # ------------------------------------------------------ lube oil console
    tk = s.place("tank", 220, 880, height=104)
    s.text(220, tk.y1 + 20, "TK-201", 13, True)
    s.text(220, tk.y1 + 33, "Lube oil reservoir", 9.5, False, MUTED)
    s.pipe([(tk.x1, 900), (360, 900)], kind="utility")

    lpa = s.place("centrifugal_pump_1", 470, 840, height=50, on_axis=True)
    s.text(470, lpa.y0 - 14, "P-201", 12, True)
    lpb = s.place("centrifugal_pump_1", 470, 960, height=50, on_axis=True)
    s.text(470, lpb.y1 + 18, "P-202", 12, True)
    s.pipe([(360, 900), (360, 840), lpa.left], kind="utility")
    s.pipe([(360, 900), (360, 960), lpb.left], kind="utility")

    s.pipe([lpa.top, (lpa.ax, 800), (620, 800)], kind="utility")
    s.pipe([lpb.top, (lpb.ax, 916), (620, 916)], kind="utility")
    s.pipe([(620, 800), (620, 916)], kind="utility")
    s.pipe([(620, 858), (900, 858)], kind="utility", arrow_at=0.88)
    s.bubble(700, 950, "PT-2003", "dcs", leader=(700, 862))
    s.bubble(800, 950, "PSL-2001", "dcs", r=24, leader=(800, 862))
    s.bubble(900, 950, "TT-2005", "dcs", leader=(878, 862))
    s.text(908, 848, "TO C1 BEARINGS AND SEALS", 9, True, anchor="start")
    s.text(620, 764, "LUBE OIL CONSOLE", 9.5, True)

    # ------------------------------------------------- DCS control functions
    # Speed and anti-surge sit side by side because they fight each other:
    # the margin override is a link between two adjacent blocks, not a run
    # threaded back across the drawing.
    s.bubble(840, 580, "UIC-2001", "dcs", r=24)
    s.bubble(990, 580, "SIC-2001", "dcs", r=24)
    s.signal([(724, 580), (816, 580)], kind="software")
    s.signal([(840, 604), (840, fcv1.y0 - 6)], kind="software")
    s.signal([(864, 580), (966, 580)], kind="software")
    s.signal([(990, 556), (990, c1.y1 + 6)], kind="software")
    s.text(915, 632, "SURGE MARGIN OVERRIDES SPEED", 8.6, False, MUTED)

    # --------------------------------------------------- schedules on sheet
    s.text(1400, 690, "MOTOR AND VALVE CONTROL", 10, True, anchor="start")
    s.seg(1400, 698, 1646, 698, INK, 1.0)
    for i, (tag, desc) in enumerate([
            ("MC-C1", "start / stop, run, fault, available, VFD"),
            ("MC-P201", "lube oil pump A, start / stop"),
            ("MC-P202", "lube oil pump B, start / stop"),
            ("XC-XV2001", "open / close, both limit switches")]):
        y = 718 + i * 17
        s.text(1400, y, tag, 8.8, True, anchor="start")
        s.text(1500, y, desc, 8.2, False, MUTED, anchor="start")

    s.text(1400, 820, "MACHINE MONITORING AND TRIPS", 10, True, anchor="start")
    s.seg(1400, 828, 1646, 828, INK, 1.0)
    for i, (tag, desc) in enumerate([
            ("ST-2001", "C1 speed feedback"),
            ("SC-2001", "C1 VFD speed reference"),
            ("IT-2001", "C1 motor current"),
            ("IT-2002", "P-201 main lube oil pump current"),
            ("IT-2003", "P-202 auxiliary lube oil pump current"),
            ("VT-2001", "C1 radial vibration"),
            ("VSHH-2001", "vibration high high, trips C1"),
            ("TT-2003", "drive end bearing temperature"),
            ("TT-2004", "non-drive end bearing temperature"),
            ("UY-2001", "surge margin, calculated"),
            ("UA-2001", "surge detected alarm"),
            ("ZT-2001", "FCV-2001 position feedback")]):
        y = 848 + i * 17
        s.text(1400, y, tag, 8.8, True, anchor="start")
        s.text(1500, y, desc, 8.2, False, MUTED, anchor="start")

    s.notes([
        "1. FCV-2001 is the anti-surge valve and fails open. UIC-2001 acts on surge",
        "    margin UY-2001 and overrides the speed reference on a falling margin.",
        "2. C1 trips on VSHH-2001 or PSL-2001. The trip is an ESD function, see",
        "    AP-PID-U900; the simulator models the machine coasting down.",
        "3. AT-2001 molecular weight compensates the surge line. A lighter gas moves",
        "    the surge point and the margin calculation follows it.",
        "4. One lube oil pump runs, the other is on auto start from PSL-2001.",
        "5. Valve fail positions: FC fail closed, FO fail open.",
    ])
    s.bottom_strip(STANDARD_LEGEND)
    return s


# ===========================================================================
# U300  fired charge heater H1
# ===========================================================================
@sheet_for("U300")
def build_u300() -> Sheet:
    s = Sheet("AP-PID-U300", "Charge heater, firing and draft", "U300",
              sheet="1 of 1", rev="D")
    s.frame()

    # A fired heater is not a rectangle: the convection neck is narrower than
    # the firebox, so every connection is taken from the measured face at the
    # elevation the line arrives at.
    f = s.place("furnace", 700, 480, height=380)
    s.text(600, 268, "H1", 15, True, anchor="end")
    s.text(600, 282, "Charge heater, two pass", 10, False, MUTED,
           anchor="end")
    P1, P2 = 520, 620

    # ------------------------------------------------------- charge, 2 pass
    s.connector(58, 570, "CHARGE FROM P-101A/B", "AP-PID-U100", "right")
    s.pipe([(190, 570), (300, 570)], number='6"-P-3001-CS')
    s.pipe([(300, P1), (300, P2)])

    fcv5 = s.valve("valve_control_diaphragm", 460, P1, "FCV-3005", width=50,
                   label="above", fail="FO")
    s.pipe([(300, P1), fcv5.left])
    s.pipe([fcv5.right, f.left_at(P1)], arrow_at=0.74)
    s.bubble(370, 430, "FT-3004", "dcs", leader=(370, P1))
    s.text(346, 508, "PASS 1", 8.6, True, anchor="end")

    fcv6 = s.valve("valve_control_diaphragm", 460, P2, "FCV-3006", width=50,
                   label="below", fail="FO")
    s.pipe([(300, P2), fcv6.left])
    s.pipe([fcv6.right, f.left_at(P2)], arrow_at=0.74)
    s.text(346, 608, "PASS 2", 8.6, True, anchor="end")

    # ------------------------------------------------------- pass outlets
    s.pipe([f.right_at(P1), (920, P1)])
    s.pipe([f.right_at(P2), (920, P2)])
    s.pipe([(920, P1), (920, P2)])
    s.bubble(890, 450, "TT-3002", "dcs", leader=(890, P1))
    s.bubble(890, 690, "TT-3003", "dcs", leader=(890, P2))

    s.pipe([(920, 570), (1150, 570)], arrow_at=0.72, number='8"-P-3002-CS',
           number_at=0.32)
    s.connector(1150, 570, "TO R1 REACTOR", "AP-PID-U400", "right")
    s.bubble(980, 470, "TT-3001", "dcs", leader=(980, 570))
    s.bubble(1080, 470, "TIC-3001", "dcs", r=24)
    s.signal([(1001, 470), (1056, 470)], kind="software")

    # ------------------------------------------------------ flue gas and ID
    s.pipe([(f.ax, f.y0), (1030, 290)], kind="utility")
    idf = s.place("fan", 1060, 290, height=60, on_axis=True)
    s.text(1060, idf.y1 + 20, "ID-301", 13, True)
    s.text(1060, idf.y1 + 33, "Induced draft fan", 9.5, False, MUTED)
    st = s.place("stack", 1180, 190, height=200)
    s.pipe([(idf.x1, 290), (st.ax, 290)], kind="utility", arrow_at=0.6)

    s.bubble(1060, 150, "SIC-3001", "dcs", r=24)
    s.signal([(1060, 174), (1060, idf.y0 - 6)], kind="software")
    s.bubble(860, 180, "AT-3001", "dcs", leader=(860, 290))
    s.text(860, 218, "flue O2", 8.6, False, MUTED)
    s.bubble(940, 180, "AT-3002", "dcs", leader=(940, 290))
    s.text(940, 218, "flue CO", 8.6, False, MUTED)
    s.bubble(1330, 190, "TT-3004", "dcs", leader=(st.x1, 190))
    s.bubble(900, 380, "PT-3003", "dcs", leader=f.right_at(430))
    s.text(900, 418, "firebox draft", 8.6, False, MUTED)

    # ---------------------------------------------------- fuel gas to burner
    s.connector(58, 760, "FUEL GAS FROM U010", "AP-PID-U010", "right")
    s.bubble(200, 680, "PSLL-3001", "dcs", r=24, leader=(200, 760))
    s.bubble(280, 680, "PSHH-3001", "dcs", r=24, leader=(280, 760))

    xv3 = s.valve("valve_gate", 400, 760, "XV-3001", width=52, label="below",
                  fail="FC")
    s.pipe([(190, 760), xv3.left], number='3"-FG-3003-CS', number_at=0.26)

    fcv1 = s.valve("valve_control_diaphragm", 530, 760, "FCV-3001", width=52,
                   label="below", fail="FC")
    s.pipe([xv3.right, fcv1.left])
    s.bubble(370, 680, "FT-3005", "dcs", leader=(370, P2))
    s.bubble(460, 680, "FT-3001", "dcs", leader=(460, 760))
    s.bubble(530, 680, "FIC-3001", "dcs", r=24)
    s.signal([(530, 704), (530, fcv1.y0 - 6)], kind="software")
    s.pipe([fcv1.right, (650, 760), (650, f.y1)], arrow_at=0.86)
    s.bubble(620, 880, "PT-3001", "dcs", leader=(620, 764))

    # ---------------------------------------------------- fuel oil to burner
    # Two valves in parallel on split range: the small one takes the bottom
    # half of the firing output, the large one the top half.
    s.connector(58, 930, "FUEL OIL FROM OSBL", "AP-PID-U000", "right")
    hv3 = s.valve("valve_gate", 280, 930, "HV-3001", width=50, label="above")
    s.pipe([(190, 930), hv3.left], number='2"-FO-3004-CS', number_at=0.34)
    xv32 = s.valve("valve_gate", 352, 930, "XV-3002", width=44,
                   label="below")
    s.pipe([hv3.right, xv32.left])
    s.pipe([xv32.right, (400, 930)])
    s.pipe([(400, 880), (400, 980)])

    fcv2 = s.valve("valve_control_diaphragm", 500, 880, "FCV-3002", width=50,
                   label="above", fail="FC")
    s.pipe([(400, 880), fcv2.left])
    s.pipe([fcv2.right, (660, 880)])
    fcv3 = s.valve("valve_control_diaphragm", 500, 980, "FCV-3003", width=50,
                   label="above", fail="FC")
    s.pipe([(400, 980), fcv3.left])
    s.pipe([fcv3.right, (660, 980)])
    s.pipe([(660, 880), (660, 980)])
    s.pipe([(660, 930), (720, 930), (720, f.y1)], arrow_at=0.9)

    s.bubble(210, 860, "PT-3002", "dcs", leader=(210, 930))
    s.bubble(330, 860, "FT-3002", "dcs", leader=(330, 930))
    s.text(760, 1000, "FCV-3002 AND FCV-3003 ON SPLIT RANGE, SEE NOTE 3",
           8.4, True, anchor="start")

    # ------------------------------------------------------ combustion air
    s.connector(1020, 930, "COMBUSTION AIR", "AP-PID-U000", "left")
    fcv4 = s.valve("valve_control_diaphragm", 900, 930, "FCV-3004", width=50,
                   label="below", fail="FO")
    s.pipe([(1020, 930), fcv4.right], kind="utility")
    s.pipe([fcv4.left, (800, 930), (800, f.y1)], kind="utility", arrow_at=0.9)
    s.bubble(900, 850, "AIC-3001", "dcs", r=24)
    s.signal([(900, 874), (900, fcv4.y0 - 6)], kind="software")
    s.bubble(990, 840, "FT-3003", "dcs", leader=(990, 926))

    s.bubble(980, 700, "BS-3002", "dcs", leader=f.right_at(646))
    s.bubble(980, 760, "BS-3001", "dcs", leader=f.right_at(660))

    # --------------------------------------------------- schedules on sheet
    s.text(1350, 620, "COMBUSTION CONTROL", 10, True, anchor="start")
    s.seg(1350, 628, 1646, 628, INK, 1.0)
    for i, (tag, desc) in enumerate([
            ("TIC-3001", "outlet temperature, cascades to FIC-3001"),
            ("FIC-3001", "fuel gas flow, fuel and air cross limited"),
            ("FIC-3003", "combustion air flow on the damper"),
            ("AIC-3001", "flue oxygen trims the excess-air target"),
            ("TIC-3004", "tube skin override, low select into firing"),
            ("PIC-3001", "fuel oil header pressure, split range"),
            ("ZC-3001", "holds FCV-3002 at working travel"),
            ("PIC-3002", "firebox draft, cascades to SC-3001"),
            ("SC-3001", "ID-301 VFD speed reference"),
            ("IT-3001", "ID-301 motor current"),
            ("ZT-3001", "FCV-3001 position feedback"),
            ("ZT-3002", "FCV-3002 position feedback"),
            ("ZT-3003", "FCV-3003 position feedback"),
            ("XS-ID301-AVL/VFD", "ID fan available and VFD healthy"),
            ("TT-3005", "pass 1 tube skin temperature"),
            ("TT-3006", "pass 2 tube skin temperature")]):
        y = 648 + i * 17
        s.text(1350, y, tag, 8.8, True, anchor="start")
        s.text(1450, y, desc, 8.2, False, MUTED, anchor="start")

    s.text(1350, 850, "BURNER MANAGEMENT", 10, True, anchor="start")
    s.seg(1350, 858, 1646, 858, INK, 1.0)
    for i, (tag, desc) in enumerate([
            ("BS-3001", "main burner flame detected"),
            ("BS-3002", "pilot flame detected"),
            ("XS-3010", "furnace purge complete permissive"),
            ("XY-3010", "burner igniter energise"),
            ("XY-3011", "furnace purge sequence start"),
            ("PSLL-3001", "fuel gas low low, closes XV-3001"),
            ("PSHH-3001", "fuel gas high high, closes XV-3001")]):
        y = 878 + i * 17
        s.text(1350, y, tag, 8.8, True, anchor="start")
        s.text(1450, y, desc, 8.2, False, MUTED, anchor="start")

    s.notes([
        "1. Firing and burner management are detailed on logic diagram AP-LOG-U300.",
        "    The simulator models the heat release and the consequence of losing it.",
        "2. XV-3001 closes on ESD-1, on either fuel gas pressure trip and on loss of",
        "    flame. It cannot be opened until XS-3010 purge complete is proved.",
        "3. FCV-3002 and FCV-3003 are split range on the oil header pressure",
        "    controller PIC-3001, whose setpoint the valve position controller",
        "    ZC-3001 trims. XV-3002 isolates the oil train on a heater trip.",
        "4. Pass balancing valves FCV-3005 and FCV-3006 fail open, so a signal",
        "    failure does not dry out a pass.",
        "5. Valve fail positions: FC fail closed, FO fail open.",
    ])
    s.bottom_strip(STANDARD_LEGEND)
    return s


# ===========================================================================
# U400  reactor and product separator
# ===========================================================================
@sheet_for("U400")
def build_u400() -> Sheet:
    s = Sheet("AP-PID-U400", "Reactor and product separator", "U400",
              sheet="1 of 1", rev="C")
    s.frame()

    r1 = s.place("pressurized_vessel", 450, 480, height=340)
    s.text(350, 700, "R1", 15, True, anchor="end")
    s.text(350, 714, "Reactor, two catalyst beds", 10, False, MUTED,
           anchor="end")

    # ------------------------------------------------------------- feed in
    s.connector(58, 340, "FROM H1 CHARGE HEATER", "AP-PID-U300", "right")
    xv1 = s.valve("valve_gate", 330, 340, "XV-4001", width=52, label="above")
    s.pipe([(190, 340), xv1.left], number='8"-P-4001-CS', number_at=0.26)
    s.text(330, xv1.y1 + 15, "FC", 9, True)
    s.pipe([xv1.right, r1.left_at(340)], arrow_at=0.7)
    s.bubble(220, 240, "TT-4001", "dcs", leader=(220, 340))
    s.bubble(280, 170, "PT-4001", "dcs", leader=(280, 340))

    # blowdown: the reactor is depressured to flare on demand
    s.pipe([(r1.ax, r1.y0), (r1.ax, 200), (600, 200)])
    bdv = s.valve("valve_gate", 660, 200, "BDV-4001", width=52, label="above")
    s.pipe([bdv.right, (760, 200)], arrow_at=0.6)
    s.connector(760, 200, "TO FLARE", "AP-PID-U800", "right")
    s.text(660, bdv.y1 + 15, "FC", 9, True)

    # ------------------------------------------------------------ quench gas
    s.connector(58, 470, "QUENCH GAS FROM C1", "AP-PID-U200", "right")
    fcvq = s.valve("valve_control_diaphragm", 280, 470, "FCV-4001", width=52,
                   label="above", fail="FC")
    s.pipe([(190, 470), fcvq.left])
    s.pipe([fcvq.right, r1.left_at(470)], arrow_at=0.7)
    s.bubble(280, 560, "FT-4001", "dcs", leader=(280, 474))
    s.text(280, 598, "interbed quench", 8.6, False, MUTED)

    # ------------------------------------------------- reactor instruments
    s.bubble(660, 380, "TT-4002", "dcs", leader=r1.right_at(380))
    s.bubble(660, 450, "TT-4003", "dcs", leader=r1.right_at(450))
    s.bubble(660, 600, "TT-4004", "dcs", leader=r1.right_at(600))
    s.bubble(790, 340, "PSHH-4001", "dcs", r=24, leader=r1.right_at(340))
    s.bubble(790, 520, "TSHH-4001", "dcs", r=24, leader=r1.right_at(520))
    s.bubble(920, 620, "PDT-4001", "dcs", r=24, leader=r1.right_at(640))

    # ------------------------------------------- effluent cooler and D3
    s.pipe([(r1.ax, r1.y1), (r1.ax, 760), (842, 760)],
           number='10"-P-4002-CS', number_at=0.62)
    e = s.place("shell_and_tube_1", 880, 760, height=76, on_axis=True)
    s.text(880, e.y0 - 22, "E-401", 14, True)
    s.text(880, e.y0 - 8, "Effluent cooler", 9.5, False, MUTED)
    s.bubble(760, 700, "AT-4001", "dcs", leader=(760, 760))
    s.text(760, 662, "product impurity", 8.6, False, MUTED)

    s.connector(620, 980, "CW SUPPLY", "AP-PID-U800", "right")
    fcvw = s.valve("valve_control_diaphragm", 830, 980, "FCV-4002", width=50,
                   label="above", fail="FO")
    s.pipe([(752, 980), fcvw.left], kind="utility")
    s.pipe([fcvw.right, (900, 980), (900, e.y1)], kind="utility", arrow_at=0.8)
    s.pipe([(860, e.y1), (860, 900), (700, 900)], kind="utility", arrow_at=0.8)
    s.connector(568, 900, "CW RETURN", "AP-PID-U800", "left")

    d3 = s.place("vessel_horizontal", 1230, 760, height=150)
    s.text(1300, 600, "D3", 15, True)
    s.text(1300, 614, "Product separator, three phase", 10, False, MUTED)
    s.pipe([e.right, d3.left_at(760)], arrow_at=0.72)

    s.bubble(1020, 700, "LT-4001", "dcs", leader=d3.left_at(700))
    s.bubble(1020, 790, "LT-4002", "dcs", leader=d3.left_at(786))
    s.bubble(1020, 600, "LSHH-4001", "dcs", r=24, leader=d3.left_at(690))
    s.bubble(920, 880, "LSLL-4001", "dcs", r=24, leader=d3.left_at(780))
    s.bubble(1470, 680, "PT-4002", "dcs", leader=d3.right_at(680))
    s.bubble(1470, 750, "TT-4005", "dcs", leader=d3.right_at(750))

    # ------------------------------------------------------ D3 off-gas, top
    # The off-gas nozzle is offset from the shell centreline so the riser
    # does not run through the vessel name.
    xv2 = s.valve("valve_gate", 1340, 440, "XV-4002", width=52, label="above")
    s.pipe([(1150, d3.y0), (1150, 440), xv2.left],
           number='10"-G-4003-CS', number_at=0.3)
    s.text(1340, xv2.y1 + 15, "FC", 9, True)
    pcv = s.valve("valve_control_diaphragm", 1450, 440, "PCV-4001", width=52,
                  label="above", fail="FO")
    s.pipe([xv2.right, pcv.left])
    s.pipe([pcv.right, (1520, 440)], arrow_at=0.6)
    s.connector(1520, 440, "TO C1 AND FUEL HEADER", "AP-PID-U200", "right")
    s.bubble(1240, 360, "AT-4002", "dcs", leader=(1240, 440))
    s.bubble(1180, 360, "FT-4002", "dcs", leader=(1180, 440))

    # ------------------------------------------- hydrocarbon liquid to T1
    s.pipe([(1300, 797), (1300, 890), (1360, 890)])
    lcv1 = s.valve("valve_control_diaphragm", 1420, 890, "LCV-4001", width=52,
                   label="above", fail="FC")
    s.pipe([lcv1.right, (1500, 890)], arrow_at=0.6)
    s.connector(1500, 890, "TO T1 COLUMN", "AP-PID-U500", "right")
    s.bubble(1250, 830, "FT-4003", "dcs", leader=(1300, 830))

    # ----------------------------------------------- sour water off the boot
    s.pipe([(1190, d3.y1), (1190, 950), (1260, 950)], kind="utility")
    lcv2 = s.valve("valve_control_diaphragm", 1320, 950, "LCV-4002", width=52,
                   label="below", fail="FC")
    s.pipe([lcv2.right, (1400, 950)], kind="utility", arrow_at=0.6)
    s.connector(1400, 950, "TO SOUR WATER", "AP-PID-U800", "right")
    s.bubble(1120, 950, "FT-4004", "dcs", leader=(1190, 950))

    # --------------------------------------------------- schedules on sheet
    s.text(940, 140, "SAFEGUARDING AND VALVE CONTROL", 10, True,
           anchor="start")
    s.seg(940, 148, 1350, 148, INK, 1.0)
    for i, (tag, desc) in enumerate([
            ("PSHH-4001", "reactor pressure high high, opens BDV-4001"),
            ("TSHH-4001", "catalyst bed temperature high high, ESD-2"),
            ("LSHH-4001", "D3 level high high, closes XV-4002"),
            ("LSLL-4001", "D3 level low low, closes LCV-4001"),
            ("XC-XV4001", "reactor feed shut off, open and closed limits"),
            ("XC-XV4002", "off-gas shut off, open and closed limits"),
            ("XC-BDV4001", "blowdown to flare, open and closed limits"),
            ("XI-4001", "reactor conversion, calculated")]):
        y = 168 + i * 17
        s.text(940, y, tag, 8.8, True, anchor="start")
        s.text(1060, y, desc, 8.2, False, MUTED, anchor="start")

    s.notes([
        "1. R1 is adiabatic. Bed temperature is controlled only by the interbed",
        "    quench FCV-4001; there is no cooling medium in the reactor.",
        "2. BDV-4001 opens on PSHH-4001 and on ESD-2. It fails closed, so a loss of",
        "    instrument air does not depressure the reactor.",
        "3. D3 separates three phases. LT-4001 reads total liquid and LT-4002 reads",
        "    the water to hydrocarbon interface in the boot.",
        "4. Losing the interface draw LCV-4002 carries water forward into T1, which",
        "    is the intended consequence, not a modelling defect.",
        "5. Valve fail positions: FC fail closed, FO fail open.",
    ])
    s.bottom_strip(STANDARD_LEGEND)
    return s


# ===========================================================================
# U500 and U600  the two distillation columns
# ===========================================================================
# T1 and T2 are the same machine with different numbers, so they are drawn by
# one pair of builders. Each column takes two sheets: crowding the overhead
# train and the reboiler onto one sheet is what makes a P&ID unreadable.
COLUMNS = {
    "U500": dict(n=5, col="T1", stages=32, boot=True,
                 feed=("FROM D3 SEPARATOR", "AP-PID-U400"),
                 dist=("TO PRODUCT STORAGE", "AP-PID-U800"),
                 bot=("TO T2 COLUMN", "AP-PID-U600")),
    "U600": dict(n=6, col="T2", stages=40, boot=False,
                 feed=("FROM T1 BOTTOMS", "AP-PID-U500"),
                 dist=("TO PRODUCT STORAGE", "AP-PID-U800"),
                 bot=("TO D1 FEED SURGE DRUM", "AP-PID-U100")),
}


def shell_bottom(p, x: float) -> float:
    """Lowest drawn edge of a symbol directly under ``x``.

    A horizontal drum has a water boot hanging below its shell, so ``y1`` is
    the bottom of the boot, not the bottom of the shell. A liquid draw taken
    anywhere but the boot has to land on the shell.
    """
    y = p.y1
    while y > p.y0:
        a, b = p.span_at(y)
        if a <= x <= b:
            return y
        y -= 2.0
    return p.y1


def build_column_overhead(code: str) -> Sheet:
    """Sheet 1: column, feed, condenser, reflux drum, reflux and distillate."""
    c = COLUMNS[code]
    n, name = c["n"], c["col"]
    s = Sheet(f"AP-PID-{code}", f"{name} column and overhead system", code,
              sheet="1 of 2", rev="A")
    s.frame()

    col = s.place("column_common", 400, 560, height=480)
    s.text(340, 300, name, 15, True, anchor="end")
    s.text(340, 314, f"{c['stages']} valve trays", 10, False, MUTED,
           anchor="end")
    s.pipe([(col.ax, col.y1), (col.ax, 900), (470, 900)])
    s.connector(470, 900, "TO REBOILER AND BOTTOMS", f"AP-PID-{code} SH 2",
                "right")

    # ------------------------------------------------------------ feed in
    s.connector(58, 600, *c["feed"], "right")
    s.pipe([(190, 600), col.left_at(600)], arrow_at=0.72,
           number=f'8"-P-{n}001-CS')
    s.bubble(230, 690, f"FT-{n}001", "dcs", leader=(230, 600))
    s.bubble(310, 690, f"TT-{n}001", "dcs", leader=(310, 600))
    s.bubble(230, 460, f"PSHH-{n}001", "dcs", r=24, leader=col.left_at(460))
    s.bubble(230, 790, f"PDT-{n}001", "dcs", r=24, leader=col.left_at(760))

    s.bubble(510, 360, f"PT-{n}001", "dcs", leader=col.right_at(360))
    s.bubble(510, 430, f"TT-{n}002", "dcs", leader=col.right_at(430))
    s.bubble(510, 500, f"TT-{n}003", "dcs", leader=col.right_at(500))
    s.bubble(510, 570, f"TT-{n}004", "dcs", leader=col.right_at(570))

    # ------------------------------------------------- overhead to condenser
    s.pipe([(col.ax, col.y0), (col.ax, 220), (522, 220)],
           number=f'14"-V-{n}002-CS', number_at=0.62)
    e1 = s.place("shell_and_tube_1", 560, 220, height=76, on_axis=True)
    s.text(560, e1.y0 - 22, f"E-{n}01", 14, True)
    s.text(560, e1.y0 - 8, "Overhead condenser", 9.5, False, MUTED)
    s.bubble(460, 160, f"TT-{n}005", "dcs", leader=(460, 220))

    # cooling water, taken off to the right so it clears the tray instruments
    s.connector(700, 500, "CW SUPPLY", "AP-PID-U800", "left")
    fcv5 = s.valve("valve_control_diaphragm", 620, 500, f"FCV-{n}005", width=50,
                   label="above", fail="FO")
    s.pipe([(700, 500), fcv5.right], kind="utility")
    s.pipe([fcv5.left, (550, 500), (550, e1.y1)], kind="utility", arrow_at=0.86)
    s.pipe([(580, e1.y1), (580, 420), (640, 420)], kind="utility", arrow_at=0.8)
    s.connector(640, 420, "CW RETURN", "AP-PID-U800", "right")
    s.bubble(660, 330, f"FT-{n}006", "dcs", leader=(580, 330))

    # ----------------------------------------------------- D5 reflux drum
    # T1 separates water in the drum boot; T2 does not, so it gets the
    # plain drum rather than a boot nobody draws from.
    d5 = s.place("vessel_horizontal" if c["boot"] else "vessel_horizontal_2",
                 1000, 400, height=140)
    s.pipe([e1.right, (950, 220), (950, d5.y0)], arrow_at=0.8)
    s.text(1140, 330, f"D{n}", 15, True, anchor="start")
    s.text(1140, 344, "Reflux drum", 10, False, MUTED, anchor="start")
    s.bubble(820, 380, f"LSHH-{n}001", "dcs", r=24, leader=d5.left_at(380))
    s.bubble(820, 460, f"LSLL-{n}001", "dcs", r=24, leader=d5.left_at(450))
    s.bubble(1220, 400, f"LT-{n}001", "dcs", leader=d5.right_at(400))
    s.bubble(760, 290, f"TT-{n}007", "dcs", leader=(760, 220))

    # overhead pressure to flare
    # The vent riser is taken off the right of the drum head so it does not
    # cross the condensate line running in from the condenser.
    s.pipe([(1060, d5.y0), (1060, 150), (1120, 150)])
    pcv = s.valve("valve_control_diaphragm", 1180, 150, f"PCV-{n}001",
                  width=52, label="below", fail="FO")
    s.pipe([pcv.right, (1260, 150)], arrow_at=0.6)
    s.connector(1260, 150, "TO FLARE HEADER", "AP-PID-U800", "right")

    if c["boot"]:
        s.pipe([(1000, d5.y1), (1000, 560), (1080, 560)], kind="utility")
        lcv = s.valve("valve_control_diaphragm", 1140, 560, f"LCV-{n}001",
                      width=52, label="below", fail="FC")
        s.pipe([lcv.right, (1220, 560)], kind="utility", arrow_at=0.6)
        s.connector(1220, 560, "TO SOUR WATER", "AP-PID-U800", "right")
        s.bubble(1240, 500, f"LT-{n}003", "dcs", leader=d5.right_at(455))
        s.text(880, 620, "WATER BOOT", 8.6, True, anchor="end")

    # ------------------------------------------------------- reflux pumps
    yb = shell_bottom(d5, 920)
    s.pipe([(920, yb), (920, 780)], number=f'8"-P-{n}003-CS', number_at=0.4)
    mova = s.valve("valve_motor_operated", 1060, 660, f"MOV-{n}001A", width=56,
                   label="above")
    movb = s.valve("valve_motor_operated", 1060, 780, f"MOV-{n}001B", width=56,
                   label="below")
    s.pipe([(920, 660), mova.left])
    s.pipe([(920, 780), movb.left])

    pa = s.place("centrifugal_pump_1", 1300, 660, height=56, on_axis=True)
    s.text(1300, pa.y0 - 14, f"P-{n}01A", 12, True)
    pb = s.place("centrifugal_pump_1", 1300, 780, height=56, on_axis=True)
    s.text(1300, pb.y1 + 18, f"P-{n}01B", 12, True)
    s.pipe([mova.right, pa.left], arrow_at=0.62)
    s.pipe([movb.right, pb.left], arrow_at=0.62)

    s.pipe([pa.top, (pa.ax, 612), (1420, 612)])
    s.pipe([pb.top, (pb.ax, 732), (1420, 732)])
    s.pipe([(1420, 612), (1420, 732)])
    s.bubble(1500, 672, f"PT-{n}003", "dcs", leader=(1420, 672))

    # reflux back to the top of the column, above everything else on the sheet
    s.pipe([(1420, 612), (1420, 120), (200, 120), (200, 380),
            col.left_at(380)], arrow_at=0.97)
    fcv1 = s.valve("valve_control_diaphragm", 700, 120, f"FCV-{n}001", width=52,
                   label="above", fail="FC")
    s.bubble(140, 300, f"FT-{n}002", "dcs", leader=(200, 300))
    s.text(950, 104, f'6"-P-{n}004-CS  REFLUX', 9, True)

    # distillate off the same header
    s.pipe([(1420, 732), (1420, 880), (1266, 880)])
    fcv2 = s.valve("valve_control_diaphragm", 1240, 880, f"FCV-{n}002",
                   width=52, label="above", fail="FC")
    s.pipe([fcv2.left, (1160, 880)], arrow_at=0.6)
    s.connector(1028, 880, *c["dist"], "left")
    s.bubble(1360, 930, f"FT-{n}003", "dcs", leader=(1360, 884))
    s.bubble(1060, 930, f"AT-{n}001", "dcs", leader=(1180, 884))

    # minimum flow back to the drum keeps the running pump off its curve end
    s.pipe([(1420, 700), (1500, 700), (1500, 970), (920, 970), (920, 780)],
           kind="utility")
    fcv6 = s.valve("valve_control_diaphragm", 1180, 970, f"FCV-{n}006",
                   width=50, label="above", fail="FO")
    s.bubble(980, 915, f"FT-{n}007", "dcs", leader=(980, 966))
    s.text(1260, 962, "PUMP MINIMUM FLOW", 8.4, True, anchor="start")

    notes = [
        "1. Reflux, distillate and pressure control are DCS functions. The",
        "    simulator models the tray hydraulics and the separation, not the loops.",
        "2. FCV-{n}001 fails closed. Losing reflux dries the top of the column and".replace("{n}", str(n)),
        "    the distillate composition AT-{n}001 degrades within minutes.".replace("{n}", str(n)),
        "3. One reflux pump runs. MOV-{n}001A and B give the start permissive; the".replace("{n}", str(n)),
        "    standby starts on low discharge pressure PT-{n}003.".replace("{n}", str(n)),
        "4. Reboiler, bottoms pumps and steam are on sheet 2.",
        "5. IT-{n}001 and IT-{n}002 read the reflux pump motor currents;".replace("{n}", str(n)),
        "    ZSO/ZSC-HV{n}001 report the reflux manual bypass position.".replace("{n}", str(n)),
        "6. Valve fail positions: FC fail closed, FO fail open.",
    ]
    if n == 5:
        # the hot gas bypass exists on T1 only, split range with PCV-5001
        pcvb = s.valve("valve_control_diaphragm", 880, 150, "PCV-5002",
                       width=50, label="below", fail="FC")
        s.pipe([(500, 220), (500, 150), pcvb.left])
        s.pipe([pcvb.right, (947, 150)], arrow_at=0.7)
        s.text(660, 196, "HOT GAS BYPASS, SEE NOTE 5a", 8.4, True,
               anchor="start")
        notes.insert(9, "5a. PCV-5002 bypasses the condenser on split range "
                        "with PCV-5001: low")
        notes.insert(10, "    controller output holds pressure up against a "
                         "cold condenser.")
    s.notes(notes)
    s.bottom_strip(STANDARD_LEGEND)
    return s


def build_column_bottoms(code: str) -> Sheet:
    """Sheet 2: sump, thermosiphon reboiler, steam and the bottoms pumps."""
    c = COLUMNS[code]
    n, name = c["n"], c["col"]
    s = Sheet(f"AP-PID-{code}", f"{name} reboiler and bottoms system", code,
              sheet="2 of 2", rev="B")
    s.frame()

    col = s.place("column_common", 360, 520, height=440)
    s.connector(120, 180, "FROM COLUMN SHEET 1", f"AP-PID-{code} SH 1",
                "right")
    s.pipe([(252, 180), (col.ax, 180), (col.ax, col.y0)])
    s.text(280, 250, name, 15, True, anchor="end")
    s.text(280, 264, "column, lower section", 10, False, MUTED, anchor="end")

    s.bubble(180, 380, f"AT-{n}002", "dcs", leader=col.left_at(380))
    s.text(180, 418, "light key in bottoms", 8.4, False, MUTED)
    s.bubble(180, 480, f"PT-{n}002", "dcs", leader=col.left_at(480))
    s.bubble(180, 570, f"TT-{n}006", "dcs", leader=col.left_at(570))
    s.bubble(180, 660, f"LT-{n}002", "dcs", leader=col.left_at(660))
    s.bubble(180, 760, f"LSLL-{n}002", "dcs", r=24, leader=col.left_at(720))

    # ------------------------------------------------- thermosiphon reboiler
    s.pipe([col.right_at(700), (520, 700), (520, 780)])
    reb = s.place("shell_and_tube_1", 600, 780, height=80, on_axis=True)
    s.pipe([(520, 780), reb.left])
    s.text(480, 846, f"E-{n}02", 14, True, anchor="end")
    s.text(480, 860, "Column reboiler", 9.5, False, MUTED, anchor="end")
    s.pipe([reb.top, (reb.ax, 620), col.right_at(620)], arrow_at=0.9)
    s.text(500, 600, "TWO PHASE RETURN", 8.4, True)

    # Steam comes down from the top of the sheet so it never shares a run with
    # the bottoms line leaving the sump.
    s.connector(880, 300, "MP STEAM", "AP-PID-U700", "left")
    fcv4 = s.valve("valve_control_diaphragm", 800, 300, f"FCV-{n}004", width=52,
                   label="above", fail="FC")
    s.pipe([(880, 300), fcv4.right], kind="utility")
    s.pipe([fcv4.left, (700, 300), (700, 780), (reb.x1, 780)], kind="utility",
           arrow_at=0.94)
    s.bubble(790, 420, f"FT-{n}005", "dcs", leader=(700, 420))

    s.pipe([(570, reb.y1), (570, 900), (700, 900)], kind="utility",
           arrow_at=0.8)
    s.connector(700, 900, "CONDENSATE RETURN", "AP-PID-U700", "right")

    # ---------------------------------------------------- bottoms pumps
    s.pipe([(col.ax, col.y1), (col.ax, 960), (940, 960)],
           number=f'6"-P-{n}005-CS', number_at=0.28)
    s.pipe([(940, 840), (940, 960)])
    mova = s.valve("valve_motor_operated", 1060, 840, f"MOV-{n}002A", width=56,
                   label="above")
    movb = s.valve("valve_motor_operated", 1060, 960, f"MOV-{n}002B", width=56,
                   label="above")
    s.pipe([(940, 840), mova.left])
    s.pipe([(940, 960), movb.left])

    pa = s.place("centrifugal_pump_1", 1300, 840, height=56, on_axis=True)
    s.text(1300, pa.y0 - 14, f"P-{n}02A", 12, True)
    pb = s.place("centrifugal_pump_1", 1300, 960, height=56, on_axis=True)
    s.text(pb.x1 + 14, 966, f"P-{n}02B", 12, True, anchor="start")
    s.pipe([mova.right, pa.left], arrow_at=0.62)
    s.pipe([movb.right, pb.left], arrow_at=0.62)

    s.pipe([pa.top, (pa.ax, 790), (1460, 790)])
    s.pipe([pb.top, (pb.ax, 910), (1460, 910)])
    s.pipe([(1460, 790), (1460, 910)])
    s.bubble(1560, 850, f"PT-{n}004", "dcs", leader=(1460, 850))

    s.pipe([(1460, 790), (1460, 560), (1214, 560)])
    fcv3 = s.valve("valve_control_diaphragm", 1180, 560, f"FCV-{n}003",
                   width=52, label="above", fail="FC")
    s.pipe([fcv3.left, (1100, 560)], arrow_at=0.6)
    s.connector(968, 560, *c["bot"], "left")
    s.bubble(1300, 480, f"FT-{n}004", "dcs", leader=(1300, 564))

    # minimum flow back to the sump line, crossing the product riser once
    s.pipe([(1460, 850), (1600, 850), (1600, 660), (820, 660), (820, 960)],
           kind="utility")
    fcv7 = s.valve("valve_control_diaphragm", 1100, 660, f"FCV-{n}007",
                   width=50, label="above")
    s.bubble(960, 600, f"FT-{n}008", "dcs", leader=(960, 656))
    s.text(1240, 640, "PUMP MINIMUM FLOW", 8.4, True, anchor="start")

    s.notes([
        "1. The reboiler is a natural circulation thermosiphon. Circulation is set",
        "    by the density difference, not by a pump, so it stops if the sump",
        "    level falls below the draw nozzle.",
        "2. FCV-{n}004 fails closed. Losing steam collapses the vapour traffic and".replace("{n}", str(n)),
        "    the column floods down rather than up.",
        "3. LSLL-{n}002 stops the bottoms pumps to protect them from running dry.".replace("{n}", str(n)),
        "3a. IT-{n}003 and IT-{n}004 read the bottoms pump motor currents.".replace("{n}", str(n)),
        "4. Minimum flow valve FCV-{n}007 fails open.".replace("{n}", str(n)),
        "5. Column, condenser, reflux drum and reflux pumps are on sheet 1.",
        "6. Valve fail positions: FC fail closed, FO fail open.",
    ])
    s.bottom_strip(STANDARD_LEGEND)
    return s


for _code in ("U500", "U600"):
    BUILDERS[_code] = (lambda cc=_code: build_column_overhead(cc))
    BUILDERS[_code + "B"] = (lambda cc=_code: build_column_bottoms(cc))


# ===========================================================================
# U010  fuel gas and utility headers
# ===========================================================================
@sheet_for("U010")
def build_u010() -> Sheet:
    s = Sheet("AP-PID-U010", "Fuel gas and utility headers", "U010",
              sheet="1 of 1", rev="A")
    s.frame()

    # ------------------------------------------------------- natural gas in
    s.connector(58, 400, "NATURAL GAS FROM OSBL", "AP-PID-U000", "right")
    mov = s.valve("valve_motor_operated", 260, 400, "MOV-0101", width=56,
                  label="below")
    s.pipe([(190, 400), mov.left])
    s.bubble(340, 320, "FT-0101", "dcs", leader=(340, 400))
    xv01 = s.valve("valve_gate", 370, 400, "XV-0101", width=44,
                   label="below")
    s.pipe([mov.right, xv01.left])
    pcv1 = s.valve("valve_control_diaphragm", 448, 400, "PCV-0101", width=52,
                   label="above", fail="FC")
    s.pipe([xv01.right, pcv1.left])
    s.pipe([pcv1.right, (1240, 400)], number='6"-FG-0101-CS', number_at=0.1)

    # D3 off-gas joins the same header, which is why a light gas upset in the
    # reactor loop is felt at every burner on the plant.
    s.connector(58, 620, "OFF-GAS FROM D3", "AP-PID-U400", "right")
    fcv1 = s.valve("valve_control_diaphragm", 300, 620, "FCV-0101", width=52,
                   label="below", fail="FC")
    s.pipe([(190, 620), fcv1.left])
    s.pipe([fcv1.right, (560, 620), (560, 400)], arrow_at=0.9)
    s.bubble(450, 700, "FT-0102", "dcs", leader=(450, 620))

    # ---------------------------------------------------- header conditions
    s.bubble(640, 300, "PT-0101", "dcs", leader=(640, 400))
    s.bubble(720, 300, "TT-0101", "dcs", leader=(720, 400))
    s.bubble(820, 300, "AT-0101", "dcs", leader=(820, 400))
    s.text(820, 262, "heating value", 8.4, False, MUTED)
    s.bubble(900, 220, "AT-0102", "dcs", leader=(900, 400))
    s.text(900, 182, "specific gravity", 8.4, False, MUTED)
    s.bubble(640, 520, "PSLL-0101", "dcs", r=24, leader=(640, 400))
    s.bubble(740, 520, "PSHH-0101", "dcs", r=24, leader=(740, 400))

    s.pipe([(1000, 400), (1000, 200), (1080, 200)])
    pcv2 = s.valve("valve_control_diaphragm", 1140, 200, "PCV-0102", width=52,
                   label="above", fail="FO")
    s.pipe([pcv2.right, (1220, 200)], arrow_at=0.6)
    s.connector(1220, 200, "TO FLARE HEADER", "AP-PID-U800", "right")
    s.text(1000, 170, "HEADER RELIEF", 8.6, True, anchor="end")

    # ------------------------------------------------------ pressure letdown
    s.pipe([(1100, 400), (1100, 560), (1160, 560)])
    pcv3 = s.valve("valve_control_diaphragm", 1220, 560, "PCV-0103", width=52,
                   label="above", fail="FC")
    s.pipe([pcv3.right, (1300, 560)], arrow_at=0.6)
    s.connector(1300, 560, "TO H1 CHARGE HEATER", "AP-PID-U300", "right")
    s.bubble(1160, 660, "PT-0102", "dcs", leader=(1160, 564))

    s.pipe([(1240, 400), (1240, 760), (1320, 760)])
    pcv4 = s.valve("valve_control_diaphragm", 1380, 760, "PCV-0104", width=52,
                   label="above", fail="FC")
    s.pipe([pcv4.right, (1460, 760)], arrow_at=0.6)
    s.connector(1460, 760, "TO B1 STEAM BOILER", "AP-PID-U700", "right")
    s.bubble(1320, 860, "PT-0103", "dcs", leader=(1320, 764))

    # ------------------------------------------------------- other utilities
    s.connector(58, 900, "INSTRUMENT AIR FROM OSBL", "AP-PID-U000", "right")
    s.pipe([(190, 900), (480, 900)], kind="utility", arrow_at=0.8)
    s.connector(480, 900, "TO ALL UNITS", "AP-PID-U000", "right")
    s.bubble(280, 975, "PT-0104", "dcs", leader=(280, 904))
    s.bubble(380, 975, "PSL-0102", "dcs", r=24, leader=(380, 904))

    s.connector(700, 900, "COOLING WATER SUPPLY", "AP-PID-U800", "right")
    s.pipe([(832, 900), (1100, 900)], kind="utility", arrow_at=0.8)
    s.connector(1100, 900, "TO ALL UNITS", "AP-PID-U000", "right")
    s.bubble(960, 975, "TT-0102", "dcs", leader=(960, 904))
    s.bubble(1050, 975, "FT-0103", "dcs", leader=(1050, 904))

    s.notes([
        "1. The fuel gas header is common to H1 and B1. Off-gas quality from D3",
        "    therefore changes the firing at both, which is the point of AT-0101.",
        "2. PCV-0102 relieves the header to flare and fails open.",
        "3. PSLL-0101 and PSHH-0101 close XV-3001 and XV-7001 at the burners.",
        "    XV-0101 is the battery limit shutdown valve, closed on ESD-0.",
        "4. Instrument air and cooling water are shown for reference only. The",
        "    simulator holds them at supply conditions unless a malfunction is set.",
        "5. Valve fail positions: FC fail closed, FO fail open.",
    ])
    s.bottom_strip(STANDARD_LEGEND)
    return s


# ===========================================================================
# U700  steam boiler B1
# ===========================================================================
@sheet_for("U700")
def build_u700() -> Sheet:
    """Sheet 1: the boiler itself, its firing and the steam it makes."""
    s = Sheet("AP-PID-U700", "Steam boiler, firing and drum", "U700",
              sheet="1 of 2", rev="A")
    s.frame()

    b = s.place("steam_boiler", 620, 560, height=340)
    s.text(300, 700, "B1", 15, True, anchor="end")
    s.text(300, 714, "Package steam boiler", 10, False, MUTED, anchor="end")

    # ------------------------------------------------------------- flue gas
    st = s.place("stack", 1000, 220, height=160)
    s.pipe([(b.ax, b.y0), (b.ax, 300), (st.ax, 300)], kind="utility")
    s.bubble(700, 220, "PT-7003", "dcs", leader=(700, 300))
    s.text(700, 182, "furnace draft", 8.4, False, MUTED)
    s.bubble(800, 220, "TT-7003", "dcs", leader=(800, 300))
    s.bubble(880, 220, "AT-7001", "dcs", leader=(880, 300))
    s.bubble(1140, 220, "AT-7002", "dcs", leader=(st.x1, 220))

    # --------------------------------------------------------- steam to users
    s.pipe([b.right_at(520), (860, 520), (860, 420), (1140, 420)],
           number='8"-S-7001-CS', number_at=0.16)
    s.bubble(960, 340, "FT-7001", "dcs", leader=(960, 420))
    s.bubble(1060, 340, "TT-7001", "dcs", leader=(1060, 420))
    s.bubble(940, 520, "PT-7002", "dcs", leader=(864, 520))
    s.connector(1140, 420, "TO STEAM DISTRIBUTION", "AP-PID-U700 SH 2", "right")

    # ------------------------------------------------------ drum and feedwater
    s.connector(58, 460, "BFW FROM P-701A/B", "AP-PID-U700 SH 2", "right")
    fcv1 = s.valve("valve_control_diaphragm", 300, 460, "FCV-7001", width=52,
                   label="above", fail="FO")
    s.pipe([(190, 460), fcv1.left])
    s.pipe([fcv1.right, b.left_at(460)], arrow_at=0.8)
    s.bubble(400, 370, "FT-7002", "dcs", leader=(400, 460))

    s.bubble(300, 560, "PT-7001", "dcs", leader=b.left_at(560))
    s.bubble(300, 640, "PSHH-7001", "dcs", r=24, leader=b.left_at(620))
    s.bubble(830, 600, "LT-7001", "dcs", leader=b.right_at(600))
    s.bubble(830, 680, "LSHH-7001", "dcs", r=24, leader=b.right_at(660))
    s.bubble(830, 760, "LSLL-7001", "dcs", r=24, leader=b.right_at(700))

    # ---------------------------------------------------------- fuel and air
    s.connector(58, 840, "FUEL GAS FROM U010", "AP-PID-U010", "right")
    fcv2 = s.valve("valve_control_diaphragm", 280, 840, "FCV-7002", width=52,
                   label="below", fail="FC")
    s.pipe([(190, 840), fcv2.left])
    s.pipe([fcv2.right, (560, 840), (560, b.y1)], arrow_at=0.9)
    s.bubble(400, 760, "FT-7003", "dcs", leader=(400, 840))
    s.bubble(1000, 800, "BS-7001", "dcs", r=24, leader=b.right_at(710))

    s.connector(58, 980, "COMBUSTION AIR", "AP-PID-U000", "right")
    fd = s.place("fan", 300, 980, height=60, on_axis=True)
    s.text(300, fd.y0 - 22, "FD-701", 13, True)
    s.text(300, fd.y0 - 8, "Forced draft fan", 9.5, False, MUTED)
    s.pipe([(190, 980), fd.left], kind="utility")
    fcv3 = s.valve("valve_control_diaphragm", 460, 980, "FCV-7003", width=50,
                   label="above", fail="FO")
    s.pipe([fd.right, fcv3.left], kind="utility")
    s.pipe([fcv3.right, (660, 980), (660, b.y1)], kind="utility", arrow_at=0.9)
    s.bubble(580, 900, "FT-7004", "dcs", leader=(580, 980))

    # -------------------------------------------------- continuous blowdown
    s.pipe([(720, b.y1), (720, 900), (820, 900)], kind="utility")
    fcv4 = s.valve("valve_control_diaphragm", 880, 900, "FCV-7004", width=50,
                   label="above", fail="FC")
    s.pipe([fcv4.right, (960, 900)], kind="utility", arrow_at=0.6)
    s.connector(960, 900, "TO BLOWDOWN DRUM", "AP-PID-U800", "right")
    s.bubble(790, 975, "CT-7001", "dcs", leader=(790, 904))

    # --------------------------------------------------- schedules on sheet
    s.text(1240, 620, "BOILER MANAGEMENT AND DRIVES", 10, True, anchor="start")
    s.seg(1240, 628, 1646, 628, INK, 1.0)
    for i, (tag, desc) in enumerate([
            ("BS-7001", "main flame detected"),
            ("XS-7010", "furnace purge complete permissive"),
            ("XY-7010", "burner igniter energise"),
            ("XY-7011", "furnace purge sequence start"),
            ("SC-7001", "FD-701 VFD speed reference"),
            ("SC-7002", "P-701A VFD speed reference"),
            ("IT-7003", "FD-701 motor current"),
            ("ZT-7001", "FCV-7001 position feedback"),
            ("LSLL-7001", "drum level low low, trips the burner"),
            ("PSHH-7001", "drum pressure high high, trips the burner")]):
        y = 648 + i * 17
        s.text(1240, y, tag, 8.8, True, anchor="start")
        s.text(1400, y, desc, 8.2, False, MUTED, anchor="start")

    s.notes([
        "1. Drum level is three element control: level trimmed by steam flow and",
        "    feedwater flow. All three blocks are configured in the DCS.",
        "2. FCV-7001 fails open. A boiler is safer flooded than dry.",
        "3. The burner trips on LSLL-7001, PSHH-7001 and loss of flame BS-7001,",
        "    and cannot relight until XS-7010 purge complete is proved.",
        "4. Deaerator, boiler feed pumps, desuperheater and letdown are on sheet 2.",
        "5. Valve fail positions: FC fail closed, FO fail open.",
    ])
    s.bottom_strip(STANDARD_LEGEND)
    return s


def build_u700_water() -> Sheet:
    """Sheet 2: deaerator, feed pumps, desuperheater and header letdown."""
    s = Sheet("AP-PID-U700", "Deaerator, feedwater and steam distribution",
              "U700", sheet="2 of 2", rev="A")
    s.frame()

    da = s.place("tank_domed", 300, 340, height=200)
    s.text(da.x1 + 14, 380, "DA-701", 15, True, anchor="start")
    s.text(da.x1 + 14, 394, "Deaerator", 10, False, MUTED, anchor="start")

    s.connector(620, 140, "DEMIN WATER", "AP-PID-U000", "left")
    lcv = s.valve("valve_control_diaphragm", 520, 140, "LCV-7001", width=52,
                  label="above", fail="FC")
    s.pipe([(620, 140), lcv.right], kind="utility")
    s.pipe([lcv.left, (da.ax, 140), (da.ax, da.y0)], kind="utility",
           arrow_at=0.9)

    s.bubble(140, 290, "PT-7004", "dcs", leader=da.left_at(290))
    s.bubble(140, 370, "LT-7002", "dcs", leader=da.left_at(370))
    s.bubble(140, 460, "TT-7002", "dcs", leader=da.left_at(420))

    # --------------------------------------------------------- feed pumps
    mova = s.valve("valve_motor_operated", 450, 600, "MOV-7001A", width=56,
                   label="above")
    movb = s.valve("valve_motor_operated", 450, 720, "MOV-7001B", width=56,
                   label="below")
    s.pipe([(da.ax, da.y1), (da.ax, 600), mova.left],
           number='4"-BFW-7002-CS', number_at=0.3)
    s.pipe([(da.ax, 600), (da.ax, 720), movb.left])

    pa = s.place("centrifugal_pump_1", 700, 600, height=56, on_axis=True)
    s.text(700, pa.y0 - 14, "P-701A", 12, True)
    pb = s.place("centrifugal_pump_1", 700, 720, height=56, on_axis=True)
    s.text(700, pb.y1 + 18, "P-701B", 12, True)
    s.pipe([mova.right, pa.left], arrow_at=0.62)
    s.pipe([movb.right, pb.left], arrow_at=0.62)

    s.pipe([pa.top, (pa.ax, 550), (880, 550)])
    s.pipe([pb.top, (pb.ax, 670), (880, 670)])
    s.pipe([(880, 550), (880, 670)])
    s.pipe([(880, 550), (880, 420), (1000, 420)], arrow_at=0.8)
    s.connector(1000, 420, "BFW TO B1 DRUM", "AP-PID-U700 SH 1", "right")

    # ---------------------------------------------------- steam distribution
    s.connector(940, 620, "MP STEAM FROM B1", "AP-PID-U700 SH 1", "right")
    s.pipe([(1072, 620), (1500, 620)], arrow_at=0.9, number='8"-S-7001-CS',
           number_at=0.2)
    s.connector(1500, 620, "TO T1 AND T2 REBOILERS", "AP-PID-U500", "right")

    # desuperheater spray, taken from the running feed pump
    s.pipe([(880, 670), (880, 800), (1074, 800)], kind="utility")
    tcv = s.valve("valve_control_diaphragm", 1140, 800, "TCV-7001", width=52,
                  label="below", fail="FC")
    s.pipe([tcv.right, (1220, 800), (1220, 624)], kind="utility", arrow_at=0.9)
    s.text(1000, 770, "DESUPERHEATER SPRAY", 8.6, True, anchor="start")

    pcv = s.valve("valve_control_diaphragm", 1420, 900, "PCV-7001", width=52,
                  label="above", fail="FC")
    s.pipe([(1360, 620), (1360, 900), pcv.left])
    s.pipe([pcv.right, (1500, 900)], arrow_at=0.6)
    s.connector(1500, 900, "TO LP HEADER", "AP-PID-U000", "right")
    s.text(1340, 960, "HEADER LETDOWN", 8.6, True)

    s.notes([
        "1. The deaerator strips oxygen with steam. Losing its level or pressure",
        "    puts oxygenated water into the boiler, which the simulator models as",
        "    a slow rise in drum conductivity rather than an immediate trip.",
        "2. One feed pump runs. MOV-7001A and B give the start permissive;",
        "    IT-7001 and IT-7002 read their motor currents.",
        "2a. ZSO/ZSC-HV7001 report the blowdown manual valve position.",
        "3. TCV-7001 sprays feedwater into the superheated steam to hold TT-7001.",
        "    It fails closed, so a failure makes the steam hotter, not wetter.",
        "4. Boiler, firing and drum instruments are on sheet 1.",
        "5. Valve fail positions: FC fail closed, FO fail open.",
    ])
    s.bottom_strip(STANDARD_LEGEND)
    return s


BUILDERS["U700B"] = build_u700_water


# ===========================================================================
# U800  effluent neutralisation
# ===========================================================================
@sheet_for("U800")
def build_u800() -> Sheet:
    s = Sheet("AP-PID-U800", "Effluent neutralisation and discharge", "U800",
              sheet="1 of 1", rev="A")
    s.frame()

    nt = s.place("tank", 500, 520, height=280)
    s.text(nt.x0 - 14, 560, "NT-801", 15, True, anchor="end")
    s.text(nt.x0 - 14, 574, "Neutralisation tank", 10, False, MUTED,
           anchor="end")

    # agitator, drawn rather than taken from the library
    s.circle(500, 320, 20, PAPER, INK, 1.4)
    s.text(500, 325, "M", 13, True)
    s.seg(500, 340, 500, 470, INK, 1.6)
    s.seg(478, 470, 522, 470, INK, 1.6)
    s.text(470, 316, "M-801", 12, True, anchor="end")
    s.text(470, 330, "Agitator", 9, False, MUTED, anchor="end")

    # ---------------------------------------------------------- effluent in
    s.connector(58, 420, "EFFLUENT FROM PROCESS UNITS", "AP-PID-U400", "right")
    s.pipe([(190, 420), (nt.x0, 420)], arrow_at=0.74,
           number='6"-E-8001-CS')
    s.bubble(250, 320, "FT-8001", "dcs", leader=(250, 420))
    s.bubble(330, 320, "TT-8001", "dcs", leader=(330, 420))
    s.bubble(250, 620, "AT-8001", "dcs", leader=(250, 424))
    s.text(250, 658, "inlet pH", 8.4, False, MUTED)
    s.bubble(340, 720, "LT-8001", "dcs", leader=(nt.x0, 660))

    # ------------------------------------------------------------- reagents
    # Caustic is split range: the small valve trims, the large one catches a
    # step change. pH is the reason: the curve is almost vertical at neutral.
    s.connector(1000, 200, "CAUSTIC", "AP-PID-U000", "left")
    hv = s.valve("valve_gate", 900, 200, "HV-8001", width=50, label="above")
    s.pipe([(1000, 200), hv.right], kind="utility")
    s.pipe([hv.left, (820, 200), (820, 320)], kind="utility")

    fcvf = s.valve("valve_control_diaphragm", 740, 200, "FCV-8002", width=50,
                   label="below", fail="FC")
    s.pipe([(820, 200), fcvf.right], kind="utility")
    fcvc = s.valve("valve_control_diaphragm", 740, 320, "FCV-8001", width=50,
                   label="below", fail="FC")
    s.pipe([(820, 320), fcvc.right], kind="utility")
    s.pipe([fcvf.left, (640, 200), (640, 320)], kind="utility")
    s.pipe([fcvc.left, (640, 320)], kind="utility")
    s.pipe([(640, 260), (530, 260), (530, nt.y0)], kind="utility",
           arrow_at=0.9)
    s.text(660, 168, "SPLIT RANGE, SEE NOTE 2", 8.4, True, anchor="start")

    s.connector(1000, 460, "ACID", "AP-PID-U000", "left")
    fcva = s.valve("valve_control_diaphragm", 880, 460, "FCV-8003", width=50,
                   label="above", fail="FC")
    s.pipe([(1000, 460), fcva.right], kind="utility")
    s.pipe([fcva.left, (700, 460), (700, 420), (nt.x1, 420)], kind="utility",
           arrow_at=0.9)

    # ------------------------------------------------------- discharge pumps
    mova = s.valve("valve_motor_operated", 670, 820, "MOV-8001A", width=56,
                   label="above")
    movb = s.valve("valve_motor_operated", 670, 940, "MOV-8001B", width=56,
                   label="below")
    s.pipe([(500, nt.y1), (500, 820), mova.left],
           number='6"-E-8002-CS', number_at=0.3)
    s.pipe([(500, 820), (500, 940), movb.left])

    pa = s.place("centrifugal_pump_1", 900, 820, height=56, on_axis=True)
    s.text(900, pa.y0 - 14, "P-801A", 12, True)
    pb = s.place("centrifugal_pump_1", 900, 940, height=56, on_axis=True)
    s.text(pb.x1 + 14, 946, "P-801B", 12, True, anchor="start")
    s.pipe([mova.right, pa.left], arrow_at=0.62)
    s.pipe([movb.right, pb.left], arrow_at=0.62)

    s.pipe([pa.top, (pa.ax, 770), (1080, 770)])
    s.pipe([pb.top, (pb.ax, 890), (1080, 890)])
    s.pipe([(1080, 770), (1080, 890)])
    s.pipe([(1080, 770), (1080, 640), (1214, 640)])
    fcvd = s.valve("valve_control_diaphragm", 1280, 640, "FCV-8004", width=52,
                   label="above", fail="FC")
    s.pipe([fcvd.right, (1360, 640)], arrow_at=0.6)
    s.connector(1360, 640, "TO OUTFALL", "AP-PID-U000", "right")
    s.bubble(1140, 540, "AT-8002", "dcs", leader=(1140, 640))
    s.text(1140, 502, "outlet pH", 8.4, False, MUTED)
    s.bubble(1240, 460, "AT-8003", "dcs", leader=(1240, 640))
    s.text(1240, 422, "outlet COD", 8.4, False, MUTED)

    # --------------------------------------------------- schedules on sheet
    s.text(1240, 760, "MOTOR CONTROL AND ALARMS", 10, True, anchor="start")
    s.seg(1240, 768, 1646, 768, INK, 1.0)
    for i, (tag, desc) in enumerate([
            ("MC-M801", "agitator start / stop, run, fault, available"),
            ("IT-8001", "M-801 agitator motor current"),
            ("MC-P801A", "discharge pump A start / stop"),
            ("MC-P801B", "discharge pump B start / stop"),
            ("IT-8002", "P-801A motor current"),
            ("IT-8003", "P-801B motor current"),
            ("ZSO/ZSC-HV8001", "caustic isolation position switches"),
            ("XC-MOV8001A", "open / close, both limits, torque"),
            ("XC-MOV8001B", "open / close, both limits, torque"),
            ("QY-8001", "linearised pH signal for control"),
            ("ASHH-8001", "pH outside discharge consent, closes FCV-8004")]):
        y = 788 + i * 17
        s.text(1240, y, tag, 8.8, True, anchor="start")
        s.text(1400, y, desc, 8.2, False, MUTED, anchor="start")

    s.notes([
        "1. pH is logarithmic, so the process gain changes by orders of magnitude",
        "    across the range. QY-8001 linearises the measurement before control.",
        "2. FCV-8002 takes 0 to 50 percent of the reagent output and FCV-8001 the",
        "    remainder, so the small valve trims and the large one catches upsets.",
        "3. ASHH-8001 closes FCV-8004 on a consent breach. Effluent then backs up",
        "    into NT-801 rather than leaving site.",
        "4. Stopping M-801 does not stop the reaction; it makes the tank behave as",
        "    plug flow and the outlet pH swing.",
        "5. Valve fail positions: FC fail closed, FO fail open.",
    ])
    s.bottom_strip(STANDARD_LEGEND)
    return s


# ===========================================================================
# U900  safety instrumented system
# ===========================================================================
ESD_CAUSES = [
    ("HS-9002", "Control room ESD pushbutton, level 0", "XXXXX"),
    ("HS-9001", "Field ESD pushbutton, level 1", ".XXX."),
    ("FD-9001", "Flame detected, zone 1", "XXXXX"),
    ("FD-9002", "Flame detected, zone 2", "XXXXX"),
    ("GD-9001", "Gas detected, reactor area, 20 percent LEL", ".X..X"),
    ("GD-9002", "Gas detected, compressor house, 20 percent LEL", ".X..."),
    ("GD-9003", "Gas detected, fired equipment, 20 percent LEL", "...X."),
    ("PSHH-4001", "R1 pressure high high", ".X..X"),
    ("TSHH-4001", "R1 catalyst bed temperature high high", ".X..X"),
    ("VSHH-2001", "C1 vibration high high", ".X..."),
    ("PSL-2001", "C1 lube oil pressure low", ".X..."),
    ("PSLL-3001", "H1 fuel gas pressure low low", ".X..."),
    ("PSHH-3001", "H1 fuel gas pressure high high", ".X..."),
    ("LSLL-7001", "B1 steam drum level low low", "...X."),
    ("PSHH-7001", "B1 steam drum pressure high high", "...X."),
    ("PSHH-5001", "T1 overhead pressure high high", "..X.."),
    ("PSHH-6001", "T2 overhead pressure high high", "..X.."),
]

ESD_EFFECTS = [
    ("XY-9001", "ESD-0", "Total plant"),
    ("XY-9002", "ESD-1", "U200 U300 U400"),
    ("XY-9003", "ESD-1", "U500 U600"),
    ("XY-9004", "ESD-1", "U700"),
    ("XY-9005", "BDV", "R1 depressuring"),
]


@sheet_for("U900")
def build_u900() -> Sheet:
    """Cause and effect, which is what a safety system drawing actually is."""
    s = Sheet("AP-PID-U900", "Safety instrumented system, cause and effect",
              "U900", sheet="1 of 1", rev="A")
    s.frame()

    # ------------------------------------------------------ architecture strip
    s.text(80, 110, "FIELD INITIATORS", 9.5, True, anchor="start")
    s.rect(80, 124, 300, 96, PAPER, INK, 1.2)
    for i, txt in enumerate(["Pushbuttons  HS-9001, HS-9002",
                             "Flame  FD-9001, FD-9002",
                             "Gas  GD-9001, GD-9002, GD-9003",
                             "Process trips, see the unit sheets"]):
        s.text(94, 146 + i * 20, txt, 8.6, False, INK, anchor="start")

    s.rect(470, 124, 260, 96, PAPER, INK, 1.6)
    s.text(600, 158, "SIS LOGIC SOLVER", 10, True)
    s.text(600, 176, "Separate from the DCS", 8.6, False, MUTED)
    s.text(600, 196, "Two out of three voting on gas", 8.6, False, MUTED)
    s.pipe([(380, 172), (470, 172)], kind="utility", arrow_at=0.7)

    s.text(820, 110, "FINAL ELEMENTS", 9.5, True, anchor="start")
    s.rect(820, 124, 320, 96, PAPER, INK, 1.2)
    for i, txt in enumerate(["Shut off valves  XV-1001 to XV-7001",
                             "Blowdown  BDV-4001",
                             "Motors  C1, P-101A/B, pumps and fans",
                             "Burners  H1 and B1 fuel isolation"]):
        s.text(834, 146 + i * 20, txt, 8.6, False, INK, anchor="start")
    s.pipe([(730, 172), (820, 172)], kind="utility", arrow_at=0.7)

    s.bubble(1300, 150, "XI-9001", "dcs", r=24)
    s.text(1340, 146, "active ESD effects", 8.6, False, MUTED, anchor="start")
    s.bubble(1300, 220, "XY-9006", "dcs", r=24)
    s.text(1340, 216, "healthy and first out reset", 8.6, False, MUTED,
           anchor="start")
    s.bubble(1300, 290, "XS-9002", "dcs", r=24)
    s.text(1340, 286, "reset request from field", 8.6, False, MUTED,
           anchor="start")
    s.bubble(1300, 360, "XS-9001", "dcs", r=24)
    s.text(1340, 356, "fire water pump running", 8.6, False, MUTED,
           anchor="start")

    # ----------------------------------------------------------- the matrix
    x0, y0 = 80, 420
    w_tag, w_desc, w_col = 110.0, 420.0, 96.0
    x_first = x0 + w_tag + w_desc
    n_rows = len(ESD_CAUSES)
    row_h = 26.0
    head_h = 64.0
    table_w = w_tag + w_desc + w_col * len(ESD_EFFECTS)

    s.text(x0, y0 - 14, "CAUSE AND EFFECT", 11, True, anchor="start")
    s.rect(x0, y0, table_w, head_h + n_rows * row_h, PAPER, INK, 1.4)
    s.seg(x0, y0 + head_h, x0 + table_w, y0 + head_h, INK, 1.4)
    s.text(x0 + 10, y0 + 38, "CAUSE", 9.5, True, anchor="start")
    s.text(x0 + w_tag + 10, y0 + 38, "DESCRIPTION", 9.5, True, anchor="start")

    for j, (tag, level, desc) in enumerate(ESD_EFFECTS):
        cx = x_first + w_col * j + w_col / 2.0
        s.text(cx, y0 + 22, tag, 9, True)
        s.text(cx, y0 + 36, level, 8.6, True, MUTED)
        s.text(cx, y0 + 52, desc, 7.4, False, MUTED)
        s.seg(x_first + w_col * j, y0, x_first + w_col * j,
              y0 + head_h + n_rows * row_h, INK, 0.9)
    s.seg(x0 + w_tag, y0, x0 + w_tag, y0 + head_h + n_rows * row_h, INK, 0.9)
    s.seg(x_first, y0, x_first, y0 + head_h + n_rows * row_h, INK, 1.2)

    for i, (tag, desc, marks) in enumerate(ESD_CAUSES):
        ry = y0 + head_h + row_h * i
        if i:
            s.seg(x0, ry, x0 + table_w, ry, INK, 0.7)
        s.text(x0 + 10, ry + 17, tag, 8.8, True, anchor="start")
        s.text(x0 + w_tag + 10, ry + 17, desc, 8.4, False, INK, anchor="start")
        for j, m in enumerate(marks):
            if m == "X":
                cx = x_first + w_col * j + w_col / 2.0
                s.text(cx, ry + 18, "X", 11, True)

    s.notes([
        "1. This sheet is the safeguarding drawing. It carries no piping; the",
        "    valves and machines it acts on are shown on the unit sheets.",
        "2. ESD-0 shuts the whole plant down. ESD-1 shuts down the named units",
        "    and leaves the rest running, which is what makes recovery possible.",
        "3. Gas detection votes two out of three within a zone before acting.",
        "4. A trip is latched. XS-9002 requests a reset and XY-9006 proves the",
        "    system healthy and shows the first cause that acted.",
        "5. The simulator supplies the initiators and obeys the outputs. The",
        "    voting and latching are configured in the safety system, not here.",
    ])
    s.bottom_strip(STANDARD_LEGEND)
    return s


# ===========================================================================
def render_png(svg_path: Path) -> Path:
    from PySide6.QtCore import QRectF
    from PySide6.QtGui import QImage, QPainter
    from PySide6.QtSvg import QSvgRenderer
    from PySide6.QtWidgets import QApplication

    QApplication.instance() or QApplication([])
    r = QSvgRenderer(str(svg_path))
    if not r.isValid():
        raise SystemExit(f"{svg_path.name} is not valid SVG")
    size = r.defaultSize()
    img = QImage(size.width(), size.height(), QImage.Format_RGB32)
    img.fill(0xFFFFFFFF)
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing, True)
    p.setRenderHint(QPainter.TextAntialiasing, True)
    r.render(p, QRectF(0, 0, size.width(), size.height()))
    p.end()
    png = svg_path.with_suffix(".png")
    img.save(str(png))
    return png


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Generate the P&ID set")
    ap.add_argument("units", nargs="*", help="unit codes, default all")
    ap.add_argument("--png", action="store_true", help="also rasterise")
    ap.add_argument("--out", default=str(OUT))
    args = ap.parse_args(argv)

    out = Path(args.out)
    codes = [u.upper() for u in args.units] or list(BUILDERS)
    for code in codes:
        if code not in BUILDERS:
            print(f"  no sheet defined for {code}")
            continue
        sheet = BUILDERS[code]()
        path = sheet.write(out / f"AP-PID-{code}.svg")
        print(f"  {path.name}  ({sheet.w} x {sheet.h})")
        if args.png:
            print(f"    -> {render_png(path).name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
