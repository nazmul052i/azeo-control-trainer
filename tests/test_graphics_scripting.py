"""The restricted graphics scripting boundary, and its authoring surface.

Scripting had no test file of its own; its coverage lived inside
`_smoke_operator.py`. These are the contracts that file could not state:

1. **The scan reads code, not prose.** A forbidden-token search over raw
   source refused `This.text = "Waiting for start"` — an ordinary operator
   label — and blamed a loop the author never wrote.
2. **The loop ban is load-bearing.** `setInterrupted()` from the watchdog
   thread does NOT stop a running loop (measured: a 3.0 s counting loop ran
   to completion with the flag set), so refusing `for`/`while`/`do` is the
   only thing keeping a display handler from freezing the station. Bounded
   iteration is available through the array methods instead, and the
   refusal says so rather than leaving the author stuck.
3. **The declarations describe the runtime.** Completion and the shipped
   `.d.ts` are generated from one table; a member declared there that the
   engine does not have would be a dead entry in an autocomplete list.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(scope="module")
def app():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


@pytest.fixture(scope="module")
def runtime(app):
    from azeo_control_trainer.core.hmi.pvms.scripting import (
        GraphicsScriptRuntime)
    return GraphicsScriptRuntime()


def run(runtime, source: str):
    from azeo_control_trainer.core.hmi.pvms.scripting import ScriptContext
    return runtime.run(source, ScriptContext())


# ------------------------------------------------------- prose vs code
@pytest.mark.parametrize("source,expected", [
    ('var s = "Waiting for start"; return s;', "Waiting for start"),
    ("var s = 'Hold while purging'; return s;", "Hold while purging"),
    ('// wait for the permissive\nreturn "ok";', "ok"),
    ('/* hold while purging */ return "ok";', "ok"),
    ('var n = 2; return `waiting for ${n}`;', "waiting for 2"),
])
def test_english_in_strings_and_comments_is_not_a_loop(runtime, source,
                                                       expected):
    """Defect 1: the label an operator reads is not a control construct."""
    result = run(runtime, source)
    assert result.ok, result.error
    assert result.value == expected


def test_an_identifier_that_merely_contains_a_keyword_is_fine(runtime):
    result = run(runtime, "var format = 5; var forward = 2; "
                          "return format + forward;")
    assert result.ok, result.error
    assert result.value == 7


# ------------------------------------------------------------ the ban
@pytest.mark.parametrize("construct,source", [
    ("while", "while (true) {}"),
    ("for", "for (var i = 0; i < 3; i++) {}"),
    ("do", "do { } while (true);"),
])
def test_loops_are_refused_and_the_refusal_teaches(runtime, construct,
                                                   source):
    """Defect 2. The watchdog cannot stop a loop, so this must hold."""
    result = run(runtime, source)
    assert not result.ok
    assert construct in result.error
    assert "forEach" in result.error, (
        "a refusal that does not name the alternative leaves the author "
        "with no way forward")


def test_a_loop_hidden_in_a_template_interpolation_is_still_refused(runtime):
    """Blanking string content must not blank the code inside ${...}."""
    result = run(runtime, 'var s = `${(function(){ while(true){} })()}`;')
    assert not result.ok
    assert "while" in result.error


@pytest.mark.parametrize("token,source", [
    ("fetch", 'fetch("http://example.com");'),
    ("eval", 'eval("1 + 1");'),
    ("import", 'import x from "y";'),
    ("require", 'require("fs");'),
    ("process", "process.exit(0);"),
    ("XMLHttpRequest", "new XMLHttpRequest();"),
    ("localStorage", 'localStorage.getItem("k");'),
])
def test_capabilities_a_picture_must_never_reach(runtime, token, source):
    result = run(runtime, source)
    assert not result.ok
    assert token in result.error


@pytest.mark.parametrize("source", [
    'return this["ev" + "al"]("1 + 2");',
    'return (() => {})["constr" + "uctor"]("return 3")();',
    'return [].filter["constr" + "uctor"]("return 4")();',
])
def test_computed_dynamic_code_generation_is_locked_down(runtime, source):
    """A token scan is not a boundary: computed property names bypass it."""
    result = run(runtime, source)
    assert not result.ok
    assert result.timed_out is False


def test_script_collections_have_a_hard_resource_limit(runtime):
    result = run(runtime, "return new Array(10001).length;")
    assert not result.ok
    assert "10000" in result.error


def test_unbounded_iterable_spread_is_refused_before_execution(runtime):
    source = ('var source = {[Symbol.iterator]: function(){ return {'
              'next: function(){ return {done:false, value:1}; }}; }}; '
              'return [...source].length;')
    result = run(runtime, source)
    assert not result.ok
    assert "spread" in result.error


def test_view_only_scripts_cannot_acknowledge_alarms(runtime):
    from azeo_control_trainer.core.hmi.pvms.scripting import ScriptContext

    class Registry:
        def acknowledge(self, *_args):
            raise AssertionError("the registry must not be reached")

    context = ScriptContext(alarm_state=Registry(), can_operate=False)
    result = runtime.run("return DLSYS.AckAllAlarms();", context)
    assert result.ok
    assert result.value == 0
    assert "view-only" in " ".join(result.diagnostics)
    capability = runtime.run(
        'return DLSYS.CanPerformFunction("AckAllAlarms");', context)
    assert capability.ok and capability.value is False


def test_station_acknowledgement_entry_points_enforce_view_only(app):
    from types import SimpleNamespace
    from azeo_control_trainer.azeo_operator_station.console import LiveStation

    station = SimpleNamespace(settings=SimpleNamespace(write_authority=False))
    assert LiveStation.acknowledge_all(station) == ()
    assert LiveStation.acknowledge_container(station, "M") == ()
    assert LiveStation.acknowledge_faceplate(station, object()) == ()


def test_scripts_use_the_hosts_checked_write_and_ack_services(runtime):
    from azeo_control_trainer.core.hmi.pvms.scripting import ScriptContext

    class Result:
        success = True
        error = ""

    calls = []
    context = ScriptContext(
        can_write=lambda path: Result(),
        write_value=lambda path, value: calls.append(
            ("write", path, value)) or Result(),
        acknowledge_container=lambda path: calls.append(
            ("ack", path)) or ("one", "two"),
    )
    result = runtime.run(
        'DLSYS.Write("M/PV", 12); '
        'return DLSYS.AckAlarmsInContainer("M");', context)
    assert result.ok and result.value == 2
    assert calls == [("write", "M/PV", 12), ("ack", "M")]


def test_sql_and_sis_remain_unavailable(runtime):
    for source in ('return typeof SQL.QueryAsync;',
                   'return typeof DLSYS.SISWriteAsync;'):
        assert run(runtime, source).value == "function", (
            "the stub exists so a script fails loudly rather than silently")
    capability = run(
        runtime, 'return DLSYS.CanPerformFunction("SISWrite");')
    assert capability.ok and capability.value is False


# ------------------------------------------------- bounded iteration
@pytest.mark.parametrize("source,expected", [
    ('var t = 0; [1,2,3].forEach(function(v){ t += v; }); return t;', 6),
    ('return [1,2,3].map(function(v){ return v*2; }).join(",");', "2,4,6"),
    ('return [1,2,3,4].filter(function(v){ return v > 2; }).length;', 2),
    ('return [1,2,3].reduce(function(a,b){ return a+b; }, 0);', 6),
    ('var t = 0; [1,2].forEach(v => { t += v; }); return t;', 3),
])
def test_bounded_iteration_is_available(runtime, source, expected):
    """The ban costs nothing an author actually needs: an array method
    cannot outrun its own array."""
    result = run(runtime, source)
    assert result.ok, result.error
    assert result.value == expected


# --------------------------------------------------------- diagnostics
def test_the_reported_error_line_is_the_author_s_line(runtime):
    """The script is wrapped before evaluation and type declarations are
    erased; neither may shift the line an error marker lands on."""
    source = ("interface Reading {\n"
              "  value: number;\n"
              "}\n"
              "return notDefined();")
    result = run(runtime, source)
    assert not result.ok
    assert result.line == 4, (
        f"expected the failing statement's own line, got {result.line}")


def test_type_erasure_preserves_line_count(app):
    from azeo_control_trainer.core.hmi.pvms.scripting import (
        transpile_typescript)
    source = "interface A {\n  x: number;\n}\nvar y = 1;"
    assert transpile_typescript(source).count("\n") == source.count("\n")
    assert ": number" not in transpile_typescript("let x: number = 1;")


# ------------------------------------------------- declared vs actual
def test_every_declared_member_exists_on_the_engine(runtime):
    """Defect 3: completion must not offer what the runtime lacks."""
    from azeo_control_trainer.core.hmi.pvms.scripting import GRAPHICS_API

    missing = []
    for obj, spec in GRAPHICS_API.items():
        for member, _signature, _doc, has_async in spec["members"]:
            for name in [member] + ([member + "Async"] if has_async else []):
                kind = run(runtime, f"return typeof {obj}.{name};").value
                wanted = ("string", "boolean") if spec.get("properties") \
                    else ("function",)
                if kind not in wanted:
                    missing.append(f"{obj}.{name} is {kind}")
    assert not missing, missing


def test_the_scopes_resolve(runtime):
    from azeo_control_trainer.core.hmi.pvms.scripting import GRAPHICS_SCOPES

    for name, _doc in GRAPHICS_SCOPES:
        assert run(runtime, f"return typeof {name};").value == "object", name


def test_declarations_and_completions_cover_the_api(app):
    from azeo_control_trainer.core.hmi.pvms.scripting import (
        GRAPHICS_API, api_completions, api_declarations)

    text = api_declarations()
    completions = set(api_completions())
    for obj, spec in GRAPHICS_API.items():
        assert f"declare const {obj}" in text
        for member, _signature, _doc, _async in spec["members"]:
            assert member in text
            assert f"{obj}.{member}" in completions
    # The refusal stubs stay out of the author's completion list.
    assert "DLSYS.SISWriteAsync" not in completions
    assert "DL.ShowContextMenuAsync" not in completions


def test_async_twins_are_declared_only_where_they_exist(runtime):
    from azeo_control_trainer.core.hmi.pvms.scripting import (
        GRAPHICS_API, api_completions)

    completions = set(api_completions())
    for obj, spec in GRAPHICS_API.items():
        for member, _signature, _doc, has_async in spec["members"]:
            present = run(
                runtime, f"return typeof {obj}.{member}Async;").value
            assert (present == "function") == bool(has_async), (
                f"{obj}.{member}Async presence disagrees with the table")
            assert (f"{obj}.{member}Async" in completions) == bool(has_async)


# ------------------------------------------------------ the assistant
def test_the_assistant_offers_completion_reference_and_error_markers(app):
    from azeo_control_trainer.azeo_graphics_designer.script_editor import (
        ScriptAssistantDialog)

    dialog = ScriptAssistantDialog("return notDefined();")
    try:
        assert dialog.editor.completer.completionCount() >= 0
        dialog.editor.completer.setCompletionPrefix("DLSYS.Re")
        assert dialog.editor.completer.completionCount() >= 1, (
            "the completer must resolve real API members")
        assert "declare const DLSYS" in dialog.reference.toPlainText()
        assert dialog.side.count() >= 3
        # Validation compiles; it does not run. An undefined call is a
        # runtime ReferenceError, so Validate passes and Test is what
        # surfaces it — and only Test should mark the line.
        assert dialog.validate_script()
        assert not dialog.editor.extraSelections()
        assert not dialog.test_script()
        assert dialog.editor.extraSelections(), (
            "a failing script must mark its line")
        assert dialog.find_text is not None
    finally:
        dialog.close()
        dialog.deleteLater()


def test_a_valid_script_clears_the_error_marker(app):
    from azeo_control_trainer.azeo_graphics_designer.script_editor import (
        ScriptAssistantDialog)

    dialog = ScriptAssistantDialog("return notDefined();")
    try:
        dialog.test_script()
        assert dialog.editor.extraSelections()
        dialog.editor.setPlainText('return "ok";')
        dialog.test_script()
        assert not dialog.editor.extraSelections()
    finally:
        dialog.close()
        dialog.deleteLater()
