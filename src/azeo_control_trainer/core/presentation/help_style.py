"""Shared HTML treatment for contextual help surfaces."""

HELP_HTML_STYLE = """
<style>
body {
    font-family: Segoe UI, Arial, sans-serif;
    background: #F7F8FA;
    color: #0E3260;
    margin: 12px;
    line-height: 1.5;
}
h2 {
    color: #0E3260;
    border-bottom: 2px solid #3574C4;
    padding-bottom: 6px;
    margin-top: 8px;
}
h3 {
    color: #3574C4;
    margin-top: 18px;
}
table {
    border-collapse: collapse;
    margin: 8px 0;
    width: 100%;
}
th, td {
    border: 1px solid #C0C8D4;
    padding: 5px 8px;
    text-align: left;
    font-size: 9pt;
}
th {
    background: #E8EAF0;
    font-weight: bold;
}
tr:nth-child(even) {
    background: #F0F1F5;
}
pre {
    background: #E8EAF0;
    border: 1px solid #C0C8D4;
    border-radius: 4px;
    padding: 8px 12px;
    font-family: Consolas, monospace;
    font-size: 9pt;
    overflow-x: auto;
}
code {
    background: #E8EAF0;
    padding: 1px 4px;
    border-radius: 3px;
    font-family: Consolas, monospace;
}
.note {
    background: #FFF8E1;
    border-left: 3px solid #F9A825;
    padding: 8px 12px;
    margin: 8px 0;
    font-size: 9pt;
}
ol, ul {
    padding-left: 24px;
}
li {
    margin: 3px 0;
}
</style>
"""



def styled_help_html(body: str, *, theme=None) -> str:
    """Engineering help keeps its style; operator help follows its own station."""
    style = HELP_HTML_STYLE
    if theme is not None:
        from azeo_control_trainer.core.hmi.theme.roles import Role
        from azeo_control_trainer.core.hmi.theme.tokens import THEMES
        p = THEMES[theme]
        # These are the known stylesheet's roles, never arbitrary replacements
        # in authored content or diagrams.
        for literal, role in {
            "#F7F8FA": Role.SURFACE_PANEL, "#0E3260": Role.TEXT,
            "#3574C4": Role.HEADING, "#C0C8D4": Role.LINE,
            "#E8EAF0": Role.SURFACE_FIELD, "#F0F1F5": Role.SURFACE_PANEL_ALT,
            "#FFF8E1": Role.SURFACE_PANEL_ALT, "#F9A825": Role.ALARM_P2,
        }.items():
            style = style.replace(literal, p[role])
        style += f"<style>a {{ color: {p[Role.ACTION]}; }}</style>"
    return style + body
