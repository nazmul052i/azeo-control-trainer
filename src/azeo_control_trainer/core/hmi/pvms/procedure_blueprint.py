"""Editable procedure HMI scaffolds, stored as ordinary user PVM definitions."""
from .configurator.model import PvmConfiguration, PvmProperty, PropertyGroup


def configuration(name):
    return PvmConfiguration(name, [PropertyGroup("Procedure", [
        PvmProperty("ProcedureRef", "Procedure Reference", title="Procedure revision",
                    description="Project procedures/ relative YAML revision, e.g. startup/rev-001/procedure.yaml. Read-only advisory state.",
                    required=True),
        PvmProperty("Title", "String", title="Display title", default="PROCEDURE"),
    ], present_online=True)])


def create_blueprint(library, name):
    """Seed shapes and bindings, never a separately coded faceplate body."""
    names = (name, name + "_PVM", name + "_Detail", name + "_Conditions", name + "_Tuning", name + "_Trends", name + "_History")
    library.reload()
    if not name or not name.isidentifier() or any(n in library.entries for n in names):
        raise ValueError("Choose a new class name; the PVM, faceplate and detail names must be unused")

    def path(field):
        return "@procedure/{ProcedureRef}/" + field

    def text(key, label, x, y, w, h=22, size=10):
        return dict(kind="text", id=key, text=label, x=x, y=y, w=w, h=h, font_size=size,
                    text_wrap=True, text_valign="top" if h > 26 else "middle")

    def data(key, field, x, y, w, h=26):
        return dict(kind="datalink", id=key, path=path(field), datalink_type="string",
                    x=x, y=y, w=w, h=h, word_wrap=True)

    def surface(w, h):
        return dict(kind="rect", id="surface", x=0, y=0, w=w, h=h,
                    fill_role="SURFACE_PANEL", width=1, locked=True)

    def button(command, label, x, y, w=86):
        return dict(kind="user_entry", id=command, x=x, y=y, w=w, h=30,
                    entry={"kind": "button", "label": label},
                    tooltip="Procedure unavailable: verify the revision, audit storage and controller readiness",
                    command_context=path("CONTEXT"),
                    actions=[dict(event="click", kind="procedure_command",
                                  target=command, source=path("CONTEXT"))],
                    enabled=False,
                    props={"enabled": {"kind": "animation", "path": path("CAN_" + command.upper()),
                                       "type": "boolean"},
                           "tooltip": {"kind": "animation", "path": path("REASON_" + command.upper()), "type": "string"}})

    def navigate(key, label, target, x, y, w):
        return dict(kind="user_entry", id=key, x=x, y=y, w=w, h=30,
                    entry={"kind": "button", "label": label},
                    actions=[dict(event="click", kind="open_user_faceplate" if target == name else "open_user_detail", target=target)])

    def table(key, field, columns, x, y, w, h):
        return dict(kind="table", id=key, x=x, y=y, w=w, h=h,
                    rows_path=path(field), rows=[], row_height=26, header_height=24,
                    row_help=field == "STEPS",
                    command_context=path("CONTEXT"),
                    empty_text={"CONDITIONS": "No active wait · conditions appear when a wait step runs",
                                "PARAMETERS": "No exposed parameters · configure operator tuning in PA Designer",
                                "EVENTS": "No events yet · start a procedure to record its progress",
                                "HISTORY": "No recorded runs for this procedure"}.get(field, "No observations available"),
                    columns=[dict(key=k, title=t, width=weight) for k, t, weight in columns])

    pvm = [surface(240, 100), text("title", "Pvm.Title", 10, 5, 220, size=11),
           data("state", "STATE", 10, 27, 220),
           navigate("open", "Procedure…", name, 10, 60, 220)]
    faceplate = [surface(440, 566), text("title", "Pvm.Title · Advisory", 12, 8, 416, size=12),
                 data("name", "TITLE", 12, 34, 416), data("state", "STATE", 12, 64, 416),
                 data("source", "SOURCE", 12, 93, 416, 44),
                 text("current_label", "Current instruction / operator response", 12, 141, 416),
                 data("instruction", "INSTRUCTION", 12, 169, 416, 126),
                 data("progress", "PROGRESS", 12, 302, 416, 42),
                 button("respond", "Respond to current prompt…", 12, 354, 416),
                 button("start", "Start", 12, 398, 96), button("pause", "Pause", 119, 398, 96),
                 button("resume", "Resume", 226, 398, 96), button("abort", "Abort", 333, 398, 95),
                 navigate("detail", "Workflow / observations / events…", names[2], 12, 442, 282),
                 button("equipment", "Equipment…", 306, 442, 122),
                 navigate("conditions_page", "Conditions", names[3], 12, 484, 132),
                 navigate("tuning_page", "Tuning", names[4], 152, 484, 132),
                 navigate("trends_page", "Trends", names[5], 292, 484, 136),
                 navigate("history_page", "History", names[6], 12, 526, 132),
                 button("help", "Help", 152, 526, 132),
                 button("workflow", "Locate step", 292, 526, 136)]
    # The original three-class contract remains; expanded pages share that
    # same ProcedureRef and use ordinary editable shapes and live collections.
    def page(title):
        return [surface(1020, 706), text("title", "Pvm.Title · " + title, 16, 8, 988, size=13),
                data("state", "STATE", 16, 40, 330), data("source", "SOURCE", 360, 40, 644, 34),
                navigate("workflow_page", "Workflow", names[2], 16, 84, 124),
                navigate("conditions_page", "Conditions", names[3], 150, 84, 124),
                navigate("tuning_page", "Tuning", names[4], 284, 84, 124),
                navigate("trends_page", "Trends", names[5], 418, 84, 124),
                navigate("history_page", "History", names[6], 552, 84, 124),
                button("equipment", "Equipment…", 686, 84, 152), button("help", "Help", 848, 84, 156)]

    workflow = table("workflow", "STEPS", [("step", "Step", 1), ("instruction", "Instruction", 3),
                                           ("state", "State", 1)], 16, 130, 590, 558)
    workflow["presentation"] = "workflow"
    detail = page("Workflow") + [workflow,
        text("current_label", "Current instruction", 628, 132, 376),
        data("instruction", "INSTRUCTION", 628, 163, 376, 130),
        data("progress", "PROGRESS", 628, 304, 376, 70),
        button("respond", "Respond to current prompt…", 628, 386, 376),
        button("workflow", "Live workflow / locate step…", 628, 430, 376),
        dict(button("equipment", "Equipment faceplate…", 628, 474, 376), id="equipment_detail"),
        text("manual", "Pause guidance at any time to work manually through equipment faceplates. Resume restarts hold qualification. Held ends the run.", 628, 530, 376, 108),
        button("pause", "Pause guidance", 628, 650, 180), button("resume", "Resume", 824, 650, 180)]
    conditions = page("Conditions / hold monitor") + [
        data("progress", "PROGRESS", 16, 130, 988, 42),
        table("conditions", "CONDITIONS", [("id", "Row", .5), ("condition", "Expression", 2.3),
              ("value", "Observed values", 2), ("quality", "Quality", .8), ("state", "State", .8),
              ("timer", "Continuous hold", 1), ("reason", "Reason", 1.5)], 16, 184, 988, 270),
        text("evidence", "All rows use one captured scan. False or unknown resets that row; group hold starts only after the configured ALL / ANY / custom group qualifies.", 16, 464, 988, 48),
        table("values", "VALUES", [("tag", "Tag", 1.2), ("path", "Source", 2), ("value", "Value", 1), ("quality", "Quality", .8)], 16, 526, 988, 160)]
    parameters = table("parameters", "PARAMETERS", [("parameter", "Parameter", 2), ("value", "Current", 1),
        ("unit", "Unit", .5), ("limits", "Limits", 1), ("access", "Applies", 1.2), ("pending", "Next run", 1)], 16, 178, 988, 394)
    parameters["row_action"] = "tune"
    tuning = page("Tuning") + [
        text("instructions", "Double-click a row or right-click → Tune parameter. Only engineer-exposed values are adjustable.", 16, 132, 988, 34),
        parameters, button("tune", "Tune parameter…", 16, 588, 250),
        text("effect", "Live: applies at an observation boundary and resets hold evidence. Next run: saved once for this revision; does not alter the active run. Changes record actor, previous value, new value and effect.", 16, 632, 988, 58)]
    trends = page("Trends") + [
        dict(kind="chart", id="history_chart", x=16, y=132, w=988, h=474, pens=[], series_path=path("TREND")),
        button("trends", "Process History View…", 16, 624, 250),
        text("trend_help", "Select pens, adjust ranges and scales, inspect event markers, and export in Process History View. Memory and process values use the shared station historian; gaps remain gaps.", 284, 618, 720, 70)]
    history = page("History") + [
        text("current_events", "Current run events · simulation seconds", 16, 132, 988),
        table("events", "EVENTS", [("time", "Time", .7), ("event", "Event", 1), ("detail", "Detail", 4)], 16, 162, 988, 220),
        text("past_runs", "Recent runs of this procedure · UTC", 16, 398, 988),
        table("runs", "HISTORY", [("time", "Started", 1.2), ("name", "Procedure", 1.4), ("state", "Result", .7), ("run_id", "Run ID", 2)], 16, 430, 988, 208),
        button("history", "Review / compare / export runs…", 16, 656, 350)]
    created = []
    try:
        for class_name, items, kind in ((name, faceplate, "faceplate"),
                                        (names[1], pvm, "pvm"),
                                        (names[2], detail, "detail"),
                                        (names[3], conditions, "detail"), (names[4], tuning, "detail"),
                                        (names[5], trends, "detail"), (names[6], history, "detail")):
            entry = library.add(class_name, items,
                                folder={"pvm": "My PVMs", "faceplate": "My Faceplates", "detail": "My Details"}[kind],
                                definition_kind=kind,
                                paired_faceplate=name if kind == "pvm" else "")
            if entry is None:
                raise ValueError(f"Could not create {class_name}")
            created.append(class_name)
        return names
    except Exception:
        for class_name in created:
            library.remove(class_name)
        raise
