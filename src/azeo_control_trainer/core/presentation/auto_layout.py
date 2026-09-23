"""Plugin-driven default graphics generators.

When a simulation plugin ships no hand-drawn P&ID / APC layout file, the
operator graphics must be built from the *active simulation itself* — its
controllers and CV tags — rather than falling back to another plugin's layout
(which is how the HDA unit ended up showing the Tennessee-Eastman APC).

These builders return ``{"items": [...]}`` layout dicts that ``PidScene.deserialize``
understands. They use only the plugin's public metadata:

* ``plugin.key_controllers``     — loop tags (match the FBD PID ``instance_name``)
* ``plugin.controller_names``    — tag -> human label
* ``plugin.build_tag_registry()``— CV tags (for AI dynamos), optional

Every generated dynamo/controller item carries the real loop tag, so clicking it
opens that loop's faceplate (the FaceplateManager discovers PID blocks by
``instance_name`` from the online strategy runtimes).
"""
from __future__ import annotations


def _controllers(plugin) -> list[str]:
    names = getattr(plugin, "controller_names", {}) or {}
    ctrls = list(getattr(plugin, "key_controllers", []) or [])
    if not ctrls:
        ctrls = list(names.keys())
    return ctrls


def build_apc_layout(plugin) -> dict:
    """Default APC operator view: a DMC status block + one DMC controller
    block per key controller, laid out in a grid."""
    names = getattr(plugin, "controller_names", {}) or {}
    ctrls = _controllers(plugin)

    items: list[dict] = [
        {"type": "dmc_status", "x": 20.0, "y": 20.0, "w": 360, "h": 120},
    ]
    cols, w, h, gx, gy, x0, y0 = 3, 240, 54, 20, 16, 20.0, 160.0
    for i, tag in enumerate(ctrls):
        r, c = divmod(i, cols)
        items.append({
            "type": "dmc_controller",
            "x": x0 + c * (w + gx),
            "y": y0 + r * (h + gy),
            "controller_tag": tag,
            "description": names.get(tag, tag),
            "w": w, "h": h,
        })
    return {"items": items}


def build_pid_layout(plugin) -> dict:
    """Default operator overview: a titled grid of controller dynamos (one per
    key controller). Each dynamo opens its loop faceplate on click."""
    names = getattr(plugin, "controller_names", {}) or {}
    ctrls = _controllers(plugin)

    title = f"{getattr(plugin, 'display_name', 'Process')} — Control Overview"
    items: list[dict] = [
        {"type": "label", "x": 40.0, "y": 12.0, "text": title, "font_size": 14},
    ]
    cols, w, h, gx, gy, x0, y0 = 4, 200, 96, 28, 28, 40.0, 56.0
    for i, tag in enumerate(ctrls):
        r, c = divmod(i, cols)
        items.append({
            "type": "dynamo",
            "x": x0 + c * (w + gx),
            "y": y0 + r * (h + gy),
            "controller_tag": tag,
            "w": w, "h": h,
        })
    return {"items": items}
