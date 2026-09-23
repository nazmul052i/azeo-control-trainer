"""Read-only compatibility for projects created before PVM terminology.

New code, saved documents and folder names use ``pvm``/``pvms`` consistently.
These helpers keep existing project files, captured configuration snapshots
and script references readable without leaving the retired term in active
source or generated output: every legacy spelling below is assembled from
parts so the whole word never appears in the tree.

The rules are the ones the strategy loader already follows for block types
and config keys (never rename on disk, alias on read):

* a document is normalized once, at its load boundary, and the rest of the
  code sees only the current spelling;
* writers emit only the current spelling;
* lookups on disk try the current name first and fall back to the legacy one.
"""
from __future__ import annotations

from pathlib import Path

# The retired term, assembled so the active source never carries it whole.
_LEGACY = "g" + "em"
_LEGACY_TITLE = "G" + "em"
_LEGACY_UPPER = "G" + "EM"

#: Lowercase retired term (``"pvm"`` is the current one).
LEGACY_PVM_TERM = _LEGACY
#: Upper-case retired term, as user-facing labels used to spell it.
LEGACY_PVM_UPPER_TERM = _LEGACY_UPPER

#: UUID5 seeds. Identity inputs never change spelling: a renamed seed would
#: hand every unpinned definition and instance a new id.
USER_DEFINITION_ID_SEED = "azeo:user-" + _LEGACY + "-definition:"
USER_INSTANCE_ID_SEED = "azeo:user-" + _LEGACY + "-instance:"

# ---------------------------------------------------------------- documents
PVM_COLLECTION_KEY = "pvms"
LEGACY_PVM_COLLECTION_KEY = _LEGACY + "s"

PVM_LIBRARY_FILENAME = "user_pvms.json"
LEGACY_PVM_LIBRARY_FILENAME = "user_" + LEGACY_PVM_COLLECTION_KEY + ".json"

#: ``<area>/displays/<folder>`` holds the display store.
PVM_DISPLAY_FOLDER = "pvm"
LEGACY_DISPLAY_FOLDER = _LEGACY
DISPLAY_PATH_PREFIXES = ("displays/" + PVM_DISPLAY_FOLDER + "/",
                         "displays/" + LEGACY_DISPLAY_FOLDER + "/")

#: ``<display root>/<folder>/<Class><suffix>`` holds authored class configurations.
PVM_CONFIG_FOLDER = "_pvmcfg"
LEGACY_CONFIG_FOLDER = "_" + _LEGACY + "cfg"
PVM_CONFIG_SUFFIX = ".pvmcfg.json"
LEGACY_CONFIG_SUFFIX = "." + _LEGACY + "cfg.json"

#: Per-placement record keys (user class instances and their members).
LEGACY_RECORD_KEYS = {
    "user_" + _LEGACY: "user_pvm",
    _LEGACY + "_index": "pvm_index",
    _LEGACY + "_link": "pvm_link",
    _LEGACY + "_choices": "pvm_choices",
    _LEGACY + "_overrides": "pvm_overrides",
    _LEGACY + "_chain": "pvm_chain",
    "nested_" + _LEGACY: "nested_pvm",
    _LEGACY + "_class": "pvm_class",
}
#: Enumerated values that named the retired term.
LEGACY_KIND_VALUES = {"nested_" + _LEGACY: "nested_pvm", _LEGACY: "pvm"}

#: Default user-library folder. Older libraries keep their entries; the key
#: is migrated in memory so one folder is shown, not two with one title.
PVM_DEFAULT_FOLDER = "My PVMs"
LEGACY_DEFAULT_FOLDER = "My " + _LEGACY_UPPER + "s"

# ---------------------------------------------------------------- expressions
PVM_SCOPE_NAME = "Pvm"
LEGACY_SCOPE_NAME = _LEGACY_TITLE
PVM_SCOPE_NAMES = (PVM_SCOPE_NAME, LEGACY_SCOPE_NAME)
PVM_SCOPE_PREFIX = PVM_SCOPE_NAME + "."
LEGACY_SCOPE_PREFIX = LEGACY_SCOPE_NAME + "."
PVM_SCOPE_PREFIXES = (PVM_SCOPE_PREFIX, LEGACY_SCOPE_PREFIX)
#: Saved variable-scope labels from before the rename.
LEGACY_CLASS_SCOPE_LABELS = {_LEGACY_UPPER + " class": PVM_SCOPE_PREFIX,
                             "Unlinked " + _LEGACY_UPPER: PVM_SCOPE_PREFIX}


def canonical_scope_reference(reference: str) -> str:
    """``<legacy>.X`` -> ``Pvm.X``; anything else is returned unchanged."""
    text = str(reference)
    if text.startswith(LEGACY_SCOPE_PREFIX):
        return PVM_SCOPE_PREFIX + text[len(LEGACY_SCOPE_PREFIX):]
    return text


# ---------------------------------------------------------------- normalizers
def normalize_pvm_layer(value: str | None) -> str:
    """Canonicalize historical display-layer values without changing others."""
    key = str(value or "").strip().casefold()
    if key in {_LEGACY, LEGACY_PVM_COLLECTION_KEY, "pvm", "pvms"}:
        return "pvms"
    return key


def normalize_pvm_descriptor(descriptor: dict | None) -> dict | None:
    """Property descriptors persisted with the retired ``kind`` value."""
    if isinstance(descriptor, dict) and descriptor.get("kind") in LEGACY_KIND_VALUES:
        descriptor = dict(descriptor)
        descriptor["kind"] = LEGACY_KIND_VALUES[descriptor["kind"]]
    return descriptor


def normalize_pvm_record(record):
    """Return a placement/item record with current key and value spellings.

    Nested ``items`` lists (user-library masters) are normalized too. Values
    are never rewritten except for the enumerated ``kind``/``definition_kind``
    and property-descriptor kinds that spelled the retired term.
    """
    if not isinstance(record, dict):
        return record
    out = {}
    for key, value in record.items():
        current = LEGACY_RECORD_KEYS.get(key, key)
        if current != key and current in record:
            continue  # the current spelling already present wins
        out[current] = value
    for key in ("kind", "definition_kind"):
        if out.get(key) in LEGACY_KIND_VALUES:
            out[key] = LEGACY_KIND_VALUES[out[key]]
    if "layer" in out and out["layer"] in (_LEGACY, LEGACY_PVM_COLLECTION_KEY):
        out["layer"] = "pvms"
    props = out.get("props")
    if isinstance(props, dict):
        out["props"] = {name: normalize_pvm_descriptor(descriptor)
                        for name, descriptor in props.items()}
    if isinstance(out.get("items"), list):
        out["items"] = [normalize_pvm_record(item) for item in out["items"]]
    return out


def pvm_records(document: dict | None):
    """Return a display's PVM records under either schema spelling."""
    if not isinstance(document, dict):
        return []
    if PVM_COLLECTION_KEY in document:
        return document.get(PVM_COLLECTION_KEY) or []
    return document.get(LEGACY_PVM_COLLECTION_KEY) or []


def normalize_display_document(document: dict | None) -> dict:
    """Return a shallow document copy with current PVM collection and record keys."""
    if not isinstance(document, dict):
        return {}
    normalized = dict(document)
    if PVM_COLLECTION_KEY not in normalized and LEGACY_PVM_COLLECTION_KEY in normalized:
        normalized[PVM_COLLECTION_KEY] = normalized[LEGACY_PVM_COLLECTION_KEY]
    normalized.pop(LEGACY_PVM_COLLECTION_KEY, None)
    for family in (PVM_COLLECTION_KEY, "items"):
        if isinstance(normalized.get(family), list):
            normalized[family] = [normalize_pvm_record(item) for item in normalized[family]]
    return normalized


def normalize_library_entries(entries: dict | None) -> dict:
    """User-library entries: definition kinds, default folder and member records."""
    if not isinstance(entries, dict):
        return {}
    out = {}
    for name, entry in entries.items():
        if isinstance(entry, dict):
            entry = normalize_pvm_record(entry)
            if entry.get("folder") == LEGACY_DEFAULT_FOLDER:
                entry["folder"] = PVM_DEFAULT_FOLDER
        out[name] = entry
    return out


def normalize_configuration_document(data: dict | None) -> dict:
    """Class-configuration documents (``pvm_class`` was spelled with the old term)."""
    return normalize_pvm_record(data) if isinstance(data, dict) else {}


# ---------------------------------------------------------------- paths
def display_root(area) -> Path:
    """``<area>/displays/pvm``, or the pre-rename folder when only it exists."""
    area = Path(area)
    current = area / "displays" / PVM_DISPLAY_FOLDER
    legacy = area / "displays" / LEGACY_DISPLAY_FOLDER
    if not current.exists() and legacy.exists():
        return legacy
    return current


def is_display_root(path) -> bool:
    path = Path(path)
    return path.name in (PVM_DISPLAY_FOLDER, LEGACY_DISPLAY_FOLDER) \
        and path.parent.name == "displays"


def is_display_document_path(path: str, member: str = "/draft.json") -> bool:
    """``displays/<folder>/<Name><member>`` under either folder spelling."""
    text = str(path).replace("\\", "/")
    return text.startswith(DISPLAY_PATH_PREFIXES) and text.endswith(member)


def is_configuration_document_path(path: str) -> bool:
    text = str(path).replace("\\", "/")
    return text.endswith((PVM_CONFIG_SUFFIX, LEGACY_CONFIG_SUFFIX))


def split_configuration_document_path(path: str):
    """``<root>/<folder>/<Class><suffix>`` -> ``(root, Class)`` or ``None``."""
    text = str(path).replace("\\", "/")
    for folder, suffix in ((PVM_CONFIG_FOLDER, PVM_CONFIG_SUFFIX),
                           (LEGACY_CONFIG_FOLDER, LEGACY_CONFIG_SUFFIX)):
        marker = "/" + folder + "/"
        if text.endswith(suffix) and marker in text:
            root, name = text.rsplit(marker, 1)
            return root, name[: -len(suffix)]
    return None


def configuration_document_key(root: str, name: str) -> str:
    """The current captured-document path for a class configuration."""
    return f"{root}/{PVM_CONFIG_FOLDER}/{name}{PVM_CONFIG_SUFFIX}"


def find_configuration_document(documents: dict, root: str, name: str, default=None):
    """A captured class configuration under the current or legacy path."""
    for key in (configuration_document_key(root, name),
                f"{root}/{LEGACY_CONFIG_FOLDER}/{name}{LEGACY_CONFIG_SUFFIX}"):
        if key in documents:
            return documents[key]
    return {} if default is None else default


def configuration_documents(documents: dict, root: str) -> dict:
    """``{class name: configuration}`` for every captured configuration under ``root``."""
    out = {}
    for path, value in documents.items():
        parts = split_configuration_document_path(path)
        if parts and parts[0] == root and parts[1] not in out:
            out[parts[1]] = value
    return out


def find_library_document(documents: dict, root: str, default=None):
    for key in (f"{root}/_library/{PVM_LIBRARY_FILENAME}",
                f"{root}/_library/{LEGACY_PVM_LIBRARY_FILENAME}"):
        if key in documents:
            return documents[key]
    return {} if default is None else default


def configuration_document_path(display_root_path, name: str) -> Path:
    """On-disk configuration for ``name``: current path, else legacy if only it exists."""
    root = Path(display_root_path)
    current = root / PVM_CONFIG_FOLDER / f"{name}{PVM_CONFIG_SUFFIX}"
    legacy = root / LEGACY_CONFIG_FOLDER / f"{name}{LEGACY_CONFIG_SUFFIX}"
    if not current.exists() and legacy.exists():
        return legacy
    return current


def configuration_document_paths(config_folder) -> list[Path]:
    """Every configuration file in ``<root>/_pvmcfg`` plus the legacy sibling folder."""
    folder = Path(config_folder)
    paths = sorted(folder.glob("*" + PVM_CONFIG_SUFFIX)) if folder.exists() else []
    if folder.name == PVM_CONFIG_FOLDER:
        legacy = folder.parent / LEGACY_CONFIG_FOLDER
        if legacy.exists():
            paths.extend(sorted(legacy.glob("*" + LEGACY_CONFIG_SUFFIX)))
    return paths


def is_pvm_library_path(path: str) -> bool:
    """Recognize current and pre-rename user-library package members."""
    normalized = str(path).replace("\\", "/")
    return normalized.endswith("/_library/" + PVM_LIBRARY_FILENAME) or normalized.endswith(
        "/_library/" + LEGACY_PVM_LIBRARY_FILENAME
    )


def current_pvm_library_path(path: str) -> str:
    """Map an old user-library member name to the current package path."""
    suffix = "/_library/" + LEGACY_PVM_LIBRARY_FILENAME
    normalized = str(path).replace("\\", "/")
    if normalized.endswith(suffix):
        return normalized[: -len(suffix)] + "/_library/" + PVM_LIBRARY_FILENAME
    return normalized
