"""Command-line entry point for the offline canonical-HMI adapter."""

from __future__ import annotations

import argparse
from pathlib import Path

from .control_designer_contract import write_control_designer_bundle
from .control_designer_exporter import export_pid_hmi_bundle


def main(argv: list[str] | None = None) -> int:
    """Export a deterministic PID binding bundle for commissioning or CI."""

    from azeo_control_trainer.config.logging_config import (
        application_logger, setup_logging,
    )

    setup_logging("launcher")
    application_logger("launcher", "hmi_export").info(
        "HMI export command started")

    parser = argparse.ArgumentParser(
        description="Export Control Designer PID metadata for Azeo HMI Studio"
    )
    parser.add_argument("area", type=Path, help="strategy area containing _project.json")
    parser.add_argument("output", type=Path, help="destination .json bundle")
    parser.add_argument(
        "--module",
        action="append",
        default=[],
        help="configured module name; repeat to export more than one",
    )
    options = parser.parse_args(argv)
    bundle = export_pid_hmi_bundle(
        options.area,
        modules=options.module or None,
    )
    write_control_designer_bundle(options.output, bundle)
    print(
        f"Exported {len(bundle['bindings'])} PID binding set(s) to {options.output} "
        f"[{bundle['bundleHash']}]"
    )
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised as a product command
    raise SystemExit(main())
