"""Feature availability at user-driven application handoffs."""
from functools import wraps

from azeo_control_trainer.config.distribution import COMPONENTS, component_available


def requires_component(component):
    def decorate(function):
        @wraps(function)
        def invoke(self, *args, **kwargs):
            if not component_available(component):
                from PySide6.QtWidgets import QMessageBox
                from .headless import is_headless
                if not is_headless():
                    QMessageBox.information(self, "Application not installed",
                        f"{COMPONENTS[component]} is not installed.\n"
                        "Close Azeo, run the same or newer Setup, and select this application.")
                return None
            return function(self, *args, **kwargs)
        invoke.installation_component = component
        return invoke
    return decorate


def action_available(handler):
    component = getattr(handler, "installation_component", None)
    return not component or component_available(component)
