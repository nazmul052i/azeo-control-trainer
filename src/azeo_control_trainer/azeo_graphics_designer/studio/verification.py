"""One structured Graphics Designer verifier shared by Verify and Publish.

The canvas historically returned plain strings while Publish repeated only a
small path check.  That allowed a display with a broken action, data element,
class interface, or connector to pass the release gate.  This adapter keeps
the established detailed checks in one place and attaches the severity model
used by :mod:`pvms.publishing`.
"""
from __future__ import annotations

from azeo_control_trainer.core.hmi.compatibility import PVM_SCOPE_PREFIXES

from azeo_control_trainer.core.hmi.pvms.publishing import ERROR, WARNING, Finding
from azeo_control_trainer.core.hmi.pvms.rendering.items import item_document_data


def _resolver(studio):
    """Return a side-effect-free live-path truth function when available."""
    source = getattr(getattr(studio, "engine", None), "_source", None)
    class_name = getattr(studio, "edited_user_class_name", lambda: "")()
    config = getattr(studio, "_config_for_name", lambda _name: None)(
        class_name) if class_name else None
    if source is None and config is None:
        return None
    from azeo_control_trainer.core.hmi.binding.result import UNRESOLVED

    def resolves(path) -> bool:
        path = str(path).strip()
        if path.startswith("@procedure/"):
            from azeo_control_trainer.core.procedures.hmi import FIELDS
            if path.rpartition("/")[2] not in FIELDS:
                return False
        if config is not None:
            if path.startswith("Standard."):
                return True
            if path.startswith(PVM_SCOPE_PREFIXES):
                root = path[4:].split(".", 1)[0]
                return config.property(root) is not None
            if "{" in path and "}" in path:
                import string
                try:
                    fields = {field for _literal, field, _format, _convert
                              in string.Formatter().parse(path) if field}
                except ValueError:
                    return False
                return bool(fields) and all(
                    config.property(field) is not None for field in fields)
        if path.startswith("@procedure/"):
            from azeo_control_trainer.core.procedures.hmi import split_path
            try:
                split_path(path)
                return True
            except ValueError:
                return False
        return source is not None and source.read(path) is not UNRESOLVED

    return resolves


def _catalog_names(studio) -> tuple[tuple[str, ...], tuple[str, ...]]:
    displays = set()
    store = getattr(studio, "store", None)
    root = getattr(store, "root", None)
    if root is not None and root.exists():
        displays.update(path.name for path in root.iterdir()
                        if path.is_dir() and (path / "draft.json").exists())
    display = getattr(studio, "display", None)
    if display is not None and getattr(display, "name", ""):
        displays.add(display.name)
    faceplates = set()
    library_getter = getattr(studio, "user_library", None)
    if callable(library_getter):
        try:
            faceplates.update(library_getter().names(
                definition_kind="faceplate"))
            faceplates.update(library_getter().names(definition_kind="detail"))
        except (AttributeError, OSError, TypeError):
            pass
    return tuple(sorted(displays)), tuple(sorted(faceplates))


def _iter_document_findings(studio):
    """Checks that historically fell between the canvas and release gate."""
    from azeo_control_trainer.core.hmi.pvms.elements import (
        DATA_ELEMENT_KINDS, WRITE_VALUE, UserEntry, actions_of,
    )
    from azeo_control_trainer.core.hmi.pvms.properties import described_properties
    from .editor_models import (
        actions_issues, data_binding_issues, data_issues,
        property_descriptor_issues, user_entry_issues,
    )

    statics = list(getattr(studio, "_static_items", lambda: ())())
    pvms = list(getattr(studio, "_items", lambda: ())())
    pipes = list(getattr(studio, "_pipe_items", lambda: ())())
    resolver = _resolver(studio)
    display_names, faceplate_names = _catalog_names(studio)
    findings: list[Finding] = []

    # Duplicate identity silently overwrote connector/diff dictionaries.
    seen: dict[str, int] = {}
    for item in (*pvms, *statics, *pipes):
        yield None
        pvm = getattr(item, "pvm", None)
        data = item_document_data(item)
        identity = str(getattr(pvm, "id", "")
                       or data.get("id", "")).strip()
        if identity:
            seen[identity] = seen.get(identity, 0) + 1
    for identity, count in sorted(seen.items()):
        if count > 1:
            findings.append(Finding(
                ERROR, f"duplicate object id: {identity}", identity))

    for item in statics:
        yield None
        data = item_document_data(item)
        identity = str(data.get("id", "item"))
        for prop, descriptor in described_properties(data).items():
            for issue in property_descriptor_issues(
                    descriptor, field=f"props.{prop}", resolver=resolver):
                findings.append(Finding(
                    issue.severity, issue.message, identity))
        if data.get("kind") in DATA_ELEMENT_KINDS:
            for issue in (*data_issues(data),
                          *data_binding_issues(data, resolver)):
                findings.append(Finding(
                    issue.severity, issue.message, identity))
        if data.get("kind") == "user_entry":
            for issue in user_entry_issues(
                    UserEntry.from_dict(data.get("entry", {}))):
                findings.append(Finding(
                    issue.severity, issue.message, identity))
        if data.get("kind") == "display_link":
            target = str(data.get("target", "")).strip()
            if target and display_names and target not in display_names:
                findings.append(Finding(
                    ERROR, f"display does not exist: {target}", identity))
        rows = list(data.get("actions", ()))
        for issue in actions_issues(
                rows, display_names=display_names,
                faceplate_names=faceplate_names):
            findings.append(Finding(
                issue.severity, issue.message, identity))
        can_write = getattr(getattr(studio, "engine", None),
                            "can_write", None)
        if callable(can_write):
            for action in actions_of(data):
                if action.kind != WRITE_VALUE or not action.target:
                    continue
                result = can_write(action.target)
                if not getattr(result, "success", False):
                    findings.append(Finding(
                        ERROR,
                        f"write target {action.target}: "
                        f"{getattr(result, 'error', 'not writable')}",
                        identity))

    display = getattr(studio, "display", None)
    if display is not None:
        for event, rows in getattr(display, "events", {}).items():
            # Display events use the same persisted Action grammar.  Preserve
            # the location because there is no canvas object to select.
            normalized = []
            for row in rows:
                candidate = dict(row)
                # Display lifecycle keys (open/close) are the event source;
                # Action.event's mouse grammar is not applicable here.
                candidate["event"] = "click"
                normalized.append(candidate)
            for issue in actions_issues(
                    normalized, display_names=display_names,
                    faceplate_names=faceplate_names):
                findings.append(Finding(
                    issue.severity, issue.message, f"display.{event}"))
    return findings


def _severity(message: str) -> str:
    """Classify non-runtime authoring advice without weakening real errors."""
    text = message.casefold()
    if text.startswith("outside display page:") \
            or text.startswith("text below 7 pt minimum:"):
        return WARNING
    return ERROR


def _item_from_message(message: str) -> str:
    if ": " not in message:
        return ""
    return message.rsplit(": ", 1)[-1].strip()


def _class_master_messages(studio, messages: list[str]) -> list[str]:
    """Remove only validation failures caused by the master edit projection.

    Class bindings are held in the library while the reusable layout is
    edited, so a valid ``Pvm.ControlTag`` Data Link intentionally appears
    pathless on that temporary canvas.  Suppress the generic empty-path error
    only when *every* projected pathless Data Link has an authoritative class
    binding; a genuinely unconfigured sibling must keep the error visible.
    """
    class_name = getattr(studio, "edited_user_class_name", lambda: "")()
    if not class_name or "Data Link: no data source" not in messages:
        return messages
    try:
        entry = studio.user_library().entries.get(class_name, {})
    except (AttributeError, OSError, TypeError):
        return messages
    stored = list(entry.get("items", ()))
    by_source = {str(row.get("source_element_id")): row for row in stored
                 if row.get("source_element_id")}
    by_id = {str(row.get("id")): row for row in stored if row.get("id")}
    projected = [item.data for item in studio._static_items()
                 if item.data.get("kind") == "datalink"
                 and not str(item.data.get("path", "")).strip()]
    if not projected:
        return messages
    for data in projected:
        original = by_source.get(str(data.get("source_element_id"))) \
            or by_id.get(str(data.get("id"))) or {}
        value = original.get("path")
        if not (isinstance(value, str)
                and value.startswith((*PVM_SCOPE_PREFIXES, "Standard."))):
            return messages
    return [message for message in messages
            if message != "Data Link: no data source"]


def iter_findings_for_studio(studio):
    """Run the complete canvas and class-interface preflight."""
    if callable(getattr(studio, "iter_validation", None)):
        messages = yield from studio.iter_validation()
    else:
        messages = list(studio.validate())
    messages = _class_master_messages(studio, messages)
    found = [Finding(_severity(message), message,
                     _item_from_message(message))
             for message in messages]
    found.extend((yield from _iter_document_findings(studio)))
    from azeo_control_trainer.core.hmi.pvms.visual_quality import iter_visual_findings
    if getattr(studio, "canvas", None) is not None and getattr(studio, "display", None) is not None:
        found.extend((yield from iter_visual_findings(
            studio.canvas.scene(), studio.display,
            getattr(studio, "quality_theme", "silver"), getattr(studio, "quality_viewport", None))))
    if not studio.edited_user_class_name() and callable(getattr(studio, "_document", None)):
        from azeo_control_trainer.core.hmi.pvms.procedure_assemblies import procedure_library, iter_revision_issues
        procedure_items = []
        # Serializing and cloning the entire canvas just to discover that it
        # has no PA references made one automatic-check slice stall on large
        # displays. Inspect retained rows cooperatively, then load each revision.
        for item in studio._static_items():
            yield None
            data = item_document_data(item)
            if data.get("user_pvm") or "@procedure/" in str(data):
                procedure_items.append(data)
        for ref, error in iter_revision_issues({"items": procedure_items, "events": studio.display.events}, {}, procedure_library(studio.store.root), studio._config_for_name):
            if error:
                found.append(Finding(ERROR, error, "@procedure/" + ref))
            yield None
    if getattr(getattr(studio, "display", None), "commissioning", None):
        from azeo_control_trainer.core.hmi.pvms.engineering import document_digest
        digest = document_digest(studio._document())
        for case in studio.display.commissioning:
            name = str(case.get("name", "Commissioning case"))
            if case.get("check_digest") != digest:
                found.append(Finding(WARNING, f"{name}: commissioning checks need to be rerun", "commissioning"))
            elif case.get("check_passed") is False:
                found.append(Finding(ERROR, f"{name}: {case.get('check_message', 'commissioning check failed')}", "commissioning"))
            if not case.get("reviewed") or case.get("review_digest") != digest:
                found.append(Finding(WARNING, f"{name}: visual review required for this display revision", "commissioning"))
    class_name = studio.edited_user_class_name()
    if class_name:
        config = studio._config_for_name(class_name)
        if config is None:
            found.append(Finding(
                ERROR, "no saved reusable-class interface", class_name))
        else:
            for issue in config.issues():
                severity = str(getattr(issue, "severity", ERROR)).lower()
                if severity not in ("informational", "warning", "error"):
                    severity = ERROR
                found.append(Finding(
                    severity, str(issue),
                    str(getattr(issue, "location", class_name))))
    # Stable output keeps the Problems pane and publish refusal deterministic.
    unique = {(one.severity, one.message, one.item): one for one in found}
    return tuple(unique[key] for key in sorted(unique))


def findings_for_studio(studio) -> tuple[Finding, ...]:
    from azeo_control_trainer.core.hmi.pvms.visual_quality import drain
    return drain(iter_findings_for_studio(studio))


__all__ = ["findings_for_studio", "iter_findings_for_studio"]
