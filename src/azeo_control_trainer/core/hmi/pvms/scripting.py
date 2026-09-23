"""Restricted Azeo Operator Station-compatible graphics scripting.

Azeo Operator Station authors interaction scripts in TypeScript and exposes the
display through ``DL``, ``DLSYS``, ``Dsp``, ``Lyt``, ``Grp``, ``Pvm``,
``GL`` and ``ENV``.  This module provides that authoring shape without
turning a picture into an alternate controller: reads and writes still go
through :class:`LiveGraphSource`, and no filesystem, network, import or
process objects are exposed to the JavaScript engine.

Qt's JavaScript engine executes ECMAScript rather than TypeScript.  The
small transpiler removes TypeScript's erasable annotations before execution;
the editor validates both that subset and the resulting JavaScript.

**The loop ban is load-bearing, not belt-and-braces.**  ``setInterrupted()``
called from the watchdog thread does not cut a running loop short: measured
on PySide6 6.10.2 / Qt 6.10.2, a three-second counting loop ran to
completion with the flag set and ``isInterrupted()`` true afterwards.  The
timer therefore *reports* an overrun, it does not stop one, and refusing
``for``/``while``/``do`` is the only thing keeping a display event handler
from freezing the operator station. Bounded iteration is available through
capped array methods -- ``forEach``, ``map``, ``filter``, ``reduce`` -- and
the refusal message says so. Dynamic code constructors and large collection
or repeated-string constructors are removed behind the source scan.
"""
from __future__ import annotations

from ..compatibility import (
    LEGACY_SCOPE_NAME, LEGACY_SCOPE_PREFIX, PVM_SCOPE_NAMES, PVM_SCOPE_PREFIX,
)

import re
import threading
from dataclasses import dataclass, field
from typing import Callable

from PySide6.QtCore import QObject, Slot
from PySide6.QtQml import QJSEngine


SCRIPT_TIMEOUT_MS = 150
MAX_SCRIPT_SOURCE = 64 * 1024
MAX_SCRIPT_COLLECTION = 10_000

#: Capabilities a picture must never reach. A graphics script is a
#: handler on a display, not a second controller.
_FORBIDDEN_API = re.compile(
    r"\b(import|require|eval|Function|WebAssembly|XMLHttpRequest|fetch|"
    r"process|document|localStorage|sessionStorage)\b"
)

#: Unbounded loop constructs. See the module docstring: the watchdog
#: cannot interrupt one, so this refusal is the station's only guard.
_FORBIDDEN_LOOP = re.compile(r"\b(while|do|for)\b")
_FORBIDDEN_SPREAD = re.compile(r"\.\.\.")

#: What to do instead, named in the refusal rather than left to the manual.
_ITERATION_HINT = ("use arr.forEach(...), arr.map(...), arr.filter(...) or "
                   "arr.reduce(...) for bounded iteration")


def _strip_literals(text: str) -> str:
    """Blank string and comment CONTENT, keeping code and line structure.

    The forbidden-token scan must not read an operator's label.  Scanning
    raw source refused ``This.text = "Waiting for start"`` because the
    word ``for`` appeared inside a string, and reported it as a banned
    loop -- an error message that made no sense against what was written.
    Template interpolations are real code, so ``${...}`` is preserved and
    still scanned.  Newlines are preserved so offsets stay aligned.
    """
    source = str(text or "")
    out: list[str] = []
    index, length = 0, len(source)
    while index < length:
        char = source[index]
        following = source[index + 1] if index + 1 < length else ""
        if char == "/" and following == "/":
            while index < length and source[index] != "\n":
                out.append(" ")
                index += 1
            continue
        if char == "/" and following == "*":
            out.append("  ")
            index += 2
            while index < length and not (
                    source[index] == "*" and index + 1 < length
                    and source[index + 1] == "/"):
                out.append("\n" if source[index] == "\n" else " ")
                index += 1
            if index < length:
                out.append("  ")
                index += 2
            continue
        if char in "'\"`":
            quote = char
            out.append(" ")
            index += 1
            while index < length and source[index] != quote:
                if source[index] == "\\" and index + 1 < length:
                    out.append("  ")
                    index += 2
                    continue
                if quote == "`" and source[index] == "$" \
                        and index + 1 < length and source[index + 1] == "{":
                    depth = 1
                    out.append("${")
                    index += 2
                    while index < length and depth:
                        if source[index] == "{":
                            depth += 1
                        elif source[index] == "}":
                            depth -= 1
                        out.append(source[index])
                        index += 1
                    continue
                out.append("\n" if source[index] == "\n" else " ")
                index += 1
            if index < length:
                out.append(" ")
                index += 1
            continue
        out.append(char)
        index += 1
    return "".join(out)


def transpile_typescript(source: str) -> str:
    """Erase the TypeScript syntax supported by graphics scripts.

    Azeo scripts are normally short event handlers.  Supporting the
    erasable subset (typed variables, parameters, return types, ``as`` casts,
    interfaces and type aliases) covers that use without shipping a Node
    toolchain in the operator station.
    """
    text = str(source or "").replace("\r\n", "\n")
    # Type-only declarations have no runtime representation. They are
    # replaced by their own newlines rather than deleted: the engine
    # reports errors by line, and collapsing a ten-line interface would
    # move every marker below it in the editor.
    text = re.sub(
        r"(?ms)^\s*(?:export\s+)?(?:interface|type)\s+[A-Za-z_$][\w$]*"
        r"(?:\s*<[^;{]+>)?\s*(?:=\s*[^;]+;|\{.*?^\s*\})\s*;?\s*",
        lambda match: "\n" * match.group(0).count("\n"), text)
    # ``const value: number`` and function parameters.  Requiring a known
    # delimiter after the type avoids eating the colon in object literals.
    text = re.sub(
        r"(?<=[A-Za-z0-9_$])\s*:\s*"
        r"[A-Za-z_$][\w$]*(?:\s*<[^;=,){}]+>)?(?:\[\])?"
        r"(?=\s*[,)=;{])", "", text)
    # Function return annotations.
    text = re.sub(
        r"\)\s*:\s*[A-Za-z_$][\w$]*(?:\s*<[^;{]+>)?(?:\[\])?\s*\{",
        ") {", text)
    # Type assertions are compile-time only.
    text = re.sub(
        r"\s+as\s+(?:const|[A-Za-z_$][\w$]*(?:\s*<[^;,)]+>)?(?:\[\])?)",
        "", text)
    # QJSEngine does not parse ``async function`` syntax, while Azeo's
    # graphics API uses Async suffixes extensively.  These built-ins are
    # immediate in this in-process station, so awaiting one lowers to the
    # checked synchronous twin with identical result/error semantics.
    text = re.sub(
        r"\bawait\s+(DLSYS|DL)\.([A-Za-z_$][\w$]*?)Async\s*\(",
        r"\1.\2(", text)
    text = re.sub(r"\bawait\s+Promise\.resolve\(([^();]*)\)", r"\1", text)
    return text


@dataclass(frozen=True)
class ScriptResult:
    """A run result suitable for the editor and station error display."""

    ok: bool
    value: object = None
    error: str = ""
    line: int = 0
    timed_out: bool = False
    diagnostics: tuple[str, ...] = ()


@dataclass
class ScriptContext:
    """The explicit capabilities granted to one graphics script."""

    source: object = None
    alarm_state: object = None
    deployment: object = None
    view: object = None
    item: object = None
    user: str = "operator"
    can_operate: bool = True
    can_write: Callable[[str], object] | None = None
    write_value: Callable[[str, object], object] | None = None
    acknowledge_container: Callable[[str], object] | None = None
    acknowledge_all: Callable[[], object] | None = None
    open_display: Callable[[str], bool] | None = None
    open_faceplate: Callable[[str], bool] | None = None
    open_detail: Callable[[str], bool] | None = None
    open_context: Callable[[str], bool] | None = None
    session_store: dict = field(default_factory=dict)
    scope_values: dict = field(default_factory=dict)
    selected_tag: str = ""
    diagnostics: list[str] = field(default_factory=list)


class _ScriptBridge(QObject):
    """QObject surface seen by QJSEngine; everything else stays private."""

    def __init__(self, context: ScriptContext):
        super().__init__()
        self.context = context

    @Slot(str, result="QVariant")
    def read(self, path: str):
        source = self.context.source
        if source is None:
            return None
        result = source.read(str(path))
        return getattr(result, "value", None)

    @Slot(str, result="QVariant")
    def condRead(self, path: str):  # noqa: N802 - Azeo API spelling
        source = self.context.source
        if source is None:
            return {"success": False, "value": None, "status": "Bad"}
        result = source.read(str(path))
        quality = getattr(getattr(result, "quality", None), "name", "BAD")
        return {
            "success": quality != "BAD" and getattr(result, "value", None)
            is not None,
            "value": getattr(result, "value", None),
            "status": quality.title(),
            "units": getattr(result, "units", ""),
            "forced": bool(getattr(result, "forced", False)),
        }

    @Slot(str, result=bool)
    def exists(self, path: str) -> bool:
        return bool(self.condRead(path)["success"])

    @Slot(str, result=bool)
    def canWrite(self, path: str) -> bool:  # noqa: N802
        source = self.context.source
        check = self.context.can_write or (
            getattr(source, "can_write", None) if source is not None else None)
        if not self.context.can_operate or not callable(check):
            return False
        answer = check(str(path))
        return bool(getattr(answer, "success", answer))

    @Slot(str, str, result=bool)
    def canPerform(self, name: str, path: str = "") -> bool:  # noqa: N802
        operation = str(name).casefold()
        if operation == "write":
            return self.canWrite(path)
        if operation in {"ack", "ackallalarms", "ackalarmsincontainer"}:
            return bool(
                self.context.can_operate
                and (self.context.alarm_state is not None
                     or callable(self.context.acknowledge_container)
                     or callable(self.context.acknowledge_all)))
        if operation in {"read", "condread", "exists"}:
            return self.context.source is not None
        return False

    @Slot(str, "QVariant", result=bool)
    def write(self, path: str, value) -> bool:
        if not self.canWrite(path):
            self.context.diagnostics.append(
                f"Write refused for {path}: view-only or parameter-owned")
            return False
        writer = self.context.write_value or getattr(
            self.context.source, "write", None)
        if not callable(writer):
            self.context.diagnostics.append(
                f"Write refused for {path}: no checked write service")
            return False
        answer = writer(str(path), value)
        success = bool(getattr(answer, "success", answer))
        if not success:
            self.context.diagnostics.append(
                f"Write refused for {path}: "
                f"{getattr(answer, 'error', 'write service refused it')}")
        return success

    @Slot(str, result=int)
    def ackContainer(self, prefix: str) -> int:  # noqa: N802
        if not self.context.can_operate:
            self.context.diagnostics.append(
                "Alarm acknowledgement refused: station is view-only")
            return 0
        callback = self.context.acknowledge_container
        if callable(callback):
            answer = callback(str(prefix))
            return int(answer) if isinstance(answer, int) \
                else len(tuple(answer or ()))
        registry = self.context.alarm_state
        if registry is None:
            return 0
        keys = tuple(row.key for row in registry.records()
                     if row.key.startswith(str(prefix).rstrip("/") + "/")
                     or row.key == str(prefix).rstrip("/"))
        return len(registry.acknowledge(keys))

    @Slot(result=int)
    def ackAll(self) -> int:  # noqa: N802
        if not self.context.can_operate:
            self.context.diagnostics.append(
                "Alarm acknowledgement refused: station is view-only")
            return 0
        callback = self.context.acknowledge_all
        if callable(callback):
            answer = callback()
            return int(answer) if isinstance(answer, int) \
                else len(tuple(answer or ()))
        registry = self.context.alarm_state
        return len(registry.acknowledge()) if registry is not None else 0

    @Slot(str, result=bool)
    def displayExists(self, name: str) -> bool:  # noqa: N802
        deployment = self.context.deployment
        return bool(deployment is not None
                    and str(name) in deployment.displays())

    @Slot(str, result=bool)
    def openDisplay(self, name: str) -> bool:  # noqa: N802
        callback = self.context.open_display
        return bool(callback is not None and callback(str(name)))

    @Slot(str, result=bool)
    def openFaceplate(self, path: str) -> bool:  # noqa: N802
        callback = self.context.open_faceplate
        return bool(callback is not None and callback(str(path)))

    @Slot(str, result=bool)
    def openDetail(self, path: str) -> bool:  # noqa: N802
        callback = self.context.open_detail
        return bool(callback is not None and callback(str(path)))

    @Slot(str, result=bool)
    def openContext(self, path: str) -> bool:  # noqa: N802
        callback = self.context.open_context
        return bool(callback is not None and callback(str(path)))

    @Slot(str, result=bool)
    def popup(self, action: str) -> bool:
        view = self.context.view
        window = view.window() if view is not None else None
        if window is None:
            return False
        if action == "close":
            return bool(window.close())
        if action == "minimize":
            window.showMinimized()
            return True
        if action == "restore":
            window.showNormal()
            return True
        return False

    @Slot(str, "QVariant", result="QVariant")
    def storeSet(self, key: str, value):  # noqa: N802
        self.context.session_store[str(key)] = value
        return value

    @Slot(str, result="QVariant")
    def storeGet(self, key: str):  # noqa: N802
        return self.context.session_store.get(str(key))

    @Slot(str, result=bool)
    def storeRemove(self, key: str) -> bool:  # noqa: N802
        return self.context.session_store.pop(str(key), None) is not None

    @Slot()
    def storeClear(self):  # noqa: N802
        self.context.session_store.clear()

    @Slot(result=str)
    def selectedTag(self) -> str:  # noqa: N802
        return self.context.selected_tag

    @Slot(str, result=str)
    def selectTag(self, path: str) -> str:  # noqa: N802
        self.context.selected_tag = str(path)
        return self.context.selected_tag

    @Slot(int, int, result=bool)
    def getBit(self, value: int, bit: int) -> bool:  # noqa: N802
        return bool(int(value) & (1 << max(0, int(bit))))

    @Slot(str, result="QVariant")
    def scopeRead(self, path: str):  # noqa: N802
        found, value = self._graphic_property(str(path))
        if found:
            return value
        values = self.context.scope_values
        if path in values:
            return values[path]
        if path.startswith("Pvm."):
            return values.get(LEGACY_SCOPE_PREFIX + path[4:])
        return None

    @Slot(str, "QVariant", result=bool)
    def scopeWrite(self, path: str, value) -> bool:  # noqa: N802
        if self._set_graphic_property(str(path), value):
            return True
        self.context.scope_values[str(path)] = value
        if path.startswith(PVM_SCOPE_PREFIX) \
                and LEGACY_SCOPE_PREFIX + path[4:] in self.context.scope_values:
            self.context.scope_values[LEGACY_SCOPE_PREFIX + path[4:]] = value
        return True

    @Slot(str, result="QVariant")
    def elementRegion(self, name: str):  # noqa: N802
        item = self._find_item(str(name))
        if item is None:
            return None
        rect = item.sceneBoundingRect()
        return {"Left": rect.left(), "Top": rect.top(),
                "Width": rect.width(), "Height": rect.height(),
                "Right": rect.right(), "Bottom": rect.bottom()}

    @Slot(str)
    def diagnostic(self, message: str):
        self.context.diagnostics.append(str(message))

    @Slot(result=str)
    def user(self) -> str:
        return self.context.user

    @Slot(result=str)
    def displayName(self) -> str:  # noqa: N802
        display = getattr(self.context.view, "display", None)
        return str(getattr(display, "name", ""))

    def _find_item(self, name: str):
        view = self.context.view
        if view is None or view.scene() is None:
            return None
        if name in ("", "This"):
            return self.context.item
        for item in view.scene().items():
            data = getattr(item, "data", None)
            pvm = getattr(item, "pvm", None)
            candidates = {
                str(getattr(pvm, "id", "")), str(getattr(pvm, "label", ""))}
            if isinstance(data, dict):
                candidates.update(str(data.get(key, ""))
                                  for key in ("id", "title", "name"))
            if name in candidates:
                return item
        return None

    def _graphic_property(self, path: str):
        scope, _, rest = path.partition(".")
        if scope not in ("Dsp", "Lyt", "Grp", *PVM_SCOPE_NAMES, "GL", "This"):
            return False, None
        item_name, dot, prop = rest.rpartition(".")
        item = self.context.item if scope in (*PVM_SCOPE_NAMES, "This") and not dot \
            else self._find_item(item_name)
        if item is None or not prop:
            return False, None
        key = _PROPERTY_KEYS.get(prop, prop)
        data = getattr(item, "data", None)
        pvm = getattr(item, "pvm", None)
        if key == "x":
            return True, item.pos().x()
        if key == "y":
            return True, item.pos().y()
        if key == "visible":
            return True, item.isVisible()
        if prop == "Tag" and pvm is not None:
            return True, str(next(iter((pvm.params or {}).values()), ""))
        if isinstance(data, dict) and key in data:
            return True, data.get(key)
        if pvm is not None and hasattr(pvm, key):
            return True, getattr(pvm, key)
        return False, None

    def _set_graphic_property(self, path: str, value) -> bool:
        scope, _, rest = path.partition(".")
        item_name, dot, prop = rest.rpartition(".")
        item = self.context.item if scope in (*PVM_SCOPE_NAMES, "This") and not dot \
            else self._find_item(item_name)
        if item is None or not prop:
            return False
        key = _PROPERTY_KEYS.get(prop, prop)
        data = getattr(item, "data", None)
        pvm = getattr(item, "pvm", None)
        try:
            if key == "x":
                item.setX(float(value))
            elif key == "y":
                item.setY(float(value))
            elif key == "visible":
                item.setVisible(bool(value))
                if isinstance(data, dict):
                    data[key] = bool(value)
            elif isinstance(data, dict):
                data[key] = value
                if key == "rot" and hasattr(item, "apply_rotation"):
                    item.apply_rotation()
            elif pvm is not None and hasattr(pvm, key):
                from dataclasses import replace
                item.pvm = replace(pvm, **{key: value})
            else:
                return False
            item.update()
            return True
        except (TypeError, ValueError):
            return False


_PROPERTY_KEYS = {
    "Label": "label", "Text": "text", "Visible": "visible",
    "FillColor": "fill", "Fill": "fill", "LineColor": "line",
    "Line": "line", "Rotation": "rot", "HorizontalPosition": "x",
    "VerticalPosition": "y", "Width": "w", "Height": "h",
}


_PRELUDE = r"""
const __scope = (prefix) => new Proxy({}, {
  get(_target, name) {
    if (name === Symbol.toPrimitive) return () => prefix;
    const path = prefix + "." + String(name);
    const value = __bridge.scopeRead(path);
    return value === null || value === undefined ? __scope(path) : value;
  },
  set(_target, name, value) {
    return __bridge.scopeWrite(prefix + "." + String(name), value);
  }
});
const __dlsys = {
  Read: p => __bridge.read(String(p)),
  ReadAsync: p => Promise.resolve(__bridge.read(String(p))),
  CondRead: p => __bridge.condRead(String(p)),
  CondReadAsync: p => Promise.resolve(__bridge.condRead(String(p))),
  Exists: p => __bridge.exists(String(p)),
  ExistsAsync: p => Promise.resolve(__bridge.exists(String(p))),
  CanWrite: p => __bridge.canWrite(String(p)),
  CanWriteAsync: p => Promise.resolve(__bridge.canWrite(String(p))),
  CanPerformFunction: (name, p="") =>
    __bridge.canPerform(String(name), String(p)),
  CanPerformFunctionAsync: (name, p="") => Promise.resolve(
    __bridge.canPerform(String(name), String(p))),
  Write: (p, v) => __bridge.write(String(p), v),
  WriteAsync: (p, v) => Promise.resolve(__bridge.write(String(p), v)),
  AckAlarmsInContainer: p => __bridge.ackContainer(String(p)),
  AckAllAlarms: () => __bridge.ackAll(),
  GetAssociatedModuleName: p => String(p).split("/")[0],
  GetAssociatedModuleNameAsync: p => Promise.resolve(String(p).split("/")[0]),
  SISWriteAsync: () => Promise.reject(new Error("SIS writes are unavailable"))
};
this.DLSYS = new Proxy(__dlsys, {
  get(target, name) { return name in target ? target[name] :
    __bridge.read(String(name)); },
  set(_target, name, value) { return __bridge.write(String(name), value); }
});
this.DL = {
  AddStoreItem: (k, v) => __bridge.storeSet(String(k), v),
  GetStoreItem: k => __bridge.storeGet(String(k)),
  RemoveStoreItem: k => __bridge.storeRemove(String(k)),
  ClearStore: () => __bridge.storeClear(),
  DisplayExistsAsync: n => Promise.resolve(__bridge.displayExists(String(n))),
  DisplayExists: n => __bridge.displayExists(String(n)),
  OpenDisplay: n => __bridge.openDisplay(String(n)),
  OpenDisplayAsync: n => Promise.resolve(__bridge.openDisplay(String(n))),
  OpenPrimaryDisplayAsync: n => Promise.resolve(__bridge.openDisplay(String(n))),
  OpenPrimaryDisplay: n => __bridge.openDisplay(String(n)),
  OpenFaceplateAsync: p => Promise.resolve(__bridge.openFaceplate(String(p || ""))),
  OpenFaceplate: p => __bridge.openFaceplate(String(p || "")),
  OpenDetailAsync: p => Promise.resolve(__bridge.openDetail(String(p || ""))),
  OpenDetail: p => __bridge.openDetail(String(p || "")),
  OpenContextAsync: p => Promise.resolve(__bridge.openContext(String(p || ""))),
  OpenContext: p => __bridge.openContext(String(p || "")),
  GetStoreItem: k => __bridge.storeGet(String(k)),
  GetSelectedTag: () => __bridge.selectedTag(),
  IsTagSelected: () => Boolean(__bridge.selectedTag()),
  SelectTag: p => __bridge.selectTag(String(p)),
  GetBit: (v, b) => __bridge.getBit(Number(v), Number(b)),
  GetDisplayElementRegion: n => __bridge.elementRegion(String(n)),
  GetParentDisplay: () => ENV.DisplayName,
  SubmitDiagnosticFailure: m => __bridge.diagnostic(String(m)),
  MessageBoxAsync: m => { __bridge.diagnostic(String(m)); return Promise.resolve(true); },
  MessageBox: m => { __bridge.diagnostic(String(m)); return true; },
  PopupClose: () => __bridge.popup("close"),
  PopupMinimize: () => __bridge.popup("minimize"),
  PopupRestore: () => __bridge.popup("restore"),
  ShowContextMenuAsync: () => Promise.resolve(false), SyncAccess: f => f()
};
this.Dsp = __scope("Dsp");
this.Lyt = __scope("Lyt");
this.Grp = __scope("Grp");
this.Pvm = __scope("Pvm");
this.__LEGACY_SCOPE__ = this.Pvm;
this.GL = __scope("GL");
this.This = __scope("This");
this.DLPATH = { Resolve: p => String(p), Combine: (...p) => p.join("/") };
this.ENV = Object.freeze({
  User: __bridge.user(), DisplayName: __bridge.displayName(), IsOperator: true
});
this.SQL = Object.freeze({
  QueryAsync: () => Promise.reject(new Error("SQL is unavailable in this station"))
});
"""
_PRELUDE = _PRELUDE.replace("__LEGACY_SCOPE__", LEGACY_SCOPE_NAME)

# Keep the QObject bridge in a closure. Leaving it on the global object let a
# script discover every Qt slot directly and bypass the authored API surface.
_PRELUDE = (
    "(function(__global, __bridge) {\n\"use strict\";\n"
    + _PRELUDE.replace("this.", "__global.")
    + "\n})(this, this.__bridge);\ndelete this.__bridge;\n"
)

_LOCKDOWN = rf"""
(function(g) {{
  "use strict";
  const max = {MAX_SCRIPT_COLLECTION};
  const intrinsicEval = g.eval;
  const refuseCode = function() {{
    throw new Error("dynamic code generation is unavailable");
  }};
  ["(function*(){{}})", "(async function(){{}})",
   "(async function*(){{}})"].forEach(function(source) {{
    try {{
      const sample = intrinsicEval(source);
      Object.defineProperty(Object.getPrototypeOf(sample), "constructor", {{
        value: refuseCode, writable: false, configurable: false
      }});
    }} catch (_error) {{}}
  }});
  Object.defineProperty(Function.prototype, "constructor", {{
    value: refuseCode, writable: false, configurable: false
  }});
  Object.defineProperty(g, "eval", {{
    value: undefined, writable: false, configurable: false
  }});
  Object.defineProperty(g, "Function", {{
    value: undefined, writable: false, configurable: false
  }});
  if (typeof RegExp === "function") {{
    Object.defineProperty(RegExp.prototype, "exec", {{
      value: refuseCode, writable: false, configurable: false
    }});
    Object.defineProperty(RegExp.prototype, "test", {{
      value: refuseCode, writable: false, configurable: false
    }});
    if (typeof RegExp.prototype.compile === "function")
      Object.defineProperty(RegExp.prototype, "compile", {{
        value: refuseCode, writable: false, configurable: false
      }});
    [Symbol.match, Symbol.matchAll, Symbol.replace, Symbol.search,
     Symbol.split].forEach(function(member) {{
      if (member && typeof RegExp.prototype[member] === "function")
        Object.defineProperty(RegExp.prototype, member, {{
          value: refuseCode, writable: false, configurable: false
        }});
    }});
    Object.defineProperty(g, "RegExp", {{
      value: undefined, writable: false, configurable: false
    }});
  }}
  ["ArrayBuffer", "SharedArrayBuffer", "DataView", "Int8Array",
   "Uint8Array", "Uint8ClampedArray", "Int16Array", "Uint16Array",
   "Int32Array", "Uint32Array", "Float32Array", "Float64Array",
   "BigInt64Array", "BigUint64Array"].forEach(function(name) {{
    if (name in g) Object.defineProperty(g, name, {{
      value: undefined, writable: false, configurable: false
    }});
  }});

  const NativeArray = Array;
  const checkedLength = function(value) {{
    const length = Number(value && value.length || 0);
    if (!Number.isFinite(length) || length > max)
      throw new Error("graphics script collection exceeds " + max + " items");
  }};
  const SafeArray = new Proxy(NativeArray, {{
    apply(target, receiver, args) {{
      if (args.length === 1 && Number(args[0]) > max) checkedLength({{length: args[0]}});
      return Reflect.apply(target, receiver, args);
    }},
    construct(target, args) {{
      if (args.length === 1 && Number(args[0]) > max) checkedLength({{length: args[0]}});
      return Reflect.construct(target, args);
    }}
  }});
  const nativeFrom = NativeArray.from;
  SafeArray.from = function(source, map, receiver) {{
    if (source === null || source === undefined || source.length === undefined)
      throw new Error("graphics scripts require a bounded array-like source");
    checkedLength(source);
    const answer = nativeFrom.call(NativeArray, source, map, receiver);
    checkedLength(answer);
    return answer;
  }};
  Object.defineProperty(NativeArray.prototype, "constructor", {{
    value: SafeArray, writable: false, configurable: false
  }});
  Object.defineProperty(g, "Array", {{
    value: SafeArray, writable: false, configurable: false
  }});
  ["forEach", "map", "filter", "reduce", "reduceRight", "some", "every",
   "find", "findIndex", "flat", "flatMap", "concat", "sort", "reverse",
   "copyWithin", "fill", "includes", "indexOf", "lastIndexOf", "join",
   "slice", "splice", "entries", "keys", "values", "push", "unshift",
   "shift", "pop"].forEach(function(name) {{
    const original = NativeArray.prototype[name];
    if (typeof original !== "function") return;
    Object.defineProperty(NativeArray.prototype, name, {{
      configurable: false, writable: false,
      value: function(...args) {{
        checkedLength(this);
        if (name === "concat") args.forEach(checkedLength);
        if ((name === "push" || name === "unshift")
            && this.length + args.length > max)
          checkedLength({{length: max + 1}});
        if (name === "splice"
            && this.length + Math.max(0, args.length - 2) > max)
          checkedLength({{length: max + 1}});
        const answer = original.apply(this, args);
        checkedLength(this);
        if (answer && typeof answer === "object") checkedLength(answer);
        return answer;
      }}
    }});
  }});
  const nativeRepeat = String.prototype.repeat;
  Object.defineProperty(String.prototype, "repeat", {{
    configurable: false, writable: false,
    value: function(count) {{
      const total = String(this).length * Number(count);
      if (!Number.isFinite(total) || total > max)
        throw new Error("graphics script string exceeds " + max + " characters");
      return nativeRepeat.call(this, count);
    }}
  }});
}})(this);
"""


#: The scripting surface, described once.  The editor's completion, its
#: reference panel and :func:`api_declarations` all read this, so a member
#: added to ``_PRELUDE`` and not here is a member no author can discover.
#: Each entry is ``(name, TypeScript signature, description, has_async)``.
#: ``has_async`` is per member and mirrors ``_PRELUDE`` exactly: only some
#: members have an ``…Async`` twin, and declaring one that does not exist
#: would be the API-level version of a button that does nothing.
#:
#: ``DLSYS.SISWriteAsync`` and ``DL.ShowContextMenuAsync`` exist on the
#: engine but are deliberately NOT declared: both are refusal stubs that
#: always reject or return false.  Advertising them in completion would
#: offer the author a capability this station does not have.
GRAPHICS_API: dict[str, dict] = {
    "DLSYS": {
        "doc": "Process data: read, write, acknowledge.",
        "members": (
            ("Read", "(path: string) => number | string | boolean | null",
             "Current value of a tag path, or null.", True),
            ("CondRead", "(path: string) => CondReadResult",
             "Value with success, status, units and forced flags.", True),
            ("Exists", "(path: string) => boolean",
             "True when the path resolves in this project.", True),
            ("CanWrite", "(path: string) => boolean",
             "True when the checked write service would accept a write.",
             True),
            ("Write", "(path: string, value: unknown) => boolean",
             "Checked operator write; false when refused.", True),
            ("CanPerformFunction",
             "(name: string, path?: string) => boolean",
             "Capability probe for supported read/write/ack operations.", True),
            ("GetAssociatedModuleName", "(path: string) => string",
             "The module leg of a tag path.", True),
            ("AckAlarmsInContainer", "(prefix: string) => number",
             "Acknowledge alarms under a module/path prefix.", False),
            ("AckAllAlarms", "() => number",
             "Acknowledge every alarm this session can see.", False),
        ),
    },
    "DL": {
        "doc": "Display, navigation and session-store services.",
        "members": (
            ("OpenDisplay", "(name: string) => boolean",
             "Navigate the active frame to a published display.", True),
            ("OpenPrimaryDisplay", "(name: string) => boolean",
             "Navigate the primary frame.", True),
            ("OpenFaceplate", "(path?: string) => boolean",
             "Open the faceplate for a module path.", True),
            ("OpenDetail", "(path?: string) => boolean",
             "Open the detail display for a module path.", True),
            ("OpenContext", "(path?: string) => boolean",
             "Open the contextual display for a module path.", True),
            ("DisplayExists", "(name: string) => boolean",
             "True when a published display of that name exists.", True),
            ("MessageBox", "(message: string) => boolean",
             "Record an operator message.", True),
            ("AddStoreItem", "(key: string, value: unknown) => unknown",
             "Put a value in the session-wide store.", False),
            ("GetStoreItem", "(key: string) => unknown",
             "Read a value from the session-wide store.", False),
            ("RemoveStoreItem", "(key: string) => boolean",
             "Remove one session-store entry.", False),
            ("ClearStore", "() => void", "Empty the session store.", False),
            ("GetSelectedTag", "() => string",
             "The tag the operator has selected.", False),
            ("IsTagSelected", "() => boolean",
             "True when a tag is selected.", False),
            ("SelectTag", "(path: string) => string", "Select a tag.", False),
            ("GetBit", "(value: number, bit: number) => boolean",
             "Test one bit of a packed integer.", False),
            ("GetDisplayElementRegion", "(name: string) => Region | null",
             "Scene rectangle of a named element.", False),
            ("GetParentDisplay", "() => string",
             "Name of the host display.", False),
            ("SubmitDiagnosticFailure", "(message: string) => void",
             "Record a diagnostic against this script.", False),
            ("PopupClose", "() => boolean",
             "Close the hosting popup.", False),
            ("PopupMinimize", "() => boolean",
             "Minimize the hosting popup.", False),
            ("PopupRestore", "() => boolean",
             "Restore the hosting popup.", False),
            ("SyncAccess", "(fn: () => unknown) => unknown",
             "Run a callback against the display synchronously.", False),
        ),
    },
    "DLPATH": {
        "doc": "Tag-path helpers.",
        "members": (
            ("Resolve", "(path: string) => string", "Resolve a path.", False),
            ("Combine", "(...parts: string[]) => string",
             "Join path segments with '/'.", False),
        ),
    },
    "ENV": {
        "doc": "Read-only session environment.",
        "members": (
            ("User", "string", "The logged-on user.", False),
            ("DisplayName", "string", "The host display's name.", False),
            ("IsOperator", "boolean", "True in an operator session.", False),
        ),
        "properties": True,
    },
}

#: Dynamic property scopes. These are proxies over the live document, so
#: their members are whatever the display declares rather than a fixed list.
GRAPHICS_SCOPES: tuple[tuple[str, str], ...] = (
    ("This", "Properties of the element the script is attached to."),
    ("Dsp", "Display variables and named element properties."),
    ("Lyt", "Layout variables shared across the workstation."),
    ("Grp", "Group variables."),
    (PVM_SCOPE_PREFIX.rstrip("."), "PVM class properties of this placement."),
    ("GL", "Global standards and shared values."),
)

#: Named in the reference panel so the boundary is discoverable before a
#: refusal, not after one.
RESTRICTED_NOTES: tuple[str, ...] = (
    "No filesystem, network, process, import, eval or DOM access.",
    "Dynamic code constructors and oversized source/collections are refused.",
    "No SIS writes and no controller bypass.",
    f"No for/while/do loops — {_ITERATION_HINT}.",
    "Writes go through the checked operator-write service and may be "
    "refused; test the result.",
    f"A run that exceeds {SCRIPT_TIMEOUT_MS} ms is reported as an overrun.",
)


def api_declarations() -> str:
    """The graphics API as a TypeScript declaration file.

    Authors write TypeScript against this surface, so shipping the
    declarations is what makes the annotations mean something rather than
    being erased against nothing.
    """
    lines = [
        "// Azeo Graphics Designer — restricted graphics scripting API.",
        "// Generated from scripting.GRAPHICS_API; do not hand-edit.",
        "",
        "interface CondReadResult {",
        "  success: boolean;",
        "  value: number | string | boolean | null;",
        "  status: string;",
        "  units: string;",
        "  forced: boolean;",
        "}",
        "",
        "interface Region {",
        "  Left: number; Top: number; Right: number; Bottom: number;",
        "  Width: number; Height: number;",
        "}",
        "",
    ]
    for name, spec in GRAPHICS_API.items():
        lines.append(f"/** {spec['doc']} */")
        lines.append(f"declare const {name}: {{")
        for member, signature, doc, has_async in spec["members"]:
            lines.append(f"  /** {doc} */")
            if spec.get("properties"):
                lines.append(f"  readonly {member}: {signature};")
                continue
            lines.append(f"  {member}: {signature};")
            if has_async:
                arguments, _, returns = signature.partition("=>")
                lines.append(
                    f"  {member}Async: {arguments.strip()} => "
                    f"Promise<{returns.strip()}>;")
        lines.append("};")
        lines.append("")
    lines.append("/** A live property scope over the display document. */")
    lines.append("interface GraphicsScope { [property: string]: any; }")
    lines.append("")
    for name, doc in GRAPHICS_SCOPES:
        lines.append(f"/** {doc} */")
        lines.append(f"declare const {name}: GraphicsScope;")
    lines.append("")
    lines.append("// Restricted by design:")
    lines.extend(f"//   - {note}" for note in RESTRICTED_NOTES)
    return "\n".join(lines) + "\n"


def api_completions() -> tuple[str, ...]:
    """Every completable identifier, for the editor's completer."""
    words: list[str] = []
    for name, spec in GRAPHICS_API.items():
        words.append(name)
        for member, _signature, _doc, has_async in spec["members"]:
            words.append(f"{name}.{member}")
            if has_async:
                words.append(f"{name}.{member}Async")
    words.extend(name for name, _doc in GRAPHICS_SCOPES)
    return tuple(sorted(set(words)))


class GraphicsScriptRuntime:
    """Compile and execute one graphics event script."""

    def __init__(self, timeout_ms: int = SCRIPT_TIMEOUT_MS):
        self.timeout_ms = max(10, int(timeout_ms))

    def validate(self, source: str) -> ScriptResult:
        return self.run(source, ScriptContext(), validate_only=True)

    def run(self, source: str, context: ScriptContext,
            *, validate_only: bool = False) -> ScriptResult:
        text = str(source or "")
        if len(text) > MAX_SCRIPT_SOURCE:
            return ScriptResult(
                False,
                error=f"graphics script exceeds {MAX_SCRIPT_SOURCE} characters")
        # Scan the code, not the prose: strings and comments are blanked
        # first so an operator label may say "waiting for start".
        scanned = _strip_literals(text)
        forbidden = _FORBIDDEN_API.search(scanned)
        if forbidden:
            construct = forbidden.group(1)
            return ScriptResult(False, error=(
                f"{construct} is not available to graphics scripts"))
        loop = _FORBIDDEN_LOOP.search(scanned)
        if loop:
            return ScriptResult(False, error=(
                f"{loop.group(1)} loops are not available to graphics "
                f"scripts — {_ITERATION_HINT}"))
        if _FORBIDDEN_SPREAD.search(scanned):
            return ScriptResult(
                False,
                error=("spread/rest syntax is not available to graphics "
                       "scripts; use the capped array methods"))
        javascript = transpile_typescript(text)
        engine = QJSEngine()
        bridge = _ScriptBridge(context)
        engine.globalObject().setProperty("__bridge", engine.newQObject(bridge))
        bootstrap = engine.evaluate(_PRELUDE, "AzeoGraphicsObjectModel.js")
        if bootstrap.isError():
            return self._error(bootstrap, context)
        lockdown = engine.evaluate(_LOCKDOWN, "AzeoGraphicsLockdown.js")
        if lockdown.isError():
            return self._error(lockdown, context)
        if re.search(r"\bawait\b", javascript):
            return ScriptResult(
                False, error="await is supported for DL/DLSYS Async methods only")
        body = "(function(){\n" + javascript + "\n})"
        compiled = engine.evaluate(body, "GraphicsScript.ts")
        if compiled.isError():
            return self._error(compiled, context, 1)
        if validate_only:
            return ScriptResult(True, diagnostics=tuple(context.diagnostics))
        timer = threading.Timer(
            self.timeout_ms / 1000.0, lambda: engine.setInterrupted(True))
        timer.daemon = True
        timer.start()
        try:
            answer = compiled.call()
        finally:
            timer.cancel()
        timed_out = bool(engine.isInterrupted())
        engine.setInterrupted(False)
        if timed_out:
            return ScriptResult(
                False, error=f"Script exceeded {self.timeout_ms} ms",
                timed_out=True, diagnostics=tuple(context.diagnostics))
        if answer.isError():
            return self._error(answer, context, 1)
        return ScriptResult(True, value=answer.toVariant(),
                            diagnostics=tuple(context.diagnostics))

    @staticmethod
    def _error(value, context: ScriptContext,
               line_offset: int = 0) -> ScriptResult:
        # The script is wrapped in `(function(){` before evaluation, so the
        # engine's line numbers sit one below the author's. An error marker
        # on the wrong line is worse than no marker.
        line = value.property("lineNumber").toInt()
        if line:
            line = max(1, line - line_offset)
        return ScriptResult(False, error=value.toString(), line=line,
                            diagnostics=tuple(context.diagnostics))


__all__ = [
    "GRAPHICS_API", "GRAPHICS_SCOPES", "GraphicsScriptRuntime",
    "RESTRICTED_NOTES", "SCRIPT_TIMEOUT_MS", "ScriptContext",
    "ScriptResult", "api_completions", "api_declarations",
    "transpile_typescript",
]
