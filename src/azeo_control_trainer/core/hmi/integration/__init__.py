"""Versioned adapters between Control Designer and the canonical HMI product.

The embedded HMI remains a visual/behavioural donor during convergence.  New
cross-product data contracts live here and remain Qt-free so engineering and
runtime applications can consume them without importing either UI stack.
"""

from .control_designer_contract import (
    CONTRACT,
    FORMAT,
    SCHEMA_VERSION,
    canonical_json,
    validate_control_designer_bundle,
    write_control_designer_bundle,
)
from .control_designer_exporter import export_pid_hmi_bundle

__all__ = [
    "CONTRACT",
    "FORMAT",
    "SCHEMA_VERSION",
    "canonical_json",
    "export_pid_hmi_bundle",
    "validate_control_designer_bundle",
    "write_control_designer_bundle",
]
