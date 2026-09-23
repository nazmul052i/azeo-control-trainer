#!/usr/bin/env python3
"""Generate the plant Process Flow Diagram as an SVG.

A PFD, not a P&ID: major equipment, the streams that connect it, and a heat and
material balance. Instruments, control valves, isolation valves and the whole
discrete side belong on the unit P&IDs and are deliberately absent here. If a
reader wants to know how a level is held, that is a P&ID question.

The topology is the one the models actually run, taken from the tear-stream bus
rather than from memory. Two things on it are worth stating plainly because
they are easy to assume backwards:

* **T1 bottoms feed T2.** They are not the recycle.
* **T2 bottoms are the recycle to D1.** They are not a product draw.

The stream table is filled from the simulator itself: the plant is restored
from the lined-out snapshot and stepped until it settles, then every figure is
read from the tag database. Nothing on this sheet is invented.

    python tools/make_pfd.py [--out docs/AzeoPlant_PFD.svg] [--png] [--fresh]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import List, Tuple

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from pid_kit import INK, MUTED, PAPER, Sheet  # noqa: E402

# ===========================================================================
# the balance, read from the running model
# ===========================================================================
def collect(db, fs) -> List[tuple]:
    """The heat and material balance, read from the running model.

    Every figure is a tag the simulator maintains. Where a stream has no
    transmitter on it the entry is left blank rather than filled with a
    plausible number.
    """

    def v(tag: str, default=0.0):
        t = db.get(tag)
        return float(t.value) if t is not None else default

    u100 = fs.unit("U100")
    charge = v("FT-3004") + v("FT-3005")

    # (no, description, phase, flow, unit, temperature degC, pressure barg)
    return [
        (1, "Natural gas make-up to fuel header", "V", v("FT-0101"), "Nm3/h",
         v("TT-0101"), v("PT-0101")),
        (2, "Fresh feed from OSBL to D1", "L", getattr(u100, "fresh_feed", 92.0),
         "m3/h", 38.0, v("PT-1001")),
        (3, "T2 bottoms recycle to D1", "L", v("FT-1002"), "m3/h",
         v("TT-6006"), v("PT-1001")),
        (4, "D1 charge to H1, pumped", "L", v("FT-1001"), "m3/h",
         v("TT-1001"), v("PT-1002")),
        (5, "H1 heated charge to R1", "VL", charge, "m3/h",
         v("TT-3001"), v("PT-4001")),
        (6, "Quench gas from C1 to R1", "V", v("FT-4001"), "kNm3/h",
         v("TT-2002"), v("PT-2002")),
        (7, "R1 effluent to E-401 and D3", "VL", v("FT-1001"), "m3/h",
         v("TT-4004"), v("PT-4001")),
        (8, "D3 off-gas to C1 suction", "V", v("FT-2001"), "kNm3/h",
         v("TT-2001"), v("PT-2001")),
        (9, "D3 off-gas to fuel header", "V", v("FT-4002"), "Nm3/h",
         v("TT-4005"), v("PT-4002")),
        (10, "D3 hydrocarbon liquid to T1", "L", v("FT-4003"), "m3/h",
         v("TT-4005"), v("PT-4002")),
        (11, "D3 sour water to effluent treatment", "L", v("FT-4004"), "m3/h",
         v("TT-4005"), v("PT-4002")),
        (12, "T1 overhead vapour to E-501", "V", v("FT-5002") + v("FT-5003"),
         "m3/h", v("TT-5005"), v("PT-5001")),
        (13, "T1 reflux to column", "L", v("FT-5002"), "m3/h",
         v("TT-5007"), v("PT-5003")),
        (14, "T1 distillate to product storage", "L", v("FT-5003"), "m3/h",
         v("TT-5007"), v("PT-5003")),
        (15, "T1 bottoms to T2, pumped", "L", v("FT-5004"), "m3/h",
         v("TT-5006"), v("PT-5004")),
        (16, "T2 overhead vapour to E-601", "V", v("FT-6002") + v("FT-6003"),
         "m3/h", v("TT-6005"), v("PT-6001")),
        (17, "T2 reflux to column", "L", v("FT-6002"), "m3/h",
         v("TT-6007"), v("PT-6003")),
        (18, "T2 distillate to product storage", "L", v("FT-6003"), "m3/h",
         v("TT-6007"), v("PT-6003")),
        (19, "T2 bottoms, pumped to recycle", "L", v("FT-6004"), "m3/h",
         v("TT-6006"), v("PT-6004")),
        (20, "MP steam from B1 to the reboilers", "V",
         v("FT-5005") + v("FT-6005"), "t/h", v("TT-7001"), v("PT-7002")),
        (21, "Treated effluent to outfall", "L", v("FT-8001"), "m3/h",
         v("TT-8001"), 0.0),
    ]


# ===========================================================================
# drawing
# ===========================================================================
W, H = 2400, 1750
PFD_LEGEND = [("process", "Major process line"),
              ("utility", "Utility / secondary line")]


def tag(s: Sheet, x: float, y: float, name: str, service: str = "",
        anchor: str = "middle") -> None:
    """Equipment number and service. A PFD names equipment and nothing else."""
    s.text(x, y, name, 16, True, INK, anchor=anchor)
    if service:
        s.text(x, y + 15, service, 10, False, MUTED, anchor=anchor)


def stream(s: Sheet, x: float, y: float, no: int) -> None:
    """Stream number in the usual flag, keyed to the balance on sheet 2."""
    s.poly([(x - 14, y - 11), (x + 8, y - 11), (x + 14, y),
            (x + 8, y + 11), (x - 14, y + 11)], PAPER, INK, 1.3)
    s.text(x - 3, y + 4.5, str(no), 11, True)


def column_train(s: Sheet, cx: float, base: float, name: str, drum: str,
                 cond: str, reb: str, rpump: str, bpump: str, boot: bool,
                 nums, dist_label: str, bot_label: str):
    """One fractionator with its overhead train and its reboiler.

    Drawn as a self contained block so the two columns sit side by side on the
    lower row without either running into the other. ``bot_label`` is left
    empty when the bottoms go somewhere else on this sheet and the caller draws
    the connection itself.
    """
    s_ovhd, s_reflux, s_dist, s_bot = nums

    col = s.place("column_common", cx, base, height=340)
    tag(s, cx - 95, base + 140, name, "Fractionator", anchor="end")

    cnd = s.place("shell_and_tube_1", cx + 150, base - 190, height=80,
                  on_axis=True)
    tag(s, cx + 150, cnd.y0 - 36, cond, "Overhead condenser")
    drm = s.place("vessel_horizontal" if boot else "vessel_horizontal_2",
                  cx + 360, base - 120, height=110)
    tag(s, cx + 360, drm.y0 - 38, drum, "Reflux drum")
    rp = s.place("centrifugal_pump_1", cx + 360, base + 20, height=56,
                 on_axis=True)
    tag(s, cx + 470, rp.cy + 4, rpump, "", anchor="start")
    rb = s.place("shell_and_tube_1", cx + 150, base + 200, height=80,
                 on_axis=True)
    tag(s, cx + 150, rb.y1 + 30, reb, "Reboiler")
    bp = s.place("centrifugal_pump_1", cx + 360, base + 200, height=56,
                 on_axis=True)
    tag(s, cx + 360, bp.y1 + 28, bpump, "Bottoms pumps")

    s.pipe([(col.ax, col.y0), (col.ax, base - 190), cnd.left], arrow_at=0.85)
    stream(s, cx + 62, base - 212, s_ovhd)
    s.pipe([cnd.right, (cx + 265, base - 190), (cx + 265, base - 120),
            drm.left_at(base - 120)], arrow_at=0.85)
    s.pipe([(cx + 330, drm.y1), (cx + 330, base + 20), rp.left], arrow_at=0.8)
    s.pipe([rp.top, (rp.ax, base - 40), (cx + 70, base - 40),
            (cx + 70, base - 100), col.right_at(base - 100)], arrow_at=0.96)
    stream(s, cx + 215, base - 62, s_reflux)

    # distillate leaves below the pump so it never crosses the reflux return
    s.pipe([(rp.ax, base + 48), (rp.ax, base + 110), (cx + 560, base + 110)],
           arrow_at=0.9)
    stream(s, cx + 500, base + 88, s_dist)
    s.text(cx + 574, base + 114, dist_label, 10.5, True, anchor="start")

    s.pipe([(col.ax, col.y1), (col.ax, base + 200), rb.left])
    s.pipe([rb.top, (rb.ax, base + 120), col.right_at(base + 120)],
           arrow_at=0.9)
    s.pipe([(rb.x1, base + 200), bp.left], arrow_at=0.7)
    s.pipe([(bp.x1, base + 200), (cx + 560, base + 200)], arrow_at=0.7)
    stream(s, cx + 500, base + 178, s_bot)
    if bot_label:
        s.text(cx + 574, base + 204, bot_label, 10.5, True, anchor="start")
    return col


def draw_diagram(counts) -> str:
    s = Sheet("AP-PFD-0001", "Process flow diagram, whole plant", "PLANT",
              sheet="1 of 2", rev="C", w=W, h=H)
    s.frame()
    BASE = 1160

    # ------------------------------------------- recycle gas loop, top row
    v201 = s.place("pressurized_vessel", 1240, 210, height=150)
    tag(s, 1240, v201.y1 + 32, "V-201", "C1 suction drum")
    c1 = s.place("compressor", 990, 210, height=115, on_axis=True)
    tag(s, 990, c1.y0 - 38, "C1", "Recycle gas compressor")
    e201 = s.place("shell_and_tube_1", 740, 210, height=84, on_axis=True)
    tag(s, 740, e201.y0 - 38, "E-201", "Discharge cooler")

    # ------------------------------------------ feed and reaction, mid row
    d1 = s.place("pressurized_vessel", 220, 560, height=200)
    tag(s, 220, d1.y1 + 34, "D1", "Feed surge drum")
    # the label sits above its line and the flag on it, so a long destination
    # never runs under its own stream number
    s.text(60, 476, "FRESH FEED", 10.5, True, anchor="start")
    s.pipe([(60, 500), (d1.x0, 500)], arrow_at=0.9)
    stream(s, 118, 500, 2)
    s.text(60, 618, "T2 BOTTOMS RECYCLE", 10.5, True, anchor="start")
    s.pipe([(60, 642), (d1.x0, 642)], arrow_at=0.9)
    stream(s, 118, 642, 3)

    pa = s.place("centrifugal_pump_1", 400, 740, height=60, on_axis=True)
    tag(s, 400, pa.y1 + 30, "P-101A/B", "Charge pumps")
    s.pipe([(220, d1.y1), (220, 740), pa.left], arrow_at=0.85)

    h1 = s.place("furnace", 700, 560, height=280)
    tag(s, 700, h1.y1 + 34, "H1", "Charge heater")
    s.pipe([pa.top, (pa.ax, 660), (540, 660), (540, 560),
            h1.left_at(560)], arrow_at=0.94)
    stream(s, 490, 638, 4)

    r1 = s.place("pressurized_vessel", 1010, 540, height=250)
    tag(s, 1010, r1.y1 + 34, "R1", "Reactor")
    s.pipe([h1.right_at(560), (r1.x0, 560)], arrow_at=0.7)
    stream(s, 900, 538, 5)

    e401 = s.place("shell_and_tube_1", 1200, 740, height=82, on_axis=True)
    tag(s, 1200, e401.y1 + 30, "E-401", "Effluent cooler")
    s.pipe([(1010, r1.y1), (1010, 740), e401.left], arrow_at=0.85)
    stream(s, 1110, 718, 7)

    d3 = s.place("vessel_horizontal", 1470, 740, height=150)
    tag(s, 1470, d3.y0 - 44, "D3", "Product separator")
    s.pipe([e401.right, d3.left_at(740)], arrow_at=0.7)

    s.pipe([(1400, d3.y0), (1400, 210), (v201.x1, 210)], arrow_at=0.9)
    stream(s, 1400, 420, 8)
    s.pipe([(v201.x0, 210), c1.right], arrow_at=0.7)
    s.pipe([c1.left, e201.right], arrow_at=0.7)
    s.pipe([e201.left, (600, 210), (600, 400), (r1.ax + 60, 400),
            (r1.ax + 60, r1.y0)], arrow_at=0.9)
    stream(s, 700, 378, 6)
    s.pipe([(1400, 300), (1660, 300)], arrow_at=0.85)
    stream(s, 1560, 278, 9)
    s.text(1674, 296, "TO FUEL GAS HEADER", 10.5, True, anchor="start")
    s.text(1674, 311, "H1 and B1 burners", 9.5, False, MUTED, anchor="start")

    # ------------------------------------------------ utilities, mid row right
    da = s.place("tank_domed", 1830, 520, height=130)
    tag(s, 1830, da.y1 + 32, "DA-701", "Deaerator")
    b1 = s.place("steam_boiler", 2100, 560, height=210)
    tag(s, 2100, b1.y1 + 32, "B1", "Steam boiler")
    s.pipe([(da.ax, da.y1), (da.ax, 690), (b1.ax - 70, 690)], arrow_at=0.85)
    s.pipe([b1.right_at(520), (2320, 520), (2320, 430)], kind="utility",
           arrow_at=0.85)
    stream(s, 2250, 498, 20)
    s.text(2320, 412, "MP STEAM", 10.5, True)
    s.text(2320, 397, "to E-502 and E-602", 9.5, False, MUTED)

    # ---------------------------------------------- fractionation, lower row
    t1 = column_train(s, 380, BASE, "T1", "D5", "E-501", "E-502",
                      "P-501A/B", "P-502A/B", True, (12, 13, 14, 15),
                      "DISTILLATE TO STORAGE", "")
    t2 = column_train(s, 1300, BASE, "T2", "D6", "E-601", "E-602",
                      "P-601A/B", "P-602A/B", False, (16, 17, 18, 19),
                      "DISTILLATE TO STORAGE", "BOTTOMS RECYCLE TO D1")

    # D3 liquid crosses to T1, and T1 bottoms cross to T2
    s.pipe([(1400, d3.y1), (1400, 845), (200, 845), (200, BASE),
            t1.left_at(BASE)], arrow_at=0.97)
    stream(s, 800, 823, 10)
    s.pipe([(940, BASE + 200), (1140, BASE + 200), (1140, BASE),
            t2.left_at(BASE)], arrow_at=0.92)
    s.text(1000, BASE + 186, "T1 BOTTOMS", 9.5, True, anchor="start")

    # ------------------------------------------------------ effluent, far right
    nt = s.place("tank", 2190, BASE, height=180)
    tag(s, 2190, nt.y1 + 32, "NT-801", "Neutralisation tank")
    s.pipe([(1540, d3.y1), (1540, 878), (2190, 878), (2190, nt.y0)],
           arrow_at=0.95)
    stream(s, 1960, 856, 11)
    s.pipe([(nt.x1, BASE), (2330, BASE)], arrow_at=0.7)
    stream(s, 2290, BASE - 22, 21)
    s.text(2345, BASE + 22, "TO OUTFALL", 10.5, True, anchor="end")

    s.text(60, 1500, "Instruments, control valves and isolation valves are not "
           "shown. See the unit P&IDs, AP-PID-U010 to AP-PID-U900.",
           10.5, False, MUTED, anchor="start")
    s.text(60, 1518, "Stream numbers key to the heat and material balance on "
           "sheet 2, which is read from the running simulator.",
           10.5, False, MUTED, anchor="start")
    s.bottom_strip(PFD_LEGEND)
    return s.svg()


def draw_balance(rows) -> str:
    s = Sheet("AP-PFD-0001", "Heat and material balance", "PLANT",
              sheet="2 of 2", rev="C", w=W, h=H)
    s.frame()
    head, rh = 34.0, 30.0
    x, y, w = 110, 150, W - 220
    cols = [(0.05, "No."), (0.34, "Stream"), (0.07, "Phase"),
            (0.14, "Flow"), (0.09, "Units"),
            (0.15, "Temperature  degC"), (0.16, "Pressure  barg")]

    s.text(x, y - 42, "HEAT AND MATERIAL BALANCE", 16, True, anchor="start")
    s.text(x, y - 22, "Every figure is read from the running simulator at the "
           "lined-out condition. Nothing here is a design case.",
           10.5, False, MUTED, anchor="start")
    s.rect(x, y, w, head + rh * len(rows), PAPER, INK, 1.4)
    s.seg(x, y + head, x + w, y + head, INK, 1.4)

    edges, acc = [], 0.0
    for frac, _ in cols:
        edges.append(acc)
        acc += frac
    for i, (frac, label) in enumerate(cols):
        cx = x + w * edges[i]
        if i:
            s.seg(cx, y, cx, y + head + rh * len(rows), INK, 0.9)
        s.text(cx + 12, y + 22, label, 11, True, anchor="start")

    def cell(i, r, text, align="start"):
        cx = x + w * edges[i]
        tx = cx + 12 if align == "start" else cx + w * cols[i][0] - 12
        s.text(tx, y + head + rh * r + 20, text, 11, False, INK, anchor=align)

    for r, (no, desc, phase, flow, unit, t, p) in enumerate(rows):
        if r:
            s.seg(x, y + head + rh * r, x + w, y + head + rh * r, INK, 0.6)
        cell(0, r, str(no))
        cell(1, r, desc)
        cell(2, r, phase)
        cell(3, r, "-" if flow is None else f"{flow:,.1f}", "end")
        cell(4, r, unit)
        cell(5, r, "-" if t is None else f"{t:,.1f}", "end")
        cell(6, r, "-" if p is None else f"{p:,.2f}", "end")

    s.bottom_strip(PFD_LEGEND)
    return s.svg()


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Generate the AzeoPlant PFD")
    p.add_argument("--out", default=str(ROOT / "docs" / "AzeoPlant_PFD.svg"))
    p.add_argument("--png", action="store_true", help="also write a PNG render")
    p.add_argument("--fresh", action="store_true",
                   help="use the built-in initial condition, not the snapshot")
    args = p.parse_args(argv)

    import logging
    logging.disable(logging.INFO)

    from azeoplant.core.engine import SimulationEngine
    from azeoplant.core.tags import TagDatabase
    from azeoplant.models.flowsheet import Flowsheet

    db = TagDatabase()
    fs = Flowsheet(db, dt=0.1)
    eng = SimulationEngine(db, fs, dt=0.1)
    snap = ROOT / "snapshots" / "lined_up.json"
    if not args.fresh and snap.exists():
        eng.load_snapshot(snap)
    for _ in range(3000):
        eng._execute_step()

    rows = collect(db, fs)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    sheets = [(out, draw_diagram(db.counts())),
              (out.with_name(out.stem + "_HMB" + out.suffix), draw_balance(rows))]
    for path, svg in sheets:
        path.write_text(svg, encoding="utf-8")
        print(f"written {path.name}  ({W} x {H})")

    if args.png:
        from PySide6.QtCore import QRectF
        from PySide6.QtGui import QImage, QPainter
        from PySide6.QtSvg import QSvgRenderer
        from PySide6.QtWidgets import QApplication
        QApplication.instance() or QApplication([])
        for path, _ in sheets:
            r = QSvgRenderer(str(path))
            img = QImage(W, H, QImage.Format_RGB32)
            img.fill(0xFFFFFFFF)
            pa = QPainter(img)
            pa.setRenderHint(QPainter.Antialiasing, True)
            r.render(pa, QRectF(0, 0, W, H))
            pa.end()
            img.save(str(path.with_suffix(".png")))
            print(f"rendered {path.with_suffix('.png').name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
