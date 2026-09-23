"""Configuration-driven loading for in-process field-I/O providers.

The engineering project names a factory; the trainer does not import a
simulator package, know its checkout location, or construct its plant.  The
factory returns a small session/adapter object and this module normalises its
lifecycle.  That keeps Local Virtual I/O on the field side of
``SharedDataStore`` just like Modbus and OPC UA.
"""
from __future__ import annotations

import importlib
import inspect
import logging
import os
import sys
import threading
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from string import Template
from typing import Any

from azeo_control_trainer.config.distribution import read_installation
from azeo_control_trainer.config.paths import project_root, workspace_root

log = logging.getLogger("fieldio.provider")


@dataclass
class _SearchPathLease:
    references: int
    inserted: bool


# ``sys.path`` and ``sys.modules`` are process-wide. Provider sessions may be
# created/stopped on different controller workers, so path mutation and the
# matching import/provenance check must be one atomic operation.
_PROVIDER_IMPORT_LOCK = threading.RLock()
_SEARCH_PATH_LEASES: dict[str, _SearchPathLease] = {}


class ProviderConfigurationError(ValueError):
    """The project does not describe a usable provider factory."""


def _expand(value: Any, variables: Mapping[str, str]) -> Any:
    """Expand environment/project variables throughout provider arguments."""
    if isinstance(value, str):
        return os.path.expandvars(Template(value).safe_substitute(variables))
    if isinstance(value, Mapping):
        return {str(key): _expand(item, variables)
                for key, item in value.items()}
    if isinstance(value, list):
        return [_expand(item, variables) for item in value]
    if isinstance(value, tuple):
        return tuple(_expand(item, variables) for item in value)
    return value


def _module_origin(module: Any) -> Path | None:
    raw = getattr(module, "__file__", None)
    if not raw:
        spec = getattr(module, "__spec__", None)
        raw = getattr(spec, "origin", None)
    if not raw or str(raw) in {"built-in", "frozen"}:
        return None
    return Path(str(raw)).resolve()


def _inside(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _search_path_candidates(path: Path, project_dir: Path) -> list[Path]:
    """Resolve a provider path in the writable project and its installed twin.

    Setup keeps executable provider code in the versioned application bundle,
    while it copies editable projects to the per-user workspace.  A shipped
    project's historical relative path must therefore be tried from the
    matching immutable project directory as well.  Custom projects have no
    bundle twin and retain the ordinary project-relative behavior.
    """
    if path.is_absolute():
        return [path.resolve()]
    candidates = [(project_dir / path).resolve()]
    bundle = project_root().resolve()
    workspace = workspace_root().resolve()
    if bundle == workspace or not read_installation(bundle).installed:
        return candidates
    try:
        relative_project = project_dir.relative_to(workspace)
    except ValueError:
        return candidates
    mirrored = (bundle / relative_project / path).resolve()
    # The fallback is for immutable application content only. A crafted path
    # must not turn the bundle mirror into a second route to arbitrary code.
    if _inside(mirrored, bundle) and mirrored not in candidates:
        candidates.append(mirrored)
    return candidates


def _factory(reference: str, search_roots: Sequence[str] = ()):
    try:
        module_name, attribute = reference.split(":", 1)
    except ValueError as error:
        raise ProviderConfigurationError(
            "provider.factory must use 'package.module:callable'") from error
    if not module_name or not attribute:
        raise ProviderConfigurationError(
            "provider.factory must name both a module and callable")
    already_imported = module_name in sys.modules
    try:
        module = importlib.import_module(module_name)
    except ImportError as error:
        raise ProviderConfigurationError(
            f"cannot load provider factory {reference!r}: {error}") from error
    roots = [Path(root).resolve() for root in search_roots]
    if roots:
        origin = _module_origin(module)
        if origin is None or not any(_inside(origin, root) for root in roots):
            state = "already-imported " if already_imported else ""
            root_text = ", ".join(repr(str(root)) for root in roots)
            raise ProviderConfigurationError(
                f"{state}provider module {module_name!r} resolved from "
                f"{str(origin)!r}, outside configured search_paths "
                f"[{root_text}]; provider roots cannot share one cached "
                "module name"
            )
    try:
        answer = getattr(module, attribute)
    except AttributeError as error:
        raise ProviderConfigurationError(
            f"cannot load provider factory {reference!r}: {error}") from error
    if not callable(answer):
        raise ProviderConfigurationError(
            f"provider factory {reference!r} is not callable")
    return answer


def _acquire_search_path(path: str) -> None:
    """Lease one provider path without stealing another session's entry."""
    with _PROVIDER_IMPORT_LOCK:
        lease = _SEARCH_PATH_LEASES.get(path)
        if lease is not None:
            lease.references += 1
            if path not in sys.path:
                # Be resilient to unrelated code replacing ``sys.path`` while
                # a provider is live. The loader owns this replacement entry.
                sys.path.insert(0, path)
                lease.inserted = True
            return
        inserted = path not in sys.path
        if inserted:
            sys.path.insert(0, path)
        _SEARCH_PATH_LEASES[path] = _SearchPathLease(1, inserted)


def _release_search_paths(paths: Sequence[str]) -> None:
    with _PROVIDER_IMPORT_LOCK:
        for path in reversed(paths):
            lease = _SEARCH_PATH_LEASES.get(path)
            if lease is None:
                continue
            lease.references -= 1
            if lease.references > 0:
                continue
            if lease.inserted:
                try:
                    sys.path.remove(path)
                except ValueError:
                    pass
            _SEARCH_PATH_LEASES.pop(path, None)


def _unique_objects(*objects: Any) -> list[Any]:
    result: list[Any] = []
    identities: set[int] = set()
    for obj in objects:
        if obj is None or id(obj) in identities:
            continue
        identities.add(id(obj))
        result.append(obj)
    return result


def _create_product(create, args: list[Any], options: Mapping[str, Any],
                    call_style: str = "auto") -> Any:
    """Call a provider without using a caught ``TypeError`` as introspection.

    A factory may expose ordinary keyword options or one configuration
    mapping.  ``Signature.bind`` chooses before execution, so a TypeError
    raised *inside* a factory is never mistaken for a signature mismatch and
    the factory is never accidentally run twice.
    """
    style = str(call_style or "auto").strip().lower()
    if style not in {"auto", "kwargs", "mapping"}:
        raise ProviderConfigurationError(
            "provider.call_style must be auto, kwargs, or mapping")
    if args or style == "kwargs":
        return create(*args, **dict(options))
    if style == "mapping":
        return create(dict(options))
    try:
        signature = inspect.signature(create)
    except (TypeError, ValueError):
        return create(**dict(options))
    try:
        signature.bind(**dict(options))
    except TypeError:
        try:
            signature.bind(dict(options))
        except TypeError as error:
            raise ProviderConfigurationError(
                "provider options match neither keyword arguments nor one "
                "configuration mapping") from error
        return create(dict(options))
    return create(**dict(options))


@dataclass
class ProviderSession:
    """Normalised provider result with an explicit transport and lifecycle."""

    product: Any
    transport: Any
    lifecycle: list[Any] = field(default_factory=list)
    search_paths: list[str] = field(default_factory=list)
    _search_paths_are_leased: bool = field(default=False, repr=False)
    _started: list[Any] = field(default_factory=list, init=False)

    def __post_init__(self) -> None:
        if self._search_paths_are_leased or not self.search_paths:
            return
        # Direct construction historically meant that the caller had inserted
        # these paths and expected this session to remove them. Adopt that
        # ownership into the same lease table so even a directly constructed
        # session cannot pull a path out from under a loader-created session.
        with _PROVIDER_IMPORT_LOCK:
            for path in self.search_paths:
                lease = _SEARCH_PATH_LEASES.get(path)
                if lease is None:
                    _SEARCH_PATH_LEASES[path] = _SearchPathLease(1, True)
                else:
                    lease.references += 1
        self._search_paths_are_leased = True

    def start(self) -> None:
        """Start each distinct provider component once."""
        if self._started:
            return
        # Adapters become ready before an engine starts publishing.  A factory
        # returning one composite object is started only once.
        try:
            for component in _unique_objects(self.transport, *self.lifecycle):
                start = getattr(component, "start", None)
                # Record ownership before invoking foreign code.  A start
                # method may claim an output lease and then raise; omitting
                # that component from the unwind list would strand the claim
                # forever.
                self._started.append(component)
                if callable(start):
                    start()
        except Exception:
            try:
                self.stop()
            except Exception:  # noqa: BLE001
                # Preserve the startup exception as the actionable cause;
                # stop() already logged every unwind failure.
                log.exception("Provider startup unwind failed")
            raise

    def stop(self) -> None:
        """Stop in reverse order and remove only paths this session inserted."""
        failures: list[Exception] = []
        while self._started:
            component = self._started.pop()
            stop = getattr(component, "stop", None)
            if callable(stop):
                try:
                    stop()
                except Exception as error:  # noqa: BLE001
                    # One broken runtime must not strand its adapter, output
                    # lease, or a provider search path.  Continue unwinding,
                    # then report the first failure to the caller.
                    failures.append(error)
                    log.exception("Provider component stop failed")
        try:
            _release_search_paths(self.search_paths)
        finally:
            self.search_paths.clear()
        if failures:
            raise RuntimeError(
                f"{len(failures)} provider component(s) failed to stop"
            ) from failures[0]


def load_provider(config: Mapping[str, Any], project_dir: Path) -> ProviderSession:
    """Load the project-declared provider and return its normalised session.

    Supported factory results are deliberately small but accommodate a plain
    LocalAdapter as well as a composed plant runtime:

    * an adapter/session exposing ``read`` and ``write``;
    * an object with ``adapter`` or ``transport`` and optional ``runtime``;
    * a mapping with those same keys.
    """
    provider = config.get("provider") or {}
    if not isinstance(provider, Mapping):
        raise ProviderConfigurationError("field_io.provider must be an object")

    project_dir = Path(project_dir).resolve()
    variables = {
        "PROJECT_DIR": str(project_dir),
        "AREA_DIR": str(project_dir),
        "CONFIG_DIR": str(project_dir),
    }
    expanded = _expand(dict(provider), variables)
    reference = str(expanded.get("factory") or "").strip()
    if not reference:
        raise ProviderConfigurationError("field_io.provider.factory is required")

    leased: list[str] = []
    skipped: list[str] = []
    paths = expanded.get("search_paths") or []
    if isinstance(paths, (str, bytes)) or not isinstance(paths, Sequence):
        raise ProviderConfigurationError("provider.search_paths must be a list")

    try:
        # Keep path ordering, import, and provenance validation atomic. A
        # second controller cannot put another checkout ahead of this one in
        # ``sys.path`` between acquisition and ``import_module``.
        with _PROVIDER_IMPORT_LOCK:
            for raw in reversed(list(paths)):
                text = str(raw)
                if "${" in text or ("%" in text and os.name == "nt"):
                    skipped.append(f"unresolved search path {text!r}")
                    continue
                for candidate in _search_path_candidates(Path(text), project_dir):
                    resolved = str(candidate)
                    if not candidate.is_dir():
                        skipped.append(f"missing search path {resolved!r}")
                        continue
                    if resolved in leased:
                        continue
                    _acquire_search_path(resolved)
                    leased.append(resolved)
            if paths and not leased:
                detail = "; ".join(reversed(skipped))
                raise ProviderConfigurationError(
                    "provider.search_paths contains no existing directory"
                    + (f" ({detail})" if detail else "")
                )
            try:
                create = _factory(reference, leased)
            except ProviderConfigurationError as error:
                detail = "; ".join(reversed(skipped))
                raise ProviderConfigurationError(
                    f"{error}" + (f" ({detail})" if detail else "")) from error
        args = expanded.get("args") or []
        options = expanded.get("options") or {}
        if isinstance(args, (str, bytes)) or not isinstance(args, Sequence):
            raise ProviderConfigurationError("provider.args must be a list")
        if not isinstance(options, Mapping):
            raise ProviderConfigurationError("provider.options must be an object")
        product = _create_product(
            create,
            list(args),
            options,
            str(expanded.get("call_style") or "auto"),
        )
        if product is None:
            raise ProviderConfigurationError(
                f"provider factory {reference!r} returned None")

        if isinstance(product, Mapping):
            transport = product.get("transport") or product.get("adapter")
            runtime = product.get("runtime")
        else:
            transport = (getattr(product, "transport", None)
                         or getattr(product, "adapter", None))
            runtime = getattr(product, "runtime", None)
        transport = transport or product
        if not callable(getattr(transport, "read", None)) \
                or not callable(getattr(transport, "write", None)):
            raise ProviderConfigurationError(
                "provider transport must expose read(tag) and write(tag, value)")
        lifecycle = _unique_objects(runtime, product)
        if skipped:
            log.debug("Provider %s ignored path candidates: %s",
                      reference, "; ".join(reversed(skipped)))
        return ProviderSession(
            product,
            transport,
            lifecycle,
            leased,
            _search_paths_are_leased=True,
        )
    except Exception:
        _release_search_paths(leased)
        raise
