"""Azeo HMI capability ledger — item by item, honestly.

This is the executable product contract. Its purpose includes recording what
is **not** implemented: anyone can enumerate what works, and a specification
that records only successes is one nobody re-reads.

The ledger follows one discipline throughout: `implementation` is a dotted
path under `azeo_control_trainer.core.hmi`, except product-owned UI paths,
which begin with ``azeo_`` directly under `azeo_control_trainer`.
`tests/_smoke_operator.py` resolves every one, so renaming a symbol breaks the
inventory instead of quietly orphaning a claim.

**On DEPARTURE.** Four entries record product decisions rather than debt:
shipped PVM classes remain tested Python classes while user-authored classes
remain editable documents; operator chrome is optimized for the training
workflow; High Performance alarms share one theme vocabulary across banner,
marks, and faceplates; and training projects keep one control scheme per
module. Recording these as MISSING would invite a later change to erase an
intentional architecture choice.

**On NOT_APPLICABLE.** Batch controls, SQL tables and enterprise
historian pens need products this repo does not have. They are not
gaps in the trainer; they are gaps between the trainer and a plant.

    from azeo_control_trainer.core.hmi.pvms.capabilities import report
    print(report())
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Status(str, Enum):
    """How far along one item is."""

    #: Built, reachable from the studio, and covered by a test.
    IMPLEMENTED = "Implemented"
    #: The model exists and is tested; nothing in the UI reaches it
    #: yet. Distinct from MISSING because the work left is wiring, and
    #: distinct from IMPLEMENTED because an engineer cannot use it.
    MODEL_ONLY = "Model only"
    #: Drawn from data nothing produces. Where a screen lies.
    DISPLAY_ONLY = "Display only"
    #: Required by the product contract, absent here.
    MISSING = "Missing"
    #: A deliberate product decision. The note is the argument.
    DEPARTURE = "Departure"
    #: Needs a product or site integration this repo does not have.
    NOT_APPLICABLE = "Not applicable"


class Area(str, Enum):
    LIBRARY = "Library"
    PROPERTY = "Property model"
    ELEMENT = "Elements"
    DISPLAY = "Displays"
    ENVIRONMENT = "Operator environment"
    PVM = "PVM classes and instances"
    CONTEXTUAL = "Faceplates and detail displays"
    DEPLOY = "Deploy and performance"


@dataclass(frozen=True, slots=True)
class Item:
    """One line of the inventory."""

    key: str
    label: str
    status: Status
    #: Dotted path under `azeo_control_trainer.core.hmi`, resolved by
    #: the smoke test. Empty only where there is nothing to point at.
    implementation: str
    area: Area
    #: Product requirement or subsystem this comes from.
    source: str = ""
    notes: str = ""

    @property
    def done(self) -> bool:
        return self.status is Status.IMPLEMENTED


def _ok(key, label, impl, area, source, notes=""):
    return Item(key, label, Status.IMPLEMENTED, impl, area, source, notes)


def _model(key, label, impl, area, source, notes=""):
    return Item(key, label, Status.MODEL_ONLY, impl, area, source, notes)


def _gap(key, label, area, source, notes=""):
    return Item(key, label, Status.MISSING, "", area, source, notes)


# ---------------------------------------------------------- the library
LIBRARY = (
    _ok("standards", "Standards: nine types, named, referenced by "
        "property", "azeo_graphics_designer.library_explorer.STANDARD_TYPES",
        Area.LIBRARY, "Standards overview",
        "Colour, Font, Boolean, Image, String, Private String, "
        "Multi-language String, Measurement, Number — the product contract's "
        "list exactly."),
    _ok("standard_ref", "A property references a standard and follows it",
        "pvms.properties.STANDARD", Area.LIBRARY,
        "Referencing standards"),
    _ok("functions", "Functions: typed conversion from value to value",
        "pvms.functions.FunctionStore", Area.LIBRARY,
        "Library functions overview",
        "Threshold (the colour-table lookup) and scale (linear, "
        "clamped) cover the classroom cases."),
    _ok("themes", "Themes swap standard values system-wide",
        "theme.tokens.THEMES", Area.LIBRARY, "Themes overview"),
    _ok("function_inputs", "Functions with up to five typed inputs and "
        "five calculation expressions", "pvms.functions.FunctionStore.eval",
        Area.LIBRARY, "Configuring functions",
        "Typed inputs and ordered calculations feed the same safe "
        "expression evaluator used by property animations."),
    _ok("function_logic_kinds", "Rule-based and value-based conversion "
        "logic as distinct authoring modes", "pvms.functions.FunctionStore",
        Area.LIBRARY, "Conversion logic"),
    _ok("templates", "Templates for displays and layouts",
        "azeo_graphics_designer.window.HmiStudioWindow.new_from_template",
        Area.LIBRARY, "Templates overview",
        "Display, contextual-display and layout templates are persistent; "
        "the File ribbon creates from and saves to them. Four protected "
        "ISA-101 L1-L4 starting points and a linked hierarchy-pack command "
        "cover the operator-display workflow without auto-publishing it. Theme-aware L1 KPIs, "
        "L2 unit operation, L3 equipment detail and L4 diagnostics use native PVMs/charts "
        "and explicit unconfigured references. Tests: test_hierarchy_starting_templates.py; "
        "native review: verify_smart_routing_hierarchy.py. A fifth protected "
        "L2 Distillation Column template adds a native process arrangement, "
        "fan indicators, PID vessels and pump PVMs; project mappings remain explicit."),
    _ok("analog_fan", "Fan-shaped analog indicator with measured range and AI faceplate",
        "pvms.dynamos.AnalogFanPvm", Area.LIBRARY, "Operating measurements",
        "AI OUT and engineering range drive the needle and numeric value. Missing, "
        "nonfinite or Bad/Uncertain data removes the needle. Neutral scale sectors "
        "do not imply operating zones; real bound limits and alarm state drive marks. "
        "Tests: test_distillation_reference.py; native operator review: "
        "verify_operator_workspace.py --native --distillation."),
    _ok("library_tree", "Library Explorer: Languages, Themes, and four "
        "permanent folders per library",
        "azeo_graphics_designer.library_explorer.LibraryExplorer", Area.LIBRARY,
        "Library Explorer pane",
        "PVM Classes, Templates, Standards, Functions — the product contract's "
        "set and its order. A missing permanent folder reads as 'this "
        "system has no such thing' rather than 'not built yet'."),
    _ok("engineering_library_catalog", "Typed PVM, Faceplate, Detail and "
        "Special Symbol project folders with health and cross-references",
        "pvms.library_catalog.LibraryCatalog", Area.LIBRARY,
        "Library Explorer pane",
        "Authored classes report validated configuration state, paired "
        "classes, display/nesting usages and origin; search and the class "
        "inspector consume the same derived catalogue."),
    _ok("multi_library", "Several configuration libraries, import and "
        "export between systems", "pvms.configuration."
        "ConfigurationLibraryStore", Area.LIBRARY,
        "Configuration libraries overview",
        "Named libraries contain the same PvmDisplay documents and can be "
        "created or opened from Studio and selected during import."),
)

# --------------------------------------------------------- the property
PROPERTY = (
    _ok("diamond", "Every property takes a static value, an animation, "
        "a blink, a function, a standard, a variable or a PVM property",
        "pvms.properties.MENU_ORDER", Area.PROPERTY,
        "Diamond context menu reference",
        "The keystone: one gesture that works on fill, line, rotation, "
        "visibility, text, width and angle alike."),
    _ok("animation", "Simple animation: a parameter through a "
        "conversion function", "pvms.properties.PropertyResolver",
        Area.PROPERTY, "Animations"),
    _ok("blink", "Blink animation on a condition, with a default",
        "pvms.properties.BLINK_STANDARD_MS", Area.PROPERTY, "Animations",
        "An unresolvable condition shows the default and does not "
        "blink: a blink that means nothing is worse than none."),
    _ok("worst_quality", "A compound value takes the worst input quality",
        "pvms.properties.worst_quality", Area.PROPERTY,
        "Referencing values through graphics expressions"),
    _ok("bad_removes", "Bad quality removes the override rather than "
        "inventing a value", "pvms.properties.UNSET", Area.PROPERTY,
        "Animations"),
    _ok("prefixes", "Context prefixes Dsp. Lyt. Grp. Pvm. GL.",
        "pvms.properties.PREFIXES", Area.PROPERTY,
        "Building graphics expressions"),
    _ok("variables", "Typed variables on display, PVM class and group",
        "pvms.variables.VariableSet", Area.PROPERTY,
        "Variables overview",
        "One animated value shared by thirty properties — the answer "
        "to the problem scripting is usually reached for."),
    _ok("expressions", "Arithmetic expressions over control paths",
        "pvms.properties.paths_in_expression", Area.PROPERTY,
        "Referencing values through graphics expressions",
        "The bracketed path syntax, DLSYS[\"M/B/P\"], because "
        "a bare path collides with division."),
    _ok("scripting", "TypeScript scripting and the Script Assistant",
        "pvms.scripting.GraphicsScriptRuntime", Area.PROPERTY,
        "Writing scripts and graphics expressions",
        "Typed event scripts run through a restricted ECMAScript runtime "
        "with source and collection caps. "
        "The graphics object model exposes only display/session services; "
        "process writes still pass LiveGraphSource ownership and the current "
        "station role, while filesystem, network and unbounded-loop APIs are "
        "absent."),
    _ok("degree_font_anim", "Degree- and font-type simple animations "
        "with start/end points and fill behaviour",
        "pvms.properties.PropertyResolver", Area.PROPERTY, "Animations",
        "Degree animations support hold, extrapolate and default outside "
        "the input range; font animations select their on/off value."),
)

# --------------------------------------------------------- the elements
ELEMENT = (
    _ok("shapes", "Arc, chord, ellipse, image, line, pie, polygon, "
        "rectangle, rounded rectangle, text, triangle, trapezoid",
        "pvms.shapes.SHAPE_KINDS", Area.ELEMENT, "Shapes elements"),
    _ok("shape_fill_direction", "Closed-shape fill percentage and "
        "bottom, top, left or right fill direction",
        "pvms.rendering.items.StaticItem._fill_clip", Area.ELEMENT,
        "Rectangle: Fill properties",
        "The same property now produces vertical PV bars and horizontal "
        "OUT bars; direction is authored in Graphics Configuration."),
    _ok("connector_freeform", "Smart connectors, straight-section dragging, branches and freeform drawing",
        "pvms.rendering.items.PipeItem", Area.ELEMENT, "Shapes elements",
        "Select and drag horizontal/vertical pipe sections without detaching equipment ports. "
        "New pipes use bounded crossover bridges; explicit junction splitting is reachable "
        "from Routing. Problems diagnoses overlaps and ambiguous contacts. "
        "Tests: test_smart_pipe_interaction.py."),
    _ok("datalink", "Datalinks: string, numeric, scaled numeric, "
        "mode, named set, scaling", "pvms.rendering.items.StaticItem",
        Area.ELEMENT, "Datalink types",
        "Placeable from ribbon or Palette, configured in Graphics "
        "Configuration, validated before publish, and bound through "
        "the shared Studio/Operator renderer. Field suffixes are "
        "resolved by LiveGraphSource."),
    _ok("datalink_errors", "????????? for Bad, @@@@@@@@ for not "
        "communicating", "pvms.elements.STALE_TEXT", Area.ELEMENT,
        "Datalink types",
        "Two different faults. Collapsing them into one blank throws "
        "away the operator's only clue about where to look."),
    _ok("user_entry", "Button, check box, combo box, radio button, "
        "slew, slider, text entry", "pvms.rendering.items.StaticItem.activate",
        Area.ELEMENT, "User entry elements",
        "All seven are placeable from the ribbon or Palette, configured "
        "in Graphics Configuration, rendered by the shared Studio/Operator "
        "item, and write only through LiveGraphSource's ownership checks. "
        "TEST mode refuses writes."),
    _ok("interaction", "Actions on mouse events, and interaction "
        "regions", "pvms.rendering.items.StaticItem.activate",
        Area.ELEMENT, "Interaction regions",
        "Mouse actions dispatch navigation, writes and value changes; an "
        "invisible element has no interaction region."),
    _ok("watch_area", "Watch area fed by drag actions",
        "azeo_graphics_designer.studio.panes._WatchArea", Area.ELEMENT, "Data elements"),
    _ok("sparkline", "Sparkline in a compact trend",
        "pvms.pvm_painters.paint_tank_trend", Area.ELEMENT,
        "Data elements",
        "Inside the trend dynamos rather than as a placeable element."),
    _ok("chart", "Chart element: up to 10 pens, real-time plus historical",
        "pvms.elements.validate_data_element", Area.ELEMENT, "Data elements"),
    _ok("alarm_list_element", "Alarm list as a placeable element",
        "pvms.elements.validate_data_element", Area.ELEMENT, "Data elements",
        "The renderer consumes the shared alarm registry and enforces the "
        "product contract's 17-instance cap."),
    _ok("multi_point", "Multi-point element, 3-12 parameters",
        "pvms.elements.validate_data_element", Area.ELEMENT,
        "Multi-Point element"),
    _ok("radar_plot", "Radar plot, 3-12 parameters",
        "pvms.elements.validate_data_element", Area.ELEMENT,
        "Radar plot element"),
    _ok("tab", "Tab element, 1-32 tab items sharing one area",
        "pvms.elements.validate_data_element", Area.ELEMENT, "Data elements"),
    _ok("display_link", "Display link navigation with a literal target",
        "pvms.rendering.items.StaticItem.activate", Area.ELEMENT,
        "Data elements",
        "Placeable and configurable; clicks delegate to the Studio tab "
        "host in VIEW/TEST and to station navigation online."),
    _ok("display_link_placeholders", "Display link placeholder targets",
        "azeo_graphics_designer.studio.assembler.PvmStudio.validate", Area.ELEMENT,
        "Data elements",
        "The placeholder is the literal name of a display not created yet, "
        "not a context substitution. It validates and publishes while absent, "
        "then begins navigating as soon as that target is published and is in "
        "the active display set; the source display is not republished."),
    _ok("date_time", "Date-time element", "pvms.elements.DATA_ELEMENT_KINDS",
        Area.ELEMENT, "Data elements"),
    _ok("table", "Table element with static text and live tag-bound cells",
        "pvms.elements.TABLE", Area.ELEMENT, "Table element",
        "Columns, widths, headers and rows are authored together; a cell may "
        "hold text or a typed live parameter binding."),
    Item("table_sql", "Table SQL queries, paging and database-driven styles",
         Status.NOT_APPLICABLE, "", Area.ELEMENT, "Table element",
         "Needs a SQL data source and an Event Chronicle. A trainer "
         "has neither."),
    Item("web_browser", "Web browser element and the $UrlWhiteList "
         "standard", Status.NOT_APPLICABLE, "", Area.ELEMENT,
         "Data elements",
         "Embedding arbitrary web content in an operator display is a "
         "site integration, and the whitelist exists because it is a "
         "security decision."),
    Item("batch", "Twelve batch ActiveX controls", Status.NOT_APPLICABLE,
         "", Area.ELEMENT, "Batch elements",
         "Needs Azeo Batch. The SFC chart PVM covers the sequencing "
         "a control trainer teaches."),
)

# --------------------------------------------------------- the displays
DISPLAY = (
    _ok("display", "A display of shapes and PVMs, published in "
        "revisions", "pvms.publishing.PvmDisplay", Area.DISPLAY,
        "Displays overview"),
    _ok("levels", "Four ISA-101 display levels", "pvms.layout.LEVELS",
        Area.DISPLAY, "Display levels"),
    _ok("display_set", "Display sets: hierarchy at most four deep, "
        "multiple roots, non-hierarchical members",
        "azeo_graphics_designer.studio.layout_editors.DisplaySetEditor", Area.DISPLAY,
        "Display sets overview",
        "The Graphics Explorer opens the hierarchy editor; it enforces the "
        "depth cap and no-duplicate rule through the one Qt-free DisplaySet "
        "model used by station routing."),
    _ok("placeholders", "Display-set placeholders for displays not "
        "yet created", "azeo_graphics_designer.studio.layout_editors.DisplaySetEditor",
        Area.DISPLAY, "Building a display hierarchy",
        "Authored explicitly in the display-set tree and retained until the "
        "named display is later published."),
    _ok("background", "Display background, themed",
        "azeo_graphics_designer.studio.canvas", Area.DISPLAY, "Display: Basics tab"),
    _ok("canvas_frame", "Canvas description, frame size, fit and view "
        "mode are authored and persisted",
        "pvms.publishing.PvmDisplay", Area.DISPLAY,
        "Display: Basics tab",
        "The Studio pane edits these values; the authoring scene and "
        "operator viewer both consume the same frame."),
    _ok("graphics_tree", "Graphics Explorer: Displays, Contextual "
        "Displays, Display Sets and Layouts as permanent folders",
        "azeo_graphics_designer.window.HmiStudioWindow", Area.DISPLAY,
        "Graphics Explorer pane",
        "Display sets and layouts show their real contents — the "
        "hierarchy's members, a layout's screens and frames — rather "
        "than a caption naming something the engineer cannot open."),
    _ok("graphics_designer_help", "Searchable Graphics Designer Help Center, "
        "context F1 and a guided authoring tour",
        "azeo_graphics_designer.studio_help.GraphicsDesignerHelpCenter", Area.DISPLAY,
        "Graphics Designer overview",
        "Stable topic keys route F1 from the active pane. The modeless "
        "help covers display lifecycle, drawing, bindings, scripts, "
        "detailed display/PVM/Faceplate/configurator procedures, diagnostics "
        "and shortcuts. The source-tree build renders the complete illustrated "
        "walkthrough in the Help browser; every Help ribbon button reaches "
        "the same action dispatcher as Studio tools."),
    _ok("property_groups", "Element properties grouped as Graphics "
        "Studio groups them", "azeo_graphics_designer.studio.panes", Area.DISPLAY,
        "Web Browser: Configurable properties",
        "Information, Fill, Line, Geometry, Visibility, with the "
        "product vocabulary (Line Thickness, Horizontal "
        "Position). Matching the vocabulary is most of what makes this "
        "transfer to a real Graphics Designer: an engineer looks for "
        "Line Thickness under Line, not Line width under Appearance."),
    _ok("continuous_hmi_quality", "Continuous HMI checks and undoable layout fixes",
        "azeo_graphics_designer.studio.quality.QualityMonitor", Area.DISPLAY,
        "Graphics Designer authoring quality",
        "Active-document edits schedule cooperative verification in Problems. "
        "Static-label clipping, PVM size, foreground overlap, viewport readability "
        "and known-color text contrast are advisory. Shared checks also run in "
        "Quick Online. Identity navigation and checked resize fixes are exercised "
        "by test_graphics_quality_workflow.py; complex dynamic visuals need review."),
    _ok("pa_assembly_mapping", "PA assemblies map saved project procedure revisions",
        "pvms.procedure_assemblies.remap_procedures", Area.DISPLAY,
        "Graphics Designer assemblies",
        "The gallery discovers authored PA classes and validates saved YAML/mapping "
        "files. Remapping carries bindings and typed instance choices while preserving "
        "paired faceplate, conditions, tuning, trend and history classes. Changed "
        "revision content invalidates Apply. test_graphics_pa_release_workflow.py."),
    _ok("release_readiness", "Guided commissioning and release readiness",
        "azeo_graphics_designer.release_workflow.ReleaseDialog", Area.DISPLAY,
        "Graphics Designer review",
        "Applicable state cases, next pending review, combined checklist/draft save "
        "and digest-based readiness feed the existing comparison and publication "
        "gate. No checklist is reported as no established coverage. Native UI and "
        "test_graphics_pa_release_workflow.py exercise the connected workflow."),
    _ok("display_events", "Display open and close events",
        "pvms.rendering.viewer.PvmDisplayView._dispatch_display_event",
        Area.DISPLAY, "Defining how users interact with graphics"),
)

# ---------------------------------------------- the operator environment
ENVIRONMENT = (
    _ok("layout", "Layouts: screens and display frames",
        "azeo_graphics_designer.studio.layout_editors.LayoutEditor", Area.ENVIRONMENT,
        "Layouts overview",
        "The editor authors screen/frame geometry and the assigned station "
        "renders the first physical screen. Additional physical monitors "
        "remain the separately recorded multi-screen gap."),
    _ok("layout_blink_rates", "Layout styling: standard and alternate "
        "blink rates", "pvms.rendering.viewer.PvmDisplayView._property_resolver",
        Area.ENVIRONMENT, "Layout: Styling tab",
        "The assigned layout's rates feed live property resolution."),
    _ok("frames", "Static and dynamic frames; a static frame's display is "
        "never replaced", "azeo_operator_station.layout_surface.StationLayoutSurface",
        Area.ENVIRONMENT, "Screen and display frame elements",
        "Multiple dynamic frames run concurrently. The console owns its "
        "AlarmBanner chrome, so the standard static AlarmBanner frame maps "
        "to that existing strip instead of drawing a second one."),
    _ok("routing", "Automatic display routing by hierarchy level",
        "azeo_operator_station.console.LiveStation.show_display", Area.ENVIRONMENT,
        "Automatic display routing and coordination",
        "The active set supplies the display's level and open_target selects "
        "the assigned dynamic frame; explicit source-frame routes still win."),
    _ok("coordination", "Frames that change together so related "
        "displays appear together",
        "azeo_operator_station.console.LiveStation._coordinate_displays",
        Area.ENVIRONMENT, "Supporting automatic display coordination on "
        "layouts"),
    _ok("breadcrumb", "Navigation trail through the hierarchy",
        "pvms.layout.DisplaySet", Area.ENVIRONMENT,
        "Configuring a navigation bar"),
    _ok("navigation_bar", "Navigation bar with display call-up buttons",
        "azeo_operator_station.layout_surface.FrameNavigationBar",
        Area.ENVIRONMENT, "Configuring a navigation bar"),
    _ok("alarm_rollup", "Alarm rollup counts beside call-up buttons, "
        "including child displays", "azeo_operator_station.alarm_rollup."
        "DisplayAlarmRollup", Area.ENVIRONMENT, "The alarm rollup"),
    _ok("workstation_assign", "Assigning layouts and display sets to "
        "workstations", "azeo_graphics_designer.studio.layout_editors."
        "WorkstationAssignmentDialog", Area.ENVIRONMENT,
        "Using display sets",
        "Graphics Designer assigns one layout, several selectable display "
        "sets and the initial set. Reset Layout, Layout Scale and Display "
        "Sets then become live station tools."),
    _ok("multi_screen", "Video desktops spanning several monitors",
        "azeo_operator_station.console.LiveStation", Area.ENVIRONMENT,
        "Layouts overview",
        "One StationLayoutSurface is created per physical screen; secondary "
        "surfaces use the authored origin and independent top-level windows."),
    Item("languages", "Multi-language publishing and language-specific "
         "strings", Status.NOT_APPLICABLE, "", Area.ENVIRONMENT,
         "Languages overview",
         "The Multi-language String standard type exists so a display "
         "authored for it still loads. Running a trainer in several "
         "languages is a product decision, not a gap."),
)

# --------------------------------------------------------------- PVMs
PVM = (
    _ok("pvm_classes", "PVM classes registered per block type and role",
        "pvms.base.registry", Area.PVM, "PVM classes overview"),
    _ok("pvm_appearance", "Per-placement PVM Fill Color and Line Color",
        "azeo_graphics_designer.studio.panes._ConfigPane._apply_pvm_colour",
        Area.PVM, "Creating PVM instances",
        "Blank values follow the active theme. Explicit values affect only "
        "ordinary panel/equipment material and line roles; alarm and status "
        "colors retain their governed meanings."),
    _ok("pvm_config", "PVM Configuration Designer: custom properties "
        "referenced from elements",
        "pvms.configurator.model.PvmConfiguration", Area.PVM,
        "Configuring PVM classes"),
    _ok("pvm_config_preflight", "PVM configuration validation, atomic "
        "save, rename propagation and undo/redo",
        "pvms.configurator.model.PvmConfiguration.issues", Area.PVM,
        "PVM Configuration Designer",
        "Invalid class documents are explained together and cannot be "
        "saved; exact references follow a property rename."),
    _ok("faceplate_blueprint", "Studio-authored professional faceplate "
        "and compact PVM pair with typed live instance inputs",
        "azeo_graphics_designer.configurator.designer.PvmConfigDesigner."
        "new_faceplate_blueprint", Area.PVM,
        "Creating PVM classes",
        "Creates a measured faceplate, a compact paired calling PVM and "
        "validated schemas; the mini button and double-click open the same "
        "live user-authored faceplate."),
    _ok("faceplate_icon_elements", "Documented Loop_fp icon buttons as "
        "placeable vector elements", "pvms.faceplate_icons.FACEPLATE_ICONS",
        Area.PVM, "Loop_fp (PID Loop module faceplate)",
        "An icon becomes a button only after an Interaction action "
        "is assigned, so the palette never creates an inert hotspot."),
    _ok("faceplate_special_symbols", "Documented faceplate marks in the "
        "Special Symbols palette", "pvms.faceplate_icons.SPECIAL_SYMBOLS",
        Area.PVM, "Loop_fp (PID Loop module faceplate)",
        "The symbol artwork is scalable and can be resized, grouped and "
        "configured with an Interaction action like any drawing element."),
    _ok("presence", "Presence and Present Online gate what is bound",
        "pvms.configurator.model", Area.PVM,
        "Display complexity measures",
        "A performance feature as much as a visibility one: what is "
        "not present online cannot cost anything."),
    _ok("convert_to_pvm", "Convert selected elements into a PVM class",
        "azeo_graphics_designer.studio.assembler", Area.PVM, "Creating PVM classes"),
    _ok("class_edit", "Edit a class's layout; instances follow on save",
        "pvms.user_library.UserPvmLibrary", Area.PVM,
        "Deploying PVM classes"),
    _ok("relink", "Relink an instance to another class, choices carried",
        "azeo_graphics_designer.studio.assembler", Area.PVM, "Creating PVM instances"),
    _ok("linked_unlinked", "Linked and unlinked instances",
        "azeo_graphics_designer.studio.assembler.PvmStudio.set_user_pvm_link", Area.PVM,
        "Creating PVM instances from a PVM class",
        "The instance shortcut menu can unlink, relink, or unlink its "
        "entire nested subtree."),
    _ok("overrides", "Per-property overrides on a linked PVM",
        "azeo_graphics_designer.studio.assembler.PvmStudio.override_user_pvm", Area.PVM,
        "PVM Overrides",
        "Overrides pin the current value and can be removed to resume "
        "class propagation."),
    _ok("nesting", "PVM classes nested inside PVM classes",
        "pvms.user_library.UserPvmLibrary.add_nested", Area.PVM,
        "Creating PVM classes",
        "Recursive expansion guards cycles and carries link and override "
        "semantics through the chain."),
    _ok("block_coverage", "A PVM class for every block an operator has "
        "a reason to look at", "pvms.coverage", Area.PVM,
        "Module PVM classes",
        "Was 5 placeable block types against 145 function blocks, so a "
        "student could build a ratio station or a voter and find "
        "nothing to draw with. The installed operator-facing set now "
        "covers 28 block families. The blocks still without "
        "one are the arithmetic and logic families, and that is "
        "correct: a display shows the process, not the AND gates "
        "behind it. Every block carrying a FACEPLATE is now also "
        "placeable, so 'open it but never draw it' is gone."),
    _ok("binding_integrity", "Every PVM binding resolves against the "
        "real block", "pvms.base.Bind", Area.PVM,
        "Module PVM classes",
        "Asserted by resolving all 384 single-path bindings through a "
        "live LiveGraphSource, not by matching names — config lookup "
        "is case-tolerant and CONDITIONS is synthesised, so name "
        "matching produced twelve false positives and hid the one real "
        "defect (PIN bound CONFIG/PULSE_VALUE; the key is PULSE_VAL). "
        "A binding that names nothing renders Bad for ever, which on a "
        "display is indistinguishable from a dead transmitter — worse "
        "than a missing PVM, because it lies."),
    _ok("palette_folders", "Palette sections derived from the library "
        "folder structure", "pvms.user_library.UserPvmLibrary.folders",
        Area.PVM, "Creating PVMs"),
    _ok("drag_link_modifiers", "Drag for a linked PVM, Alt-drag for "
        "unlinked, Shift+Alt-drag to unlink nested PVMs too",
        "azeo_graphics_designer.studio.assembler.PvmStudio.place_user_pvm", Area.PVM,
        "Creating PVMs"),
    Item("python_classes", "Shipped PVM classes are Python, not "
         "authored documents", Status.DEPARTURE, "pvms.dynamos",
         Area.PVM, "PVM classes overview",
         "Deliberate. A class defined in code has a test; a class "
         "defined by drawing has a screenshot. User-authored drawing "
         "PVMs go through `user_library` and follow the same class contract, "
         "so both routes exist and only the shipped set is code."),
)

# ------------------------------------------------------ contextual
CONTEXTUAL = (
    _ok("faceplates", "Faceplates per block type, dispatched by tag",
        "pvms.render.PvmFaceplateWidget", Area.CONTEXTUAL,
        "Common faceplate and detail display elements"),
    _ok("faceplate_writes", "Writable faceplate bindings reach checked "
        "operator controls", "pvms.render.PvmFaceplateWidget.write_bound",
        Area.CONTEXTUAL, "User entry elements",
        "Controls stay disabled until the host attaches its write service "
        "and follow the block's current CanWrite decision. Detail Limits "
        "and Tuning fields update declared CONFIG parameters online and "
        "re-apply the block without an operator-role gate; granular role "
        "authorization is deliberately deferred."),
    _ok("faceplate_sections", "AI_fp built as one fixed-coordinate "
        "surface with a reusable eight-section declaration",
        "pvms.analog_surface.AnalogFaceplateSurface", Area.CONTEXTUAL,
        "AI_fp (Analog Monitoring module faceplate)",
        "title / value / pv_bar+mode / trend / alarms / unit / "
        "buttons. The tuple remains the class catalogue and is validated "
        "by `build_sections`; the operator body is a fixed 204 x 468 "
        "painter because independent QWidget size hints moved its scale, "
        "trend and alarm table away from the accepted layout."),
    _ok("bar_colour", "The PV bar uses one invariant colour in every "
        "theme", "pvms.analog_surface.AnalogFaceplateSurface",
        Area.CONTEXTUAL,
        "AI_fp (Analog Monitoring module faceplate)",
        "#7B92AD, held as a shared design token, "
        "and theme-invariant like the alarm priorities. Only workable "
        "because the level is identified by its OUTLINE: the bare fill "
        "clears WCAG's 3:1 non-text minimum against hpgray's track by "
        "1.73:1 while the "
        "outlined boundary clears it by 9.2:1 at worst. Re-tinting per "
        "theme was the alternative and it loses the one thing that was "
        "asked for."),
    _ok("faceplate_layouts", "Module faceplates use the declared "
        "section stacks", "pvms.render.PvmFaceplateWidget."
        "_build_azeo_layout", Area.CONTEXTUAL,
        "Module faceplate contract",
        "AI, AO, PID, cascade, device, selector, ratio, totalizer and SFC "
        "families declare reusable section order while retaining their "
        "specialized bar and table panels."),
    _ok("faceplate_profiles", "Every registered faceplate and detail uses "
        "a fixed shell and Azeo faceplate palette, with a visual contract "
        "declared per class",
        "pvms.faceplate_catalog.CONTRACTS", Area.CONTEXTUAL,
        "Module faceplate contract",
        "AI, loop, device, condition, selector, voter and "
        "state contracts define the compact/wide profile families. Native "
        "geometry and runtime-contract matches are labelled native. "
        "AT, CTLSL, Device and Motor Interlock retain family geometry but "
        "are labelled adapted because their trainer data "
        "contracts are narrower; trainer-only ratio, totalizer, balance, "
        "cascade, select and SFC contracts are adaptations too. None are "
        "explicitly identified. Studio "
        "passes its Azeo theme into contextual windows; a coverage check "
        "refuses any registered contextual class without a profile, so a new "
        "class cannot silently fall back to an elastic generic dialog."),
    _ok("detail", "PDF-style two-column detail displays with Limits, "
        "Alarms and MERROR/MSTATUS/BLOCKERR diagnostics",
        "pvms.detail_ui.DETAIL_PANELS",
        Area.CONTEXTUAL, "Module detail displays",
        "AI, pulse input, analog output, PID, device, interlock and sequence "
        "details use specialized bodies; none render as a generic list of "
        "internal binding keys."),
    _ok("tag_substitution", "Partial tag substitution: one document serves "
        "every module", "pvms.instances.ContextualDisplay.substitute",
        Area.CONTEXTUAL, "Faceplates and detail displays"),
    _ok("pinning", "Pushpin, minimize, and replacement of unpinned "
        "contextual displays", "pvms.render.PvmFaceplateWidget.toggle_pin",
        Area.CONTEXTUAL, "Allowing pinning and replacement"),
    _ok("mini", "Mini faceplates with collapse and expand",
        "pvms.render.PvmFaceplateWidget.toggle_minimized",
        Area.CONTEXTUAL, "Module faceplates"),
    _ok("title_bar", "Contextual display title bar: icon, title, pushpin, "
        "minimize, close", "pvms.render.ContextualTitleBar",
        Area.CONTEXTUAL, "Title bar elements"),
    _ok("primary_control", "Primary Control displays opened from an alarm",
        "azeo_operator_station.alarm_rollup.DisplayAlarmRollup.primary_control",
        Area.CONTEXTUAL, "Primary control displays"),
    _ok("tuning_trend", "Tuning trend popup: one-second PV/SP/OUT",
        "pvms.contextual.TuningTrendWidget", Area.CONTEXTUAL,
        "Tuning trends"),
)

# ------------------------------ the High Performance PVM anatomy
# Shared furniture is built once in `pvms/hp/` rather than 28 times.
# Alarm state is fed by the shared runtime registry; the same records drive
# the banner, HP furniture, display rollups, and primary-control navigation.
HP_ANATOMY = (
    _ok("hp_alarm_box", "Alarm box: a thick coloured rectangle carrying "
        "the highest priority alarm", "pvms.hp.marks.draw_alarm_box",
        Area.PVM, "Common components in High Performance PVM classes"),
    _ok("hp_status_box", "Status box, and the precedence that an alarm "
        "outranks it", "pvms.hp.state.AlarmBoxState.shows_status_box",
        Area.PVM, "Common components in High Performance PVM classes",
        "One rectangle, one meaning: the status box shows only when "
        "no active or unacknowledged alarm exists."),
    _ok("hp_level_split", "Level 1 shows the box, Level 2 the icon",
        "pvms.hp.marks.draw_alarm_icon", Area.PVM,
        "Common components in High Performance PVM classes",
        "An overview is read across the room; a detail display up "
        "close. The level comes from the display, not the PVM."),
    _ok("hp_icon_states", "The alarm icon's four states, each separable "
        "without colour", "pvms.hp.state.AlarmBoxState.icon_state",
        Area.PVM, "Other PVM class components",
        "Shape gives priority, fill gives active, an outer ring gives "
        "unacknowledged, a dash gives suppressed."),
    _ok("hp_status_icons", "Status icons, with the product's suppression "
        "rules", "pvms.hp.state.AlarmBoxState.visible_conditions",
        Area.PVM, "Status icons",
        "Not Running hides Bad I/O; either hides Simulate."),
    _ok("hp_status_indicator", "The abnormal-status indicator, which "
        "replaces the alarm icon or wraps it",
        "pvms.hp.marks.draw_status_indicator", Area.PVM,
        "Status indicator"),
    _ok("hp_combination_bar", "Combination bar graph: PV, SP, alarm "
        "limit regions, T/t shape recognition",
        "pvms.hp.bars.draw_combination_bar", Area.PVM,
        "Combination bar graph",
        "Limits live in their own strip so a PV sitting at its limit "
        "does not cover the limit it is sitting on."),
    _ok("hp_user_scale", "User-defined scale, marked by perpendicular "
        "end lines", "pvms.hp.bars.BarScale", Area.PVM,
        "Combination bar graph",
        "A partial range read as a full one misjudges every value on "
        "the display, so the mark is not optional."),
    _ok("hp_deviation_bar", "Deviation bar graph, SP pinned to the "
        "centre", "pvms.hp.bars.draw_deviation_bar", Area.PVM,
        "Deviation bar graph"),
    _ok("hp_out_bar", "OUT bar graph with 25/50/75 ticks",
        "pvms.hp.bars.draw_out_bar", Area.PVM, "OUT bar graph"),
    _ok("hp_data_fields", "Data fields: six right-justified characters, "
        "F_DECPT places", "pvms.hp.tag.format_data_field", Area.PVM,
        "Data fields",
        "Fixed width is what lets a column of PVMs line its decimal "
        "points up; a field that grows with its value breaks it."),
    _ok("hp_display_tag", "Display tag with the ShowTag modes and their "
        "fallback", "pvms.hp.tag.display_tag", Area.PVM,
        "Common components in High Performance PVM classes"),
    _ok("hp_eu_descriptor", "EU descriptor from the scaling parameter",
        "pvms.hp.tag.eu_descriptor", Area.PVM, "EU descriptor"),
    _ok("hp_hover", "The High Performance hover window",
        "pvms.hp.hover.hover_text", Area.PVM,
        "High Performance hover window",
        "A row appears only when its binding resolved — a row reading "
        "'SP 0.0' on a block with no setpoint answers the operator "
        "with something that was never true."),
    _ok("hp_classes", "HP faces registered as variants, leaving the "
        "shipped dynamos alone", "pvms.hp.classes.HPCombinationPvm",
        Area.PVM, "High Performance PVM classes reference"),
    _ok("hp_alarm_count", "Alarm count across an area, unit or parameter",
        "binding.alarm_state.RuntimeAlarmRegistry.block_summary", Area.PVM,
        "Alarm count"),
    _ok("hp_acknowledged", "Acknowledged vs unacknowledged alarms",
        "binding.alarm_state.RuntimeAlarmRegistry.acknowledge", Area.PVM,
        "Other PVM class components"),
    _ok("hp_suppressed", "Suppressed and shelved alarms",
        "binding.alarm_state.RuntimeAlarmRegistry.suppress", Area.PVM,
        "Other PVM class components",
        "Suppressed records remain visible as suppressed but do not demand "
        "attention or inflate active alarm totals."),
    _ok("hp_not_running", "Module Not Running, from runtime state",
        "binding.source.LiveGraphSource", Area.PVM, "Status icons"),
    _ok("hp_dc_conditions", "No Permit, Interlocked, Tracking and Bypassed "
        "status icons", "pvms.hp.fb_classes.DEVICE_CONDITION_BINDS",
        Area.PVM, "Status icons"),
    _ok("hp_sp_wrk", "SP_WRK on the combination bar",
        "pvms.hp.classes.HPCombinationPvm", Area.PVM,
        "Combination bar graph",
        "The trainer's PID publishes SP_WRK directly; RCAS_OUT remains the "
        "remote-host back-calculation parameter."),
    _ok("hp_status_box_config", "Per-condition show/hide for the status box",
        "pvms.base.PvmClass.STATUS_BOX_CONDITIONS", Area.PVM,
        "Common components in High Performance PVM classes"),
    _ok("azeo_chrome", "Azeo Operator Station's menu bar and navigation bar "
        "are drawn, positioned and budgeted",
        "azeo_operator_station.workspace.OperatorCommandBar", Area.ENVIRONMENT,
        "Azeo Operator Station desktop",
        "Native labeled commands use the shared Control Designer blue. "
        "Comfortable chrome is 144 px with two alarm lines (13.3% of 1080); "
        "compact is 136 px. Larger targets and a persistent alarm banner "
        "justify this documented budget departure. Single-frame stations "
        "omit the redundant frame navigation row."),
    _ok("azeo_menu_actions", "Every menu-bar button that is drawn "
        "does something", "azeo_operator_station.console.LiveStation."
        "menu_action", Area.ENVIRONMENT, "Azeo Operator Station desktop",
        "It shipped with 1 of 12 wired. All standard buttons are now "
        "painted and handled; the smoke contract compares those sets, "
        "so adding a button without an action fails the suite."),
    _ok("azeo_nav_actions", "The navigation bar navigates",
        "azeo_operator_station.console.LiveStation.navigate",
        Area.ENVIRONMENT, "Azeo Operator Station desktop",
        "Back/forward run on `NavigationStack`, which holds display IDS "
        "and no document format. It was promoted before the former stack "
        "was archived and now serves the one shipping console. A "
        "button with nowhere to go stays present and faint; a "
        "control that vanishes moves the ones beside it."),
    _ok("azeo_errors_bubble", "The Errors bubble reports errors on "
        "an open display",
        "azeo_operator_station.console.LiveStation.sync_chrome",
        Area.ENVIRONMENT, "Azeo Operator Station desktop",
        "Counted from the display actually on screen — an object "
        "bound to a tag the area does not define — rather than "
        "invented. Refreshed on every console tick beside the "
        "configuration bubble."),
    _ok("station_dialogs", "Station Search, Display Errors and Alarm List "
        "open complete operator dialogs",
        "azeo_operator_station.dialogs.StationSearchDialog", Area.ENVIRONMENT,
        "Azeo Operator Station desktop",
        "Search indexes published displays and their real control paths. "
        "Alarm List refreshes live and acknowledges, suppresses or navigates "
        "through the shared alarm registry."),
    _ok("station_history", "History opens Process History View",
        "azeo_operator_station.console.LiveStation.open_process_history",
        Area.ENVIRONMENT, "Azeo Operator Station desktop",
        "The station records graph-derived points to its persistent SQLite archive. "
        "Right-click Add to Historian and Ctrl-click multi-tag selection open "
        "independent resizable charts without altering the process display; "
        "tests/test_operator_historian_selection.py checks native gestures, "
        "capacity, reuse and cleanup. "
        "Process History provides background range queries, saved groups, quality "
        "and age, A/B cursors, correlated observed events, measured response "
        "comparisons and raw report exports. tests/test_history_workspace.py "
        "checks persistence, cursor work, quality, range selection and export; "
        "tools/verify_history_workspace.py reaches these from a running station."),
    _ok("historian_context_menu", "Historian context menu for time, pens, scales and evidence",
        "history.view.ProcessHistoryView", Area.ENVIRONMENT, "Process History workspace",
        "Replaces both plotting-axis menus with connected historian actions. "
        "Mouse and keyboard invocation, A/B placement, saved pen formatting, "
        "quality-aware clipboard values and error containment are checked by "
        "tests/test_historian_context_menu.py."),
    _ok("station_workspace", "Docked operating tools retain process context",
        "azeo_operator_station.workspace.ContextWorkspace", Area.ENVIRONMENT,
        "Azeo Operator Station desktop",
        "Native toolbar and keyboard actions reach pinned faceplate comparisons, "
        "reused trends, stable alarm selection and trainee/instructor presentation "
        "views. Hidden tools stop their refresh timers; pop-out remains available. "
        "tests/test_operator_workspace.py exercises the gestures and lifecycle."),
    _ok("station_freshness", "Live status follows observed controller scans",
        "azeo_operator_station.console.LiveStation.sync_status", Area.ENVIRONMENT,
        "Azeo Operator Station desktop",
        "Completed scan counters drive freshness, pause and partial-stall states. "
        "No observable counter means No Data. The UI clock cannot keep stopped "
        "controller data looking live; simulation is separately identified."),
    _ok("station_session_controls", "Window/Full Desktop, Alarm Filter, "
        "Utilities and optional local user role",
        "azeo_operator_station.console.LiveStation.toggle_window_mode",
        Area.ENVIRONMENT, "Azeo Operator Station desktop",
        "The View Only role is enforced at the shared write boundary for "
        "user entries, faceplates, built-in actions and scripts. Local logon "
        "is explicitly a trainer identity switch, not external authentication."),
    Item("chrome_menu_bar", "A menu bar and an unlabelled icon "
         "toolbar on the console", Status.DEPARTURE,
         "azeo_operator_station.shell.chrome_azeo.MENU_BUTTONS", Area.ENVIRONMENT,
         "Azeo Operator Station desktop",
         "The station retains command and hierarchy rows for training. "
         "Primary commands now have native labels and keyboard focus; compact "
         "density retains named tooltips. Secondary commands live in Station. "
         "Icons are scalable code-native marks, with semantic process/alarm "
         "colors independent of the shared blue application controls."),
    _ok("quick_online_view", "Quick Online View — a live sandbox instance "
        "fed items on demand", "azeo_graphics_designer.quick_online.QuickOnlineView",
        Area.DEPLOY,
        "Testing graphics configuration online prior to publishing",
        "It accepts draft displays without publishing and wraps the shared "
        "source read-only, leaving workstation assignments untouched. Resending "
        "a draft refreshes the preview; operator themes, fixed viewport sizes, "
        "built-in and authored faceplates use the shared runtime surfaces. "
        "Close releases preview bindings and timers. Checked by "
        "test_graphics_authoring_confidence.py."),
    _ok("installed_items", "Azeo-installed items are protected from "
        "modification", "pvms.configuration.InstalledItems", Area.DEPLOY,
        "Modifying Azeo-installed graphics configuration",
        "Display, layout and user-PVM mutation paths enforce the product's "
        "copy-rename-modify rule."),
    Item("hp_priority_colours", "Alternative alarm-box colour vocabulary",
         Status.DEPARTURE,
         "pvms.hp.state.PRIORITY_ROLES", Area.PVM,
         "Common components in High Performance PVM classes",
         "Ours are the theme's ALARM_P1..P3 and ALARM_SHELVED. The "
         "banner, the alarm marks and the faceplates already speak "
         "these colours, and a box calling an advisory magenta beside "
         "a banner calling it yellow would be two colours for one "
         "event. The installed roles are covered by visual regression tests."),
)


# ---------------------- the function-block display catalogue
# This layer binds to ONE FUNCTION BLOCK rather than to a module: compact
# PVM classes, faceplates and detail displays share a block-rooted contract.
#
# The architectural point is worth keeping in front of whoever reads
# this: per-block pop-ups are a legitimate
# ALTERNATIVE to one-loop-per-module, not as a lesser option. Our
# one-scheme-per-module convention is OUR training choice, and the
# binding grammar (`MODULE/BLOCK/PARAM`) is already block-granular, so
# supporting the other pattern is a matter of classes and faceplates —
# not of re-plumbing anything.
FUNCTION_BLOCK = (
    _ok("fb_granular_binding", "Bindings address a single function "
        "block, not just a module", "binding.source.LiveGraphSource",
        Area.PVM, "Function block PVM classes, faceplates, and detail "
        "displays",
        "`MODULE/BLOCK/PARAM` — the prerequisite for the whole "
        "per-block display layer, and it already holds."),
    _ok("fb_seq_pvm", "Controller_SEQ_ — Step Sequencer state",
        "pvms.hp.fb_classes.ControllerSEQPvm", Area.PVM,
        "Controller_STD_ and Controller_SEQ_"),
    _ok("fb_std_pvm", "Controller_STD_ — State Transition Diagram state",
        "pvms.hp.fb_classes.ControllerSTDPvm", Area.PVM,
        "Controller_STD_ and Controller_SEQ_"),
    _ok("fb_mode_normal", "Abnormal mode is actual != NORMAL **or** "
        "actual != target", "binding.result.BindingResult.mode_mismatch",
        Area.PVM, "Status icons",
        "The contract requires both comparisons. We carried only the target "
        "half, which stayed silent for the case that matters most — a "
        "loop parked in MAN that nobody intends to put back. Blocks "
        "gained a `normal_mode` config to make the other half "
        "answerable."),
    _ok("fb_dc_conditions", "No Permit and Interlocked status icons, "
        "bound off DEVCTL", "pvms.hp.fb_classes.DEVICE_CONDITION_BINDS",
        Area.PVM, "Status icons",
        "Polarity is the trap: PERMISSIVE_D and INTERLOCK are True "
        "when the permit is GRANTED, so the icons show on the False "
        "case."),
    Item("fb_quality_unreadable", "Bad status and 'data cannot be "
         "read' are distinct renderings", Status.IMPLEMENTED,
         "pvms.elements.STALE_TEXT", Area.ELEMENT,
         "INSPECT_Block_1_ PVM class",
         "The INSPECT colour table splits Uncertain / Bad / cannot-be-"
         "read; our `?????????` vs `@@@@@@@@` already draws the same "
         "distinction, arrived at independently."),
    _ok("fb_condition_table", "The DCC_fp condition table: tabs per "
        "kind, first-out, bypass, and the elapsed-time bar",
        "pvms.faceplate_fb.ConditionTable", Area.CONTEXTUAL,
        "DCC_fp (Discrete Control Condition function block faceplate)",
        "The elapsed-time bar is the reason it exists: a condition "
        "true for 2 of its 4 seconds is ABOUT TO trip, and the alarm "
        "banner stays silent until it has. Bound to the block's own "
        "`condition_table()`, so the display cannot drift from the "
        "logic. **A trip inverts** — reading `state` at face value "
        "reports a tripped condition as OK."),
    _ok("fb_seq_faceplate", "SEQ_fp — current state over the "
        "sequencer's outputs", "pvms.hp.fb_faceplates.SEQFaceplate",
        Area.CONTEXTUAL, "SEQ_fp (Step Sequence function block "
        "faceplate)"),
    _ok("fb_std_faceplate", "STD_fp — current state over the "
        "transitions being evaluated",
        "pvms.hp.fb_faceplates.STDFaceplate", Area.CONTEXTUAL,
        "STD_fp (State Transition Diagram function block faceplate)",
        "The destination state per transition lives in the block's "
        "addressable MATRIX configuration. The faceplate parses only "
        "the current STATE row and omits absent, zero, or malformed "
        "cells rather than inventing per-transition parameters."),
    _ok("fb_isel_faceplate", "Xmtr_fp — the Input Selector's four "
        "inputs, output and algorithm",
        "pvms.hp.fb_faceplates.ISELFaceplate", Area.CONTEXTUAL,
        "Xmtr_fp (Input Selector function block faceplate)",
        "Input status rides on the value's TEXT COLOUR, which is what the "
        "display-state contract already "
        "says, so no second status vocabulary was needed."),
    _ok("fb_section_library", "The function-block faceplate bodies "
        "join the shared section library",
        "pvms.faceplate_ui.register_section", Area.CONTEXTUAL,
        "Function block faceplates reference",
        "`conditions`, `state_list` and `selector` take fixed places "
        "in the canonical order. A contributor names what it sits "
        "AFTER rather than an index, so adding one cannot silently "
        "renumber the others, and `build_sections` keeps refusing any "
        "layout that reorders them."),
    _ok("fb_faceplates_remaining", "FaceplateFB for AT, AVTR, DVTR, "
        "ECTLSL and ERAMP", "pvms.hp.fb_faceplates.AVTRFaceplate",
        Area.CONTEXTUAL, "Function block faceplates",
        "Voters provide Trip, Pre-Trip, Bypass, Startup and Alert tabs "
        "with all sixteen input rows; DVTR disables the inapplicable "
        "Pre-Trip tab."),
    Item("fb_faceplates_products", "PTC_fp and UPh_fp",
         Status.NOT_APPLICABLE, "", Area.CONTEXTUAL,
         "Function block faceplates",
         "The trainer has no PTC or Unit Phase product block. An empty "
         "faceplate would claim a capability the runtime cannot provide."),
    _ok("fb_detail_displays", "The available DetailFB set: DC_dt and "
        "FFPid_dt", "pvms.device.DeviceDetail", Area.CONTEXTUAL,
        "Function block detail displays",
        "DeviceDetail and PIDDetail bind the trainer's DC and PID runtime "
        "blocks and render through the shared module-detail shell."),
    Item("fb_uph_detail", "UPh_dt", Status.NOT_APPLICABLE, "",
         Area.CONTEXTUAL, "Function block detail displays",
         "The trainer has no Unit Phase product block."),
    _ok("fb_bypassed_icon", "The Bypassed status icon",
        "pvms.hp.classes.HPCombinationPvm", Area.PVM, "Status icons"),
    _ok("fb_advanced_control", "The available AdvancedControl PVM set: "
        "MPC, MPCPlus, MPCPro, INSPECT, LAB",
        "pvms.hp.fb_classes.ADVANCED_CONTROL_KEYS", Area.PVM,
        "Advanced Control PVM classes",
        "The three MPC product faces bind the running DMC controller; "
        "INSPECT and LAB bind their native trainer blocks."),
    Item("fb_nn_product", "NN advanced-control PVM", Status.NOT_APPLICABLE,
         "", Area.PVM, "Advanced Control PVM classes",
         "The trainer has no neural-network control product."),
    _ok("fb_voter_pvms", "Controller_AVTR_/DVTR_ 1..3Input and InputX "
        "PVMs", "pvms.hp.fb_classes.VOTER_PVM_KEYS", Area.PVM,
        "Advanced Function Block PVM classes",
        "Concrete variants cover every extensible input through 16."),
    _ok("fb_cem_effect_pvm", "Controller_CEM_Effect_, one PVM per effect",
        "pvms.hp.fb_classes.CEM_EFFECT_KEYS", Area.PVM,
        "Controller_CEM_Effect_",
        "All sixteen variants carry the effect state, first-out and "
        "configured override."),
    Item("fb_per_block_modules", "Building a whole strategy in one "
         "module with per-block pop-ups", Status.DEPARTURE, "",
         Area.DISPLAY, "Function block PVM classes, faceplates, and "
         "detail displays",
         "This is a legitimate alternative to "
         "one-loop-per-module. We mandate one scheme per module "
         "because a trainee reading a monolith cannot see where one "
         "loop ends. That is an explicit training-product choice."),
)


# ------------------- display authoring and PVM catalogue
AUTHORING = (
    _ok("hp_class_catalogue", "The 28 shipped High Performance PVM classes",
        "pvms.hp.classes.HP_CLASS_KEYS", Area.PVM,
        "High Performance PVM classes",
        "The catalogue preserves every installed name, immutable measured "
        "size and horizontal, vertical, numeric, pump, valve and SHDI/SHDO "
        "family."),
    _ok("hp_alarms_pvm", "HP_Alarms — a PVM mapped to an AREA, not a module",
        "pvms.hp.classes.HPAlarmsPvm", Area.PVM,
        "High Performance PVM classes"),
    _ok("daru_rollup", "Display alarm rollup: active / suppressed / "
        "unacknowledged counts and top priority per DISPLAY",
        "azeo_operator_station.alarm_rollup.DisplayAlarmRollup", Area.ENVIRONMENT,
        "DARU (Display Alarm rollup) object",
        "The rollup follows display bindings, can include children and "
        "resolves a module's primary control display."),
    _ok("gl_library_prefix", "The `GL.` prefix — referencing a "
        "configuration library's standards and functions",
        "pvms.properties.PREFIXES", Area.PROPERTY,
        "Building graphics expressions"),
    _ok("path_zone_and_index", "Parameter paths: `<zone>%` and `[integer]` "
        "array subscripts", "binding.source.split_parameter_path",
        Area.PROPERTY, "Referencing Azeo parameters in the Azeo system"),
    _ok("selection_pane", "The Selection pane — every element, with "
        "visibility and lock", "azeo_graphics_designer.studio.selection_pane.SelectionPane",
        Area.ELEMENT, "Performing basic operations with graphic elements",
        "It is named by multiple element operations and is "
        "the only route back to an element that has been hidden or "
        "locked — neither can be clicked on the canvas. Listed top of "
        "z-order first, because that is the order the operator sees "
        "them stacked."),
    _ok("element_shortcut_menu", "Element operations on the element's "
        "own shortcut menu",
        "azeo_graphics_designer.studio.assembler.PvmStudio._add_element_actions",
        Area.ELEMENT, "Performing basic operations with graphic elements",
        "Visibility, lock, order, align, distribute, clipboard, group "
        "and delete. The menu was style-only (fill, line, arrows), so "
        "the expected element shortcut-menu workflow was false for most "
        "commands. A ribbon is where you go when "
        "you know a command's name; a shortcut menu is where you go "
        "when you have the thing under the pointer."),
    _ok("distribute", "Distribute evens the GAPS between elements",
        "azeo_graphics_designer.studio.assembler.PvmStudio.distribute_selected",
        Area.ELEMENT, "Spacing elements",
        "Was `lambda: None` on the ribbon — a button that did nothing. "
        "Gaps rather than centres, because spacing centres evenly "
        "leaves ragged space whenever elements differ in size; the "
        "outermost two hold still, since they define the span."),
    _ok("interaction_zorder", "Overlapping interaction regions: the "
        "topmost element in z-order acts",
        "pvms.rendering.items.StaticItem.activate", Area.ELEMENT,
        "Interaction regions",
        "Scene hit-testing selects the topmost present-online item; group "
        "regions remain behind their member items."),
    _ok("pvm_connection_points", "Connection points on PVMs, added at the "
        "CLASS", "pvms.configurator.model.PvmConfiguration."
        "add_connection_point", Area.PVM, "Drawing with connection points",
        "Class points remain inherited. Equipment exposes semantic nozzle names and arbitrary "
        "outline attachments; compact-glyph and inline-item rotations each transform points "
        "and departure directions once. The connect target is highlighted and named in the status bar."),
    _ok("authoring_scripting", "The operator-safe TypeScript layer: "
        "operators, Date/Math/String, DL/DLSYS/DLPATH/ENV/Region objects "
        "and Script Assistant", "azeo_graphics_designer.script_editor.ScriptAssistantDialog",
        Area.PROPERTY, "Writing scripts and graphics expressions",
        "Includes object/snippet insertion, syntax highlighting, validation, "
        "test, find/replace, element/display events and the session DL store. "
        "SIS writes, SQL/Event Chronicle and external integration remain "
        "unavailable because this trainer has no corresponding service."),
)

# ------------------------------------------------------------- deploy
DEPLOY = (
    _ok("publish", "Publish a display in revisions, with history and "
        "revert", "pvms.publishing.DisplayStore.publish", Area.DEPLOY,
        "Publishing graphics configuration"),
    _ok("publish_gate", "Publishing is gated on binding health",
        "pvms.publishing.PublishRefused", Area.DEPLOY,
        "Confirming that graphics configuration is ready"),
    _ok("workstation_target", "A station takes the revision aimed at "
        "it, else the widest current one",
        "pvms.publishing.DisplayStore.published_document", Area.DEPLOY,
        "Publishing graphics configuration"),
    _ok("no_pvm_publish", "PVM classes are never published; their "
        "displays are", "pvms.user_library.UserPvmLibrary",
        Area.DEPLOY, "Deploying PVM classes"),
    _ok("complexity", "Complexity measures and index",
        "pvms.complexity.Complexity", Area.DEPLOY,
        "Display complexity index",
        "handlers/42 + tags/1.2 + params/33, which reproduces the "
        "product's worked example (100/50/1000 -> 74) exactly."),
    _ok("single_writer", "One editor at a time per display",
        "pvms.publishing.DisplayLocked", Area.DEPLOY,
        "Managing graphics configuration"),
    _ok("operator_station", "A console that shows ONLY published PVM "
        "revisions", "azeo_operator_station.console.LiveStation",
        Area.ENVIRONMENT, "Publishing graphics configuration",
        "Nothing on it reads a draft. The display comes from a revision "
        "released to this workstation, it renders through the same "
        "`DisplayRenderer` the studio uses, and clicking a PVM opens a "
        "PVM faceplate. The former DynaLive stack is archived outside "
        "`src`; it is not a second runtime."),
    _ok("deployment_rules_shared", "One set of publish/pull rules behind "
        "the display store", "azeo_operator_station.shell.deployment.DeploymentRules",
        Area.DEPLOY, "Publishing graphics configuration",
        "`DisplayStore` answers four store questions; every rule about "
        "when a screen may change derives from those answers, so the "
        "store cannot grow a second opinion."),
    _ok("pull_model", "Publishing MARKS configuration available; "
        "workstations pull it", "azeo_operator_station.deployment.PvmDeployment",
        Area.DEPLOY, "Publishing graphics configuration",
        "The rule: a display already on screen is never swapped "
        "underneath the operator — item 7's argument one layer up. "
        "Lives in `console/deployment.py`, which is the ONE "
        "deployment model; a second grew over the PVM store before "
        "anyone noticed this existed and has been folded back in."),
    _ok("uncached_not_pending", "An unopened display is not an update",
        "azeo_operator_station.deployment.PvmDeployment._hold", Area.DEPLOY,
        "Publishing graphics configuration",
        "`update_available` used to return True whenever the station "
        "held nothing, so the indicator lit for every display it had "
        "never opened. A badge that is always lit is a badge nobody "
        "reads. First open now ADOPTS the current revision silently, "
        "which is the product contract for uncached items."),
    _ok("refresh_context", "Refresh keeps the running context",
        "azeo_operator_station.shell.deployment.DeploymentRules.refresh", Area.DEPLOY,
        "Publishing graphics configuration",
        "Display set, theme, language and open displays survive. A "
        "refresh that dumped the operator home would be the worse "
        "interruption."),
    _ok("uncached_silent", "An uncached display fetches newest on "
        "first use, silently", "azeo_operator_station.shell.deployment.DeploymentRules.open",
        Area.DEPLOY, "Publishing graphics configuration",
        "Nobody is looking at it, so there is nothing to interrupt — "
        "and counting it toward the Refresh bubble would light the "
        "button for something the operator cannot act on."),
    _ok("offline_station", "A station that is not communicating keeps "
        "what it holds and catches up on reconnect",
        "azeo_operator_station.shell.deployment.DeploymentOffline", Area.DEPLOY,
        "Publishing graphics configuration",
        "Refreshing while offline is REFUSED rather than reporting no "
        "updates: 'no updates' is a lie the operator acts on, and the "
        "truth is 'cannot tell'."),
    _ok("wip_notify", "Work In Progress on a display, and the "
        "displays a class edit affects",
        "pvms.publishing.displays_using_class", Area.DEPLOY,
        "Publishing graphics configuration",
        "WIP ADVISES rather than refusing — Azeo auto-deselects the "
        "check box and lets a user tick it again, so `allow_wip` is "
        "that tick made explicit. Publishing clears the flag and its "
        "comment, as the publishing contract requires."),
    _ok("verify_severity", "Verification severities: only ERROR blocks "
        "a publish", "pvms.publishing.Finding", Area.DEPLOY,
        "Verification tests performed on graphics configuration",
        "Informational and Warning stay eligible. A gate that refuses "
        "on every finding gets switched off, and then nothing is "
        "checked at all — the severities are what keep the ERROR "
        "refusal credible."),
    _ok("complexity_report", "A complexity report across every display",
        "pvms.configuration.complexity_report", Area.DEPLOY,
        "Display complexity index"),
    _ok("import_export", "Export and import graphics configuration between "
        "systems", "pvms.configuration.ConfigurationLibraryStore",
        Area.DEPLOY, "Exporting and importing graphics configuration"),
    _ok("find_replace", "Find and Replace across configuration",
        "pvms.configuration.find_replace", Area.DEPLOY,
        "Finding and replacing data"),
    Item("operate_conversion", "Converting legacy operator graphics graphics",
         Status.NOT_APPLICABLE, "", Area.DEPLOY,
         "Converting graphic configuration from legacy operator graphics",
         "Needs legacy operator graphics pictures to convert."),
    Item("electronic_signature", "Electronic signatures on alarm "
         "acknowledgement", Status.NOT_APPLICABLE, "", Area.DEPLOY,
         "Applying electronic signatures",
         "A regulated-site feature requiring Azeo security."),
)


TRAINING = (
    _ok("procedure_hmi_blueprint", "Authored advisory procedure PVM, faceplate and workflow detail",
        "azeo_graphics_designer.configurator.designer.PvmConfigDesigner.new_procedure_blueprint", Area.CONTEXTUAL,
        "Procedure HMI authoring", "Seven editable classes: paired PVM/faceplate, graphical workflow, conditions/holds, tuning, trends and history. Shared theme roles, checked commands, equipment navigation and Block Help. Published route and bounded 500-step rendering tested in test_procedure_hmi.py."),
    _ok("procedure_operator_tuning", "Exposed typed memory and timer tuning with live or next-run effect",
        "azeo_operator_station.procedure_session.ProcedureSession.tune", Area.CONTEXTUAL,
        "Procedure parameter tuning", "Explicit authoring exposure; typed limits and authority checks; signed one-shot next-run queue; live changes restart qualification. Storage failure ends guidance. Covered by test_procedure_tuning.py and published HMI tests."),
    _ok("procedure_history_surfaces", "PA trends and condition evidence use shared observations and history",
        "azeo_operator_station.procedure_monitor", Area.CONTEXTUAL,
        "Procedure monitoring", "Captured values/quality, individual/group holds, blocking reason; shared historian pens, quality gaps, current numeric criteria and event markers; Process History View and stored-run review/compare/export. Covered by test_procedure_hmi.py."),
    _ok("procedure_station_session", "One advisory procedure session shared by all station presentations",
        "azeo_operator_station.procedure_session.ProcedureSession", Area.ENVIRONMENT,
        "Procedure execution", "Read-only snapshot bindings; current-run/prompt checks; view-only authority; closing views preserves execution; station close waits for final audit. Linear advisory execution only. Stored-run report review remains in the Procedures workspace."),
    _ok("training_sessions", "Repeatable exercises with snapshots, input faults and objective evidence",
        "azeo_operator_station.console.LiveStation.open_training", Area.ENVIRONMENT,
        "Engineering training sessions",
        "Uses the existing Simulation Workbench and a snapshot-capable process provider. "
        "Input faults are restored on Finish; objective completion is reviewed, not automatically graded."),
    _ok("loop_diagnostics", "Loop diagnosis and measured baseline/trial response comparison",
        "azeo_operator_station.training.LoopDiagnosticsDialog", Area.ENVIRONMENT,
        "Loop diagnosis",
        "PV/SP/OUT, modes, tracking, optional valve feedback, error integral, overshoot and settling. "
        "Bad observations and changing setpoints invalidate step-response metrics."),
    _ok("training_timeline", "Persistent training timeline, recorded trends and exportable session reports",
        "azeo_operator_station.training.TrainingDialog.review_session", Area.ENVIRONMENT,
        "Session review",
        "Correlates observed alarms, controller first-out, journal actions, tuning changes and "
        "sampled values on simulation time. Reviewing a recording sends no process writes."),
    _ok("alarm_investigation", "Timed alarm shelving, expiry, first-out inspection and response guidance",
        "azeo_operator_station.training.AlarmInvestigationDialog", Area.ENVIRONMENT,
        "Alarm investigation",
        "Extends the shared alarm registry and installed condition faceplates. "
        "Observed event order is explicitly distinguished from controller first-out."),
    _ok("engineering_assemblies", "Reusable equipment assemblies and previewed bulk control-block mapping",
        "azeo_graphics_designer.engineering_tools.AssemblyDialog", Area.PVM,
        "Engineering assemblies",
        "Existing PvmDisplay templates, member PVM links and clipboard topology are reused. "
        "Pump/VFD, vessel, compressor and turbine starters use class dimensions and explicit interfaces. "
        "Compatible tag search, theme previews, selection-only remapping and duplication validate every "
        "declared reference, including independent speed/run controls, before one undoable application. "
        "Tests: test_graphics_equipment_assemblies.py; native review: verify_equipment_assemblies.py."),
    _ok("commissioning_checklist", "Saved commissioning cases with state checks and revision-specific visual evidence",
        "azeo_graphics_designer.engineering_tools.CommissioningDialog", Area.DEPLOY,
        "Commissioning checklist",
        "Cases use the isolated TEST overlay; expected binding states and visual reviews are separate. "
        "Verify/Publish reports failed, unrun and stale results."),
    _ok("canvas_productivity", "Stable snapping, overlap selection and responsive pipe drag previews",
        "azeo_graphics_designer.studio.pointer_drag.PointerDrag", Area.PVM,
        "Canvas productivity", "Ports and manual bends preview immediately; full obstacle routing runs on release. Tested in test_graphics_productivity.py; benchmark_graphics_canvas.py measures 500/1000 objects."),
    _ok("engineering_worksheet", "Validated multi-object engineering worksheet with one-step undo",
        "azeo_graphics_designer.worksheet.WorksheetDialog", Area.PVM,
        "Engineering worksheet", "Installed PVM parameters, labels, variants, display links and authored class properties use the existing document and instance rebuild services."),
    _ok("graphics_quick_access", "Command search, recent components and favorite property navigation",
        "azeo_graphics_designer.quick_access.CommandSearch", Area.PVM,
        "Quick access", "Ctrl+K reaches all ribbon commands. Property search focuses existing editors; class selections show inherited/instance state and reset."),
    _ok("pvm_change_impact", "Saved/proposed class impact with affected instances and incompatible choices",
        "azeo_graphics_designer.configurator.impact.ImpactDialog", Area.PVM,
        "PVM impact", "Compares current draft consumers, class previews and resolved values before saving. Removed instance choices block save; existing display publishing remains authoritative."),
    _ok("visual_state_sequences", "Saved timed TEST sequences with ramps, alarm lifecycle and visual evidence",
        "azeo_graphics_designer.sequence_tools.SequenceDialog", Area.DEPLOY,
        "Visual sequences", "Uses render-only overrides, restores prior TEST state and never writes the process. Captures are evidence, not automatic commissioning passes."),
    _ok("visual_revision_review", "Side-by-side display revisions with semantic changes and canvas navigation",
        "azeo_graphics_designer.revision_review.RevisionReview", Area.DEPLOY,
        "Revision review", "Read-only Operator Live renderer and existing publish gate. Compares display configuration using current process values and class definitions."),
)

ITEMS = (LIBRARY + PROPERTY + ELEMENT + DISPLAY + ENVIRONMENT + PVM
         + HP_ANATOMY + FUNCTION_BLOCK + AUTHORING + CONTEXTUAL
         + DEPLOY + TRAINING)


def of_status(status: Status) -> tuple:
    return tuple(i for i in ITEMS if i.status is status)


def of_area(area: Area) -> tuple:
    return tuple(i for i in ITEMS if i.area is area)


def unresolved() -> tuple:
    """The work front: missing, model-only, or drawn but undriven.

    Departures and not-applicables are deliberately absent — leaving a
    decision on the work front invites somebody to close it by undoing
    the decision.
    """
    return tuple(i for i in ITEMS
                 if i.status in (Status.MISSING, Status.MODEL_ONLY,
                                 Status.DISPLAY_ONLY))


def qualified_implementation(dotted: str) -> str:
    """Return the import path represented by a capability-ledger location."""

    prefix = "azeo_control_trainer." if dotted.startswith("azeo_") \
        else "azeo_control_trainer.core.hmi."
    return prefix + dotted


def counts() -> dict:
    out = {s: 0 for s in Status}
    for item in ITEMS:
        out[item.status] += 1
    return {s.value: n for s, n in out.items() if n}


def report() -> str:
    """The whole inventory, grouped by area."""
    lines = [f"Azeo HMI capability — {len(ITEMS)} items", ""]
    for area in Area:
        rows = of_area(area)
        if not rows:
            continue
        lines.append(f"{area.value}")
        for item in rows:
            lines.append(f"  [{item.status.value:<14}] {item.label}")
            if item.notes:
                lines.append(f"       {item.notes}")
        lines.append("")
    lines.append("Totals: " + ", ".join(
        f"{n} {name.lower()}" for name, n in counts().items()))
    return "\n".join(lines)


def markdown_inventory() -> str:
    """Generate the documentation tables from this authoritative ledger."""
    def cell(value: str) -> str:
        return str(value).replace("|", "\\|").replace("\n", " ")

    lines = ["## The inventory", ""]
    for area in Area:
        rows = of_area(area)
        if not rows:
            continue
        lines.extend((f"### {area.value}", "",
                      "| Item | Status | Where |",
                      "| --- | --- | --- |"))
        for item in rows:
            where = f"`{cell(item.implementation)}`" \
                if item.implementation else ""
            lines.append(
                f"| {cell(item.label)} | {item.status.value} | {where} |")
            if item.notes:
                lines.append(f"| | | *{cell(item.notes)}* |")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"
