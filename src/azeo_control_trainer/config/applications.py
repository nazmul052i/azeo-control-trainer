"""Qt-free product catalog for launchers, logging, Help, and About surfaces."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import Final


class ApplicationId(str, Enum):
    """Stable application identities used in configuration and automation."""

    EXPLORER = "explorer"
    CONTROL_DESIGNER = "control_designer"
    GRAPHICS_DESIGNER = "graphics_designer"
    OPERATOR_STATION = "operator_station"
    SIMULATION_WORKBENCH = "simulation_workbench"
    PA_DESIGNER = "pa_designer"


class ApplicationKind(str, Enum):
    """The operational responsibility of an application shell."""

    ENGINEERING_SHELL = "engineering_shell"
    ENGINEERING = "engineering"
    OPERATIONS = "operations"
    SIMULATION = "simulation"


@dataclass(frozen=True, slots=True)
class ApplicationDescriptor:
    """One authoritative product identity.

    Keeping these values together prevents window titles, console commands,
    logging names, and documentation from inventing slightly different names
    for the same product.
    """

    id: ApplicationId
    title: str
    kind: ApplicationKind
    package: str
    command: str
    surface: str
    log_name: str
    description: str


#: Module Graphics Designer supplies for release-time display verification.
#: Core locates it by name through this catalog so shared code never imports
#: a product; an import failure means that component is not installed.
RELEASE_DISPLAY_VERIFIER: Final[str] = "azeo_control_trainer.azeo_graphics_designer.release_check"

APPLICATIONS: Final[tuple[ApplicationDescriptor, ...]] = (
    ApplicationDescriptor(
        ApplicationId.EXPLORER,
        "Azeo Explorer",
        ApplicationKind.ENGINEERING_SHELL,
        "azeo_control_trainer.azeo_explorer",
        "azeo-explorer",
        "explorer",
        "explorer",
        "Project shell for controllers, modules, displays, deployment, and tools.",
    ),
    ApplicationDescriptor(
        ApplicationId.CONTROL_DESIGNER,
        "Azeo Control Designer",
        ApplicationKind.ENGINEERING,
        "azeo_control_trainer.azeo_control_designer",
        "azeo-control-designer",
        "control",
        "control_designer",
        "Function-block control-module engineering, download, and debugging.",
    ),
    ApplicationDescriptor(
        ApplicationId.GRAPHICS_DESIGNER,
        "Azeo Graphics Designer",
        ApplicationKind.ENGINEERING,
        "azeo_control_trainer.azeo_graphics_designer",
        "azeo-graphics-designer",
        "graphics",
        "graphics_designer",
        "Display, PVM, faceplate, navigation, and deployment authoring.",
    ),
    ApplicationDescriptor(
        ApplicationId.PA_DESIGNER,
        "Azeo PA Designer",
        ApplicationKind.ENGINEERING,
        "azeo_control_trainer.azeo_pa_designer",
        "azeo-pa-designer",
        "procedures",
        "pa_designer",
        "Visual procedure authoring, parameter mapping, validation, and project revisions.",
    ),
    ApplicationDescriptor(
        ApplicationId.OPERATOR_STATION,
        "Azeo Operator Station",
        ApplicationKind.OPERATIONS,
        "azeo_control_trainer.azeo_operator_station",
        "azeo-operator-station",
        "station",
        "operator_station",
        "Published-display process operation, alarms, faceplates, and trends.",
    ),
    ApplicationDescriptor(
        ApplicationId.SIMULATION_WORKBENCH,
        "Azeo Simulation Workbench",
        ApplicationKind.SIMULATION,
        "azeo_control_trainer.azeo_simulation_workbench",
        "azeo-simulation-workbench",
        "simulation",
        "simulation_workbench",
        "Process clock, scenarios, snapshots, playback, and Virtual I/O exercises.",
    ),
)

_BY_ID = MappingProxyType({item.id: item for item in APPLICATIONS})
_BY_SURFACE = MappingProxyType({item.surface: item for item in APPLICATIONS})


def application(value: ApplicationId | str) -> ApplicationDescriptor:
    """Return a product by stable identity, refusing an unknown product."""

    try:
        key = value if isinstance(value, ApplicationId) else ApplicationId(value)
        return _BY_ID[key]
    except (KeyError, ValueError) as error:
        raise ValueError(f"unknown Azeo application: {value!r}") from error


def application_for_surface(surface: str) -> ApplicationDescriptor:
    """Return the product responsible for a launcher surface."""

    try:
        return _BY_SURFACE[surface]
    except KeyError as error:
        raise ValueError(f"unknown Azeo application surface: {surface!r}") from error
