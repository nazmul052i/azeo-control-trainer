"""Procedure commands use the context last rendered by the calling HMI element."""
from copy import deepcopy

from PySide6.QtWidgets import QInputDialog, QMessageBox

from azeo_control_trainer.core.presentation.headless import is_headless


def operator_response(parent, prompt):
    if is_headless():
        return None, False
    title = "Procedure response"
    message = prompt["prompt"]
    if prompt["prompt_kind"] == "skip_gate":
        choice, accepted = QInputDialog.getItem(parent, title, message,
                                                 ["Continue step", "Skip with reason"], 0, False)
        if not accepted:
            return None, False
        if choice == "Continue step":
            return {"decision": "continue"}, True
        reason, accepted = QInputDialog.getText(parent, "Skip step", "Reason for skipping this step")
        return {"decision": "skip", "reason": reason}, accepted
    if prompt["prompt_kind"] in {"confirm", "output", "hmi_window"}:
        if prompt["prompt_kind"] == "output":
            schedule = prompt.get("schedule")
            detail = (
                f"{prompt['path']}: {schedule['start']} to {schedule['end']} "
                f"at {schedule['rate_per_sec']}/s"
                if schedule else f"{prompt['path']} = {prompt.get('value')!r}"
            )
            message += f"\n\nChecked output: {detail}"
        elif prompt["prompt_kind"] == "hmi_window":
            message += f"\n\nOpen: {prompt['target']}"
        answer = QMessageBox.question(parent, title, message,
                                      QMessageBox.Yes | QMessageBox.Cancel,
                                      QMessageBox.Cancel)
        return True, answer == QMessageBox.Yes
    step = prompt["step"]
    choices = step.get("choices", ())
    if step.get("input_type") == "bool":
        choices = [True, False]
    if choices:
        labels = [str(choice) for choice in choices]
        default = step.get("value")
        text, accepted = QInputDialog.getItem(parent, title, message, labels,
                                             choices.index(default) if default in choices else 0, False)
        return (choices[labels.index(text)] if accepted else None), accepted
    text, accepted = QInputDialog.getText(parent, title, message, text=str(step.get("value", "")))
    return text, accepted


def dispatch(station, action, view, item):
    try:
        session = station.procedure_session()
        if session is None or item is None:
            raise ValueError("A procedure command requires a live station and a bound HMI element")
        data = item.data if isinstance(getattr(item, "data", None), dict) else {}
        binding = item.bindings.get(action.get("source") or data.get("command_context", ""))
        if binding is None or binding.result.quality.name != "GOOD":
            raise ValueError("Procedure context is unavailable; refresh the display")
        context = deepcopy(binding.result.value)
        if not isinstance(context, dict):
            raise ValueError("Invalid procedure context")
        command = str(action.get("target", ""))
        if command == "workflow":
            station.open_procedures().show_workflow(context["ref"])
            return True
        if command == "equipment":
            path = action.get("value") or context.get("equipment", "")
            mapped = {"/".join(path.split("/")[:2]) for path in session.prepare(context["ref"]).bindings.values()}
            if path not in mapped:
                raise ValueError("Equipment is not mapped by this procedure")
            return open_equipment(station, session.context_for(context["ref"]), path)
        if command == "trends":
            from .procedure_monitor import trend_paths
            paths = trend_paths(session, context["ref"])
            if not paths:
                raise ValueError("No numeric process or memory points are available for this procedure")
            station.open_process_history(paths=paths[:10])
            return True
        if command == "history":
            dialog = station.open_procedures()
            dialog.tabs.setCurrentIndex(dialog.tabs.count() - 1)
            dialog.refresh_history()
            return True
        if command == "help":
            return block_help(station, "operator_hmi")
        if command == "tune":
            if not station.settings.write_authority:
                raise ValueError("View-only station cannot tune procedures")
            from .procedure_tuning import ParameterEditor
            if not session.snapshot(context["ref"])["PARAMETERS"]:
                raise ValueError("No parameters are exposed for operator tuning")
            station._present(ParameterEditor(station, session.context_for(context["ref"]), context,
                                             str(action.get("value") or "")))
            return True
        value = None
        if command == "respond":
            if not context.get("prompt"):
                raise ValueError("There is no current operator prompt")
            value, accepted = operator_response(view or station, context["prompt"])
            if not accepted:
                return False
            prompt_kind = context["prompt"]["prompt_kind"]
            if prompt_kind == "hmi_window":
                target = str(context["prompt"].get("target", "")).strip()
                value = session.open_hmi_target(target, context["ref"])
            elif prompt_kind == "output":
                value = True
        elif command == "abort" and not is_headless():
            if QMessageBox.question(view or station, "Abort procedure", "End this advisory procedure run?",
                                    QMessageBox.Yes | QMessageBox.Cancel,
                                    QMessageBox.Cancel) != QMessageBox.Yes:
                return False
        elif command == "break":
            if is_headless():
                return False
            value, accepted = QInputDialog.getText(view or station, "Break procedure",
                                                   "Reason for the break")
            if not accepted:
                return False
        session.execute(command, context["token"], value, context["ref"])
        station.command_feedback.setText("Procedure: " + command + " accepted")
        station.feedback_area.show()
        if view is not None:
            view.refresh()
        return True
    except Exception as error:
        station.command_feedback.setText("Procedure command refused: " + str(error))
        station.feedback_area.show()
        return False


def equipment_class(station, path):
    from azeo_control_trainer.core.hmi.pvms.base import registry
    module, _, name = path.partition("/")
    graph = station.graphs_provider().get(module)
    block = next((block for block in graph.blocks.values() if block.instance_name == name), None) if graph else None
    cls = registry.get(block.block_type, "faceplate") if block else None
    return cls


def open_equipment(station, session, path):
    cls = equipment_class(station, path)
    if cls is None:
        raise ValueError(f"No installed equipment faceplate for {path}")
    if session.run.active:
        session.run.control.suspend_for_presentation()
    try:
        station.open_faceplate(cls().place("procedure-equipment", path=path))
    finally:
        session.observations.wait_for_scan()
    return True


def block_help(station, step_type):
    from azeo_control_trainer.core.presentation.procedure_help import ProcedureHelpCenter
    from azeo_control_trainer.core.procedures.help_content import help_topics
    if step_type not in help_topics():
        return False
    if getattr(station, "_procedure_help", None) is None:
        station._procedure_help = ProcedureHelpCenter(station)
        station._procedure_help.setWindowTitle("Procedure Block Help")
    station._procedure_help.show_topic(step_type)
    station._procedure_help.show()
    station._procedure_help.raise_()
    return True
