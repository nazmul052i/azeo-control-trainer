"""Opt-in isolated training provider at the product composition boundary."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path

from azeo_control_trainer.core.configuration.documents import ConfigurationError


def attach_training_process(store, package_root):
    from azeo_control_trainer.connectivity.fieldio.local_virtual_io import LocalVirtualIoDriver
    document = json.loads((Path(package_root) / "_project.json").read_text(encoding="utf-8"))
    candidates = []
    def visit(value):
        if isinstance(value, dict):
            if value.get("type") == "local_virtual_io":
                candidates.append(value)
            else:
                for child in value.values():
                    visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)
    visit(document)
    if len(candidates) != 1 or candidates[0].get("provider", {}).get("factory") != "azeoplant.embedding:create_embedded_plant":
        raise ConfigurationError("This training target supports one released in-process Azeo plant provider")
    config = deepcopy(candidates[0])
    provider_root = Path(__file__).resolve().parents[3] / "AzeoPlantSimulator"
    if not (provider_root / "azeoplant/embedding.py").is_file():
        raise ConfigurationError("Install the bundled Azeo training process on this workstation")
    # Resolve installed provider code locally; captured project paths cannot
    # nominate executable code or a network transport on this isolated target.
    config["provider"]["search_paths"] = [str(provider_root)]
    # Mutable process checkpoints are deliberately excluded from engineering
    # releases. Start a local process first; the exercise Start action restores
    # the separately retained, verified training baseline explicitly.
    config["provider"].setdefault("options", {})["snapshot"] = None
    config["snapshot"] = None
    digest = hashlib.sha256()
    for path in sorted((provider_root / "azeoplant").rglob("*.py")):
        digest.update(path.relative_to(provider_root).as_posix().encode())
        digest.update(path.read_bytes().replace(b"\r\n", b"\n"))
    driver = LocalVirtualIoDriver(store, config, package_root)
    driver._configuration_provider_identity = {"factory": config["provider"]["factory"], "implementation_hash": digest.hexdigest()}
    store.field_io_driver = driver
    if not driver.start():
        raise ConfigurationError("Training process did not start: " + driver.last_error)
    return driver
