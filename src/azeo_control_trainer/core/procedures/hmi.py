"""Read-only procedure bindings and checked response types shared by HMI clients."""
from __future__ import annotations

import math
import logging
from pathlib import PurePosixPath
import weakref

PREFIX = "@procedure/"
COMMANDS = ("start", "pause", "break", "resume", "abort", "respond", "equipment", "tune", "trends", "history", "help", "workflow")
FIELDS = frozenset({"TITLE", "STATE", "SOURCE", "INSTRUCTION", "PROGRESS", "RUN_ID", "RESULT",
                    "TOKEN", "PROMPT", "STEPS", "VALUES", "EVENTS", "COMPLETION", "REASON", "CONTEXT",
                    "REVISION", "CONDITIONS", "PARAMETERS", "TREND", "HISTORY", *("CAN_" + command.upper() for command in COMMANDS),
                    *("REASON_" + command.upper() for command in COMMANDS)})


def reference(value: str) -> str:
    value = str(value).replace("\\", "/")
    path = PurePosixPath(value)
    if (not value or path.is_absolute() or ":" in value or
            any(part in {"", ".", ".."} for part in value.split("/")) or
            path.suffix.lower() not in {".yaml", ".yml"}):
        raise ValueError("ProcedureRef must be a relative procedure YAML revision within the project library")
    return value


def split_path(path: str) -> tuple[str, str]:
    ref, _, field = path.removeprefix(PREFIX).rpartition("/")
    if field.startswith("MEMORY:"):
        from ..datastore.memory_tags import memory_name
        memory_name("MEMORY/" + field.removeprefix("MEMORY:") + "/VALUE")
        return reference(ref), field
    if field not in FIELDS:
        raise ValueError("Unknown procedure snapshot field")
    return reference(ref), field


def response_value(prompt: dict, value):
    kind = prompt["prompt_kind"]
    if kind == "skip_gate":
        if not isinstance(value, dict) or value.get("decision") not in {"continue", "skip"}:
            raise ValueError("Choose Continue or Skip")
        if value["decision"] == "continue":
            return {"decision": "continue", "reason": ""}
        reason = value.get("reason")
        if not isinstance(reason, str) or not reason.strip() or len(reason.strip()) > 500:
            raise ValueError("Skipping requires a reason of at most 500 characters")
        return {"decision": "skip", "reason": reason.strip()}
    step = prompt["step"]
    if kind in {"confirm", "output", "hmi_window"}:
        if value is not True:
            raise ValueError("This action requires explicit acceptance")
        return True
    if kind == "comment":
        if not isinstance(value, str) or not value.strip():
            raise ValueError("Enter an observation before submitting")
        return value.strip()
    dtype = step.get("input_type", "float")
    if dtype in {"float", "int"}:
        if isinstance(value, bool):
            raise ValueError("Enter a number")
        number = float(value)
        if not math.isfinite(number) or (dtype == "int" and not number.is_integer()):
            raise ValueError("Enter a finite " + ("integer" if dtype == "int" else "number"))
        for key, rejected in (("min_value", lambda limit: number < limit),
                              ("max_value", lambda limit: number > limit)):
            if step.get(key) is not None and rejected(step[key]):
                raise ValueError(f"Response violates {key}: {step[key]}")
        return int(number) if dtype == "int" else number
    if dtype == "bool":
        if type(value) is not bool and (not isinstance(value, str) or value not in {"true", "false"}):
            raise ValueError("Choose true or false")
        return value is True or value == "true"
    if dtype == "selection":
        if value not in step.get("choices", ()):
            raise ValueError("Choose one of the procedure's configured responses")
        return value
    if not isinstance(value, str):
        raise ValueError("Enter a text response")
    return value


class ProcedureSource:
    """Decorate the station source without granting procedure paths write access."""
    def __init__(self, base, session):
        self.base = base
        self._session_ref = weakref.WeakMethod(session) if getattr(session, "__self__", None) is not None else None
        self._session_getter = None if self._session_ref else session

    def __getattr__(self, name):
        return getattr(self.base, name)

    def __setattr__(self, name, value):
        base = self.__dict__.get("base")
        # Provider overrides must reach the source that owns snapshots and
        # alarm sampling; a shadow attribute makes the two see different graphs.
        if name not in {"base", "_session_ref", "_session_getter"} and base is not None and hasattr(base, name):
            setattr(base, name, value)
        else:
            object.__setattr__(self, name, value)

    def __delattr__(self, name):
        if name in self.__dict__:
            object.__delattr__(self, name)
        else:
            delattr(self.base, name)

    def read(self, path):
        if not str(path).startswith(PREFIX):
            return self.base.read(path)
        from azeo_control_trainer.core.hmi.binding.result import BindingResult, UNRESOLVED
        from azeo_control_trainer.core.strategy.model.terminal import Quality
        try:
            ref, field = split_path(path)
            getter = self._session_ref() if self._session_ref else self._session_getter
            session = getter() if getter else None
            if session and field.startswith("MEMORY:"):
                return session.memory_result(ref, field.removeprefix("MEMORY:"))
            if session and field == "TREND":
                return BindingResult(value=session.trend_snapshot(ref), quality=Quality.GOOD)
            snapshot = session.snapshot(ref) if session else {}
            if field not in snapshot:
                return UNRESOLVED
            return BindingResult(value=snapshot[field], quality=Quality.GOOD)
        except (ValueError, OSError):
            return UNRESOLVED
        except Exception as error:
            # A missing optional runtime or unwritable audit store must leave
            # this binding unavailable, not take down the hosting graphics view.
            message = f"{type(error).__name__}: {error}"
            if message != getattr(self, "_reported_error", None):
                logging.getLogger(__name__).warning("Procedure HMI unavailable: %s", message)
                self._reported_error = message
            return UNRESOLVED

    def can_write(self, path):
        if str(path).startswith(PREFIX):
            from azeo_control_trainer.core.hmi.binding.source import WriteResult
            return WriteResult(False, "Procedure state is read-only; use a checked procedure command")
        return self.base.can_write(path)

    def write(self, path, value):
        if str(path).startswith(PREFIX):
            return self.can_write(path)
        return self.base.write(path, value)


class ProcedureDesignSource(ProcedureSource):
    """Graphics Designer previews the contract; only Station can execute a run."""
    def __init__(self, base):
        super().__init__(base, lambda: None)

    def read(self, path):
        if not str(path).startswith(PREFIX):
            return self.base.read(path)
        from azeo_control_trainer.core.hmi.binding.result import BindingResult, UNRESOLVED
        from azeo_control_trainer.core.strategy.model.terminal import Quality
        field = str(path).rpartition("/")[2]
        if field not in FIELDS or field in {"CONTEXT", "PROMPT", "TOKEN"}:
            return UNRESOLVED
        if "{" not in path:
            try:
                split_path(path)
            except ValueError:
                return UNRESOLVED
        value = (False if field.startswith("CAN_") else {} if field == "TREND" else () if field in {"STEPS", "VALUES", "EVENTS", "CONDITIONS", "PARAMETERS", "HISTORY"}
                 else 0 if field == "COMPLETION" else "Design preview — operate procedures in Operator Station")
        return BindingResult(value=value, quality=Quality.GOOD)
