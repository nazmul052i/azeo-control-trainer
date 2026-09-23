"""Author the plant shutdown screens using Graphics Designer's retained documents."""
from pathlib import Path

from plant_safe_landing import CLASS, MONITOR, WORKFLOW, route_index, stages
from azeo_control_trainer.core.hmi.pvms.procedure_blueprint import create_blueprint, configuration
from azeo_control_trainer.core.hmi.pvms.publishing import PvmDisplay
from azeo_control_trainer.core.hmi.pvms.user_library import UserPvmLibrary


def text(key, label, x, y, w, h=28, size=11):
    return dict(kind="text", id=key, text=label, x=x, y=y, w=w, h=h,
                font_size=size, text_wrap=True, text_valign="middle")


def link(key, label, target, x, y, w=220):
    return dict(kind="display_link", id=key, text=label, target=target,
                x=x, y=y, w=w, h=34)


def readout(key, path, x, y, w=110, dtype="numeric"):
    return dict(kind="datalink", id=key, path=path, datalink_type=dtype,
                x=x, y=y, w=w, h=30, decimals=1)


def control_name(stage):
    return "Plant - L3 Manual " + stage.key.title()


def customize_procedure_classes(library):
    fp = library.entries[CLASS]["items"]
    next(item for item in fp if item.get("id") == "abort")["entry"]["label"] = "End guide"
    # Keep the manual-control route outside state-dependent PA commands.
    next(item for item in fp if item.get("id") == "surface")["h"] = 608
    fp.append(link("manual", "Manual shutdown controls", WORKFLOW, 12, 568, 416))
    detail = library.entries[CLASS + "_Detail"]["items"]
    detail[:] = [item for item in detail if item.get("id") != "equipment_detail"]
    detail.append(link("manual_shutdown", "Manual shutdown controls", WORKFLOW, 628, 474, 376))
    for name, body in ((CLASS, fp), (CLASS + "_Detail", detail)):
        entry = library.entries[name]
        library.add(name, body, folder=entry["folder"], definition_kind=entry["definition_kind"])


def author(project: Path, root: Path, ref: str):
    routes = route_index(project)
    library = UserPvmLibrary(root)
    names = create_blueprint(library, CLASS)
    for name in names:
        configuration(name).save(root / "_pvmcfg")
    customize_procedure_classes(library)
    choices = {"ProcedureRef": ref, "Title": "SAFE LANDING"}

    def instance(x, y):
        return library.instantiate(CLASS + "_PVM", x, y,
                                   config=configuration(CLASS + "_PVM"), choices=choices)

    items = [text("title", "PLANT  /  Shutdown monitoring", 24, 18, 1140, 42, 20),
             text("subtitle", "Measured levels and recent trends · operator-guided safe landing", 24, 65, 1080),
             text("scope", "Monitored hot, pressurized hold — retain cooling, lube and protection. Not a maintenance release.", 24, 112, 1512, 42),
             link("workflow", "Safe Landing / manual controls", WORKFLOW, 24, 864, 320),
             link("overview", "Plant overview", "Overview - L1 Plant", 378, 864, 260)]
    items += instance(1290, 12)
    pvms = []
    vessels = (
        ("D1", "Feed inventory", "LT-1001", ("PT-1002", "FT-1001", "FT-1004"), "U100 - L2 Feed Preparation"),
        ("D3", "Reactor separator", "LT-4001", ("PT-4001", "TT-4002", "TT-4003"), "U400 - L2 Reactor Section"),
        ("V501", "T1 reflux drum", "LT-5001", ("FT-5005", "FT-5006"), "U500 - L2 Fractionation"),
        ("V601", "T2 reflux drum", "LT-6001", ("FT-6005", "FT-6006"), "U600 - L2 Product Recovery"),
    )
    # These cards describe separate inventories, so there are no invented
    # pipes implying a direct hydraulic connection between unrelated vessels.
    for index, (tag, title, level, readings, unit_display) in enumerate(vessels):
        x = 24 + index * 384
        items += [dict(kind="rect", id=tag+"_panel", x=x, y=177, w=366, h=540,
                       fill_role="SURFACE_PANEL", width=1),
                  text(tag+"_title", tag+" / "+title, x+12, 193, 342, 38, 13)]
        pvms.append(dict(id=tag+"_vessel", **{"class": "AI/dynamo_inline"}, variant="vessel",
                         params={"path": routes[level][0]}, label=tag, x=x+10, y=270, w=188, h=328,
                         choices={"equipment_symbol": "vessel", "show_hp_anatomy": False}))
        for row, signal in enumerate((level, *readings)):
            path, kind = routes[signal]
            assert kind == "AI", signal
            pvms.append(dict(id=signal, **{"class": "AI/dynamo_compact"},
                             params={"path": path}, label=signal,
                             x=x+210, y=276+row*91, w=143, h=72))
        # Link only to installed project unit displays.
        candidates = sorted(root.glob(unit_display.split(" - L2 ")[0] + " - L2*"))
        if candidates:
            items.append(link(tag+"_unit", "Open unit display", candidates[0].name, x+12, 658, 340))
    items += [text("boiler", "B1 / Drum inventory and retained energy", 24, 737, 480, 35, 13)]
    for index, signal in enumerate(("LT-7001", "PT-7001", "TT-3005", "FT-3001", "FT-7003")):
        pvms.append(dict(id=signal, **{"class": "AI/dynamo_compact"}, params={"path": routes[signal][0]},
                         label=signal, x=24+index*304, y=781, w=280, h=66))
    documents = [PvmDisplay(MONITOR, pvms=pvms, items=items, width=1560, height=920,
                            level=2, parent="Overview - L1 Plant",
                            description="Shutdown inventory overview with measured AI values and recent sample trends.")]
    panels = [item["id"] for item in items if item["kind"] == "rect" and item["id"].endswith("_panel")]
    documents[0].stacking_order = panels + [pvm["id"] for pvm in pvms] + [item["id"] for item in items if item["id"] not in panels]

    items = [text("title", "SAFE LANDING  /  Manual control workspace", 24, 18, 1160, 45, 20),
             text("authority", "Equipment controls remain available at every step, during Pause, and after End guide.", 24, 76, 1210, 34),
             text("sequence", "Open the PA faceplate for guidance. Each command below is an individual operator action; verify actual feedback before confirming.", 24, 122, 1480, 48),
             link("monitor", "Vessels / trends / AI", MONITOR, 24, 855, 340)]
    items += instance(1290, 12)
    for index, stage in enumerate(stages()):
        y = 203 + index*88
        items.append(text(stage.key+"_title", stage.title, 24, y, 450, 36, 13))
        if stage.actions:
            items.append(link(stage.key, "Open manual controls", control_name(stage), 504, y, 300))
        else:
            items.append(text("field", "Simulation Workbench → Plant disturbances → MF-031; active, value 0. Verify FT-1004.",
                              504, y, 995, 60))
    documents.append(PvmDisplay(WORKFLOW, items=items, width=1560, height=920, level=3, parent=MONITOR))

    for stage in stages():
        if not stage.actions:
            continue
        items = [text("title", stage.title + " / Manual controls", 24, 18, 1150, 48, 20),
                 text("instruction", stage.instruction, 24, 82, 1512, 88),
                 text("authority", "1 Select MAN   2 Apply the individual demand   3 Verify field feedback in the PA conditions. Protection remains active.", 24, 180, 1512, 50),
                 link("workspace", "All manual controls", WORKFLOW, 24, 865, 340),
                 link("monitor", "Vessels / trends / AI", MONITOR, 395, 865, 340)]
        columns = 2 if len(stage.actions) > 10 else 1
        for col in range(columns):
            x = 24 + col*770
            for label, offset, w in (("Output / demand", 0, 260), ("Mode", 266, 75), ("Readback", 430, 105)):
                items.append(text(f"header{col}{offset}", label, x+offset, 240, w))
        for index, (tag, value) in enumerate(stage.actions):
            col, row = divmod(index, 11) if columns == 2 else (0, index)
            x, y = 24 + col*770, 280+row*50
            path, kind = routes[tag]
            field = "OUT" if kind == "AO" else "OUT_D"
            feedback = "READBACK" if kind == "AO" else "READBACK_D"
            items += [text(tag+"_tag", tag, x, y, 258),
                      readout(tag+"_mode", path+"/MODE.ACTUAL", x+266, y, 78, "string"),
                      readout(tag+"_readback", path+"/"+feedback, x+430, y, 90, "string" if kind == "DO" else "numeric")]
            command_label = ("Remove START" if tag.endswith("-STR") else "Apply STOP" if tag.endswith("-STP")
                             else "Apply CLOSE" if tag.endswith("-CLS") else "Remove OPEN") if kind == "DO" else f"Set {value:g}%"
            for key, label, target, setting, dx, width in (
                    ("manual", "MAN", path+"/MODE.TARGET", "MAN", 352, 66),
                    ("apply", command_label, path+"/"+field, value, 538, 184)):
                items.append(dict(kind="user_entry", id=tag+"_"+key, x=x+dx, y=y, w=width, h=34,
                                  entry={"kind": "button", "path": target, "value": setting, "label": label}))
        documents.append(PvmDisplay(control_name(stage), items=items, width=1560, height=920,
                                    level=3, parent=MONITOR))
    return documents
