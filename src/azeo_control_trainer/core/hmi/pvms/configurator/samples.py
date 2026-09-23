"""Three executable valve configuration examples.

The designer and configuration tests share these working documents:
Orientation's one-column grid, the extended Orientation
whose Custom row references ``Pvm.CustomAngle`` and whose
``EnableCustom`` column gates that property's Presence, the Link
selection swapping FB/Parameter/Scale together, and Port present only
when ``ValveType.threeWay`` is true.
"""
from __future__ import annotations

from .model import (
    ALWAYS, WHEN_TRUE, PvmConfiguration, PvmProperty, Option,
    Presence, PropertyGroup,
)


def _sel(name, columns, rows, default, title="", description="",
         presence=None):
    return PvmProperty(
        name=name, ptype="Selection", title=title,
        description=description, default=default,
        columns=list(columns),
        options=[Option(r[0], list(r[1:])) for r in rows],
        presence=presence or Presence())


def _show_hide(name, title, default="Show"):
    return _sel(name, ["Yes"], [("Show", "True"), ("Hide", "False")],
                default, title=title)


def hp_c_valve() -> PvmConfiguration:
    orientation = _sel(
        "Orientation", ["BodyRot"],
        [("Up", "0"), ("Down", "180"), ("Left", "270"),
         ("Right", "90")], "Up")
    orientation_ext = _sel(
        "OrientationExtended", ["BodyRot", "EnableCustom"],
        [("Up", "0", "False"), ("Down", "180", "False"),
         ("Left", "270", "False"), ("Right", "90", "False"),
         ("Custom", "Pvm.CustomAngle", "True")], "Up",
        title="Orientation (extended)")
    custom_angle = PvmProperty(
        name="CustomAngle", ptype="Degree Angle",
        title="Custom Angle", default="0",
        presence=Presence("OrientationExtended.EnableCustom",
                          WHEN_TRUE))
    link = _sel(
        "Link", ["FB", "Parameter", "Scale"],
        [("PID", "PID1", "OUT",
          "DVSYS[Pvm.Tag+'/'+Pvm.Link.FB+'/OUT_SCALE']"),
         ("PID Readback", "PID1", "OUT_READBACK",
          "DVSYS[Pvm.Tag+'/'+Pvm.Link.FB+'/OUT_SCALE']"),
         ("AO1", "AO1", "PV",
          "DVSYS[Pvm.Tag+'/'+Pvm.Link.FB+'/PV_SCALE']"),
         ("AO2", "AO2", "PV",
          "DVSYS[Pvm.Tag+'/'+Pvm.Link.FB+'/PV_SCALE']"),
         ("MANLD", "MANLD1", "OUT",
          "DVSYS[Pvm.Tag+'/'+Pvm.Link.FB+'/OUT_SCALE']")],
        "PID", title="Link", description="Function Block Link")
    return PvmConfiguration("HP_C_Valve", [
        PropertyGroup("BasicConfiguration", [
            _sel("Level", ["LevelNo"],
                 [("Level 1", "1"), ("Level 2", "2"),
                  ("Level 3", "3"), ("Level 4", "4")],
                 "Level 2", title="Display Level"),
            PvmProperty("Tag", "Control Tag", title="Module"),
            PvmProperty("FriendlyName", "String",
                        title="Friendly Name"),
            PvmProperty("ShowTag", "Boolean", title="Show Tag",
                        default="True"),
            link,
            _show_hide("ShowBody", "Show Valve Body"),
            _show_hide("ShowBar", "Show Output Bar"),
            PvmProperty("ShowTickMarks", "Boolean",
                        title="Show Tick", description="Show Tick",
                        default="True",
                        presence=Presence("ShowBar.Yes", WHEN_TRUE)),
            orientation, orientation_ext, custom_angle,
            _show_hide("ShowIcons", "Show Icons", default="Hide"),
        ]),
        PropertyGroup("ChartBuilder", [
            _sel("ChartBuilder", ["Rows"],
                 [("None", "0"), ("Compact", "3"), ("Full", "6")],
                 "None", title="Chart Builder"),
            *(PvmProperty(f"Parameter{i}", "Parameter Reference")
              for i in range(1, 4)),
            *(PvmProperty(f"Description{i}", "String")
              for i in range(1, 4)),
        ], present_online=False),
    ])


def _three_way(pvm_class: str, extra_basic=()) -> PvmConfiguration:
    valve_type = _sel(
        "ValveType", ["twoWay", "threeWay"],
        [("Two way", "True", "False"), ("Three way", "False", "True")],
        "Two way", title="Valve Type",
        presence=Presence("", ALWAYS))
    port = _sel(
        "Port", ["ClosedPort", "OpenPort"],
        [("a (In) - b (Open)", "2", "3"),
         ("a (In) - c (Open)", "3", "2"),
         ("b (In) - a (Open)", "1", "3"),
         ("b (In) - c (Open)", "3", "1"),
         ("c (In) - a (Open)", "1", "2"),
         ("c (In) - b (Open)", "2", "1")],
        "a (In) - b (Open)",
        description="Port Selection (in Close Pos)",
        presence=Presence("ValveType.threeWay", WHEN_TRUE))
    scale = _sel(
        "Scale", ["Source"],
        [("From FB", "DVSYS[Pvm.Tag+'/'+Pvm.FB+'/OUT_SCALE']"),
         ("From parameter", "Pvm.ParameterScale")],
        "From FB", title="Scale")
    return PvmConfiguration(pvm_class, [
        PropertyGroup("BasicConfiguration", [
            PvmProperty("Tag", "Control Tag", title="Module"),
            PvmProperty("FriendlyName", "String",
                        title="Friendly Name"),
            PvmProperty("FB", "Function Block Reference",
                        title="Function Block"),
            PvmProperty("FBNumber", "Number", title="FB Number"),
            PvmProperty("ShowTag", "Boolean", title="Show Tag",
                        default="True"),
            scale,
            PvmProperty("ParameterScale", "Parameter Reference"),
            valve_type, port, *extra_basic,
        ]),
        PropertyGroup("GeometryConfiguration", [
            _sel("Orientation", ["BodyRot"],
                 [("Up", "0"), ("Down", "180"), ("Left", "270"),
                  ("Right", "90")], "Up"),
        ]),
        PropertyGroup("ExtendedConfiguration", [
            _sel("FailPositionIndicator", ["Show"],
                 [("Show", "True"), ("Hide", "False")], "Show"),
            _sel("Output", ["Source"],
                 [("Implied", "OUT"), ("Readback", "OUT_READBACK")],
                 "Implied"),
            _sel("MinimalIndications", ["Yes"],
                 [("On", "True"), ("Off", "False")], "Off"),
            PvmProperty("ILInfo", "Boolean", title="Interlock Info",
                        default="False"),
            _sel("ModeSelection", ["Set"],
                 [("Full", "FULL"), ("Reduced", "REDUCED")], "Full"),
            _sel("AbnormalMode", ["Show"],
                 [("Show", "True"), ("Hide", "False")], "Show"),
            PvmProperty("LockInfo", "Boolean", title="Lock Info",
                        default="False"),
        ], present_online=False),
        PropertyGroup("ChartBuilder", [
            _sel("ChartBuilder", ["Rows"],
                 [("None", "0"), ("Compact", "3")], "None"),
            PvmProperty("Parameter1", "Parameter Reference"),
            PvmProperty("Description1", "String"),
        ], present_online=False),
    ])


def pcsd_3w_ctrl_valve() -> PvmConfiguration:
    return _three_way("PCSD_3WCtrlValve_v01_")


def ctrl_valve() -> PvmConfiguration:
    return _three_way("CtrlValve_", extra_basic=(
        PvmProperty("FBPath", "Function Block Reference",
                    title="Module / FB"),
        PvmProperty("PV", "Parameter Reference", title="PV path"),
        PvmProperty("Color", "Color", title="Valve Body Color"),
    ))


def sample_configurations() -> dict:
    return {c.pvm_class: c for c in
            (hp_c_valve(), pcsd_3w_ctrl_valve(), ctrl_valve())}
