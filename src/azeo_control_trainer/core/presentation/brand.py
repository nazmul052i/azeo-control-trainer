"""Control Designer's reference palette for the engineering applications."""
from dataclasses import dataclass

# Control Designer supplies the reference colors; the other products follow it.
AUTHORING_BLUE = "#004487"
AUTHORING_SELECTION = "#CFE3F5"
AUTHORING_HOVER = "#E3EEF7"

#: Colour families of the colour-with-depth icon set
#: (`core/presentation/icon_set.py`): (light, base, dark) per command class.
#: A family names what a command does, never one command, so a user learns
#: nine colours once. Alarm and process-state roles elsewhere are untouched;
#: the alarm family is used only by alarm commands.
ICON_FAMILIES = {
    "document": ("#7FB2E8", "#2F6FB5", "#1B4B86"),
    "container": ("#F6CF74", "#E0A83A", "#A57316"),
    "run": ("#8AD094", "#3C9A4B", "#22672E"),
    "stop": ("#EC948C", "#C9463D", "#8F231C"),
    "graphics": ("#82D6DA", "#2BA1A6", "#186D71"),
    "procedure": ("#BCA5E6", "#7A5CB8", "#503388"),
    "tool": ("#AEBAC7", "#6B7B8C", "#414D5A"),
    "hardware": ("#6F98CF", AUTHORING_BLUE, "#002B57"),
    "alarm": ("#F7B98A", "#E8792F", "#A44C14"),
}


@dataclass(frozen=True)
class AuthoringColors:
    """Engineering chrome shared by Explorer and both Studio applications."""

    blue: str = AUTHORING_BLUE
    on_blue: str = "#FFFFFF"
    menu_hover: str = "#285F9A"
    menu_pressed: str = "#163F69"
    selection: str = AUTHORING_SELECTION
    hover: str = AUTHORING_HOVER
    page: str = "#ECEEEF"
    pane: str = "#FFFFFF"
    chrome: str = "#F5F6F7"
    chrome_alt: str = "#F4F6F8"
    border: str = "#D8DADC"
    border_light: str = "#E1E5EA"
    text: str = "#2B323B"
    text_secondary: str = "#5A6168"
    text_muted: str = "#687784"
    disabled: str = "#9AA5B4"
    field_border: str = "#CBD2DA"
    field_hover: str = "#809AB5"
    table_alternate: str = "#F7F9FB"
    scroll_handle: str = "#B9C5D2"
    scroll_hover: str = "#859BB2"
    # Engineering feedback, never process alarm or signal-quality roles.
    error: str = "#A52A32"
    success: str = "#246B46"
    warning: str = "#946000"


UI = AuthoringColors()
