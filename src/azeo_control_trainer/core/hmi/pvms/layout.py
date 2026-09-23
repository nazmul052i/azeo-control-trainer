"""Layouts, screens, display frames and display sets.

The operator *environment*, which is a different thing from a display.
A layout is assigned to a workstation; screens stand for its physical
monitors; display frames divide a screen into regions and declare
which hierarchy level opens where. A display set is the collection of
displays available on that workstation, arranged into a navigation
hierarchy at most four levels deep.

Why this matters for a trainer more than it looks: "which frame does
this display open in, and what happened to the one that was there" is
a real thing an operator has to know and a real thing an engineer has
to configure. Without layouts our console is one window and that
question cannot be asked.

The rules encoded here are the manual's, and the two that actually
bite are:

- **A static frame never has its initial display replaced.** That is
  what an alarm banner is: permanently visible, and it stops being
  that the moment something can push it aside.
- **Routing follows the display's LEVEL, not the click.** A level-2
  display launched from anywhere in the layout lands in the frame that
  declares level 2. This is what makes navigation predictable across a
  console an operator did not personally configure.

Qt-free: this is the model and the routing decision. `viewer.py` and
the station render it.
"""
from __future__ import annotations

from dataclasses import dataclass, field

#: ISA-101's four levels, as the manual names them.
LEVELS = (1, 2, 3, 4)
LEVEL_TITLES = {1: "Overview", 2: "Unit control", 3: "Subsystem detail",
                4: "Diagnostics and support"}

#: A display set is at most four levels deep — the manual's own cap.
MAX_HIERARCHY_DEPTH = 4

STATIC, DYNAMIC = "static", "dynamic"

#: Where a display goes when no frame claims its level.
OTHER = "other"
NEW_WINDOW = "new_window"
NAV_HIERARCHICAL, NAV_FLAT = "hierarchical", "flat"
RELOCATE_NONE, RELOCATE_SWAP, RELOCATE_COPY = "none", "swap", "copy"


@dataclass
class DisplayFrame:
    """One region of a screen, and what may open in it."""

    name: str
    #: Fractions of the parent screen: x, y, width, height. Fractions
    #: rather than pixels because a layout must resize to whatever
    #: monitor it lands on — the manual's "one-size-fits-all".
    rect: tuple = (0.0, 0.0, 1.0, 1.0)
    kind: str = DYNAMIC
    #: Hierarchy levels this frame opens. Empty means "other displays".
    levels: tuple = ()
    navigation_bar: bool = False
    #: Hierarchical follows the active branch; flat lists the configured
    #: levels without changing shape as the operator moves through it.
    navigation_style: str = NAV_HIERARCHICAL
    #: A static frame's permanent occupant.
    initial_display: str = ""
    #: Displays launched from here open in this frame instead.
    routes_to: str = ""
    #: Automatic display coordination: keep related displays together.
    coordinates: bool = False
    #: A navigation call from this frame may be kept local even though the
    #: other frames normally coordinate with it.
    prevent_external_coordination: bool = False
    #: What to do if an automatically coordinated target is already visible.
    relocation: str = RELOCATE_NONE

    @property
    def is_static(self) -> bool:
        return self.kind == STATIC

    def accepts(self, level: int) -> bool:
        """Whether a display of this level may open here."""
        if self.is_static:
            return False        # its display never gets replaced
        return level in self.levels if self.levels else False

    @property
    def accepts_other(self) -> bool:
        return not self.is_static and not self.levels

    def to_dict(self) -> dict:
        out = {"name": self.name, "rect": list(self.rect),
               "kind": self.kind}
        if self.levels:
            out["levels"] = list(self.levels)
        if self.navigation_bar:
            out["navigation_bar"] = True
            if self.navigation_style != NAV_HIERARCHICAL:
                out["navigation_style"] = self.navigation_style
        if self.initial_display:
            out["initial_display"] = self.initial_display
        if self.routes_to:
            out["routes_to"] = self.routes_to
        if self.coordinates:
            out["coordinates"] = True
        if self.prevent_external_coordination:
            out["prevent_external_coordination"] = True
        if self.relocation != RELOCATE_NONE:
            out["relocation"] = self.relocation
        return out

    @classmethod
    def from_dict(cls, data: dict) -> "DisplayFrame":
        return cls(name=str(data.get("name", "")),
                   rect=tuple(data.get("rect", (0.0, 0.0, 1.0, 1.0))),
                   kind=str(data.get("kind", DYNAMIC)),
                   levels=tuple(data.get("levels", ())),
                   navigation_bar=bool(data.get("navigation_bar")),
                   navigation_style=str(data.get(
                       "navigation_style", NAV_HIERARCHICAL)),
                   initial_display=str(data.get("initial_display", "")),
                   routes_to=str(data.get("routes_to", "")),
                   coordinates=bool(data.get("coordinates")),
                   prevent_external_coordination=bool(data.get(
                       "prevent_external_coordination")),
                   relocation=str(data.get("relocation", RELOCATE_NONE)))


@dataclass
class Screen:
    """One physical monitor's worth of space."""

    name: str
    width: int = 1920
    height: int = 1080
    #: Position in the video desktop, in screen widths.
    origin: tuple = (0, 0)
    frames: list = field(default_factory=list)

    def add_frame(self, frame: DisplayFrame) -> DisplayFrame:
        self.frames.append(frame)
        return frame

    def frame(self, name: str):
        return next((f for f in self.frames if f.name == name), None)

    def pixel_rect(self, frame: DisplayFrame) -> tuple:
        """A frame's rectangle in this screen's pixels."""
        x, y, w, h = frame.rect
        return (int(x * self.width), int(y * self.height),
                int(w * self.width), int(h * self.height))

    def to_dict(self) -> dict:
        return {"name": self.name, "width": self.width,
                "height": self.height, "origin": list(self.origin),
                "frames": [f.to_dict() for f in self.frames]}

    @classmethod
    def from_dict(cls, data: dict) -> "Screen":
        return cls(name=str(data.get("name", "")),
                   width=int(data.get("width", 1920)),
                   height=int(data.get("height", 1080)),
                   origin=tuple(data.get("origin", (0, 0))),
                   frames=[DisplayFrame.from_dict(f)
                           for f in data.get("frames", ())])


@dataclass
class Layout:
    """The whole operator environment for one workstation."""

    name: str
    screens: list = field(default_factory=list)
    #: Blink rates live here (the manual's Styling tab), so a blink
    #: animation resolves against the layout rather than a literal.
    blink_standard_ms: int = 500
    blink_alternate_ms: int = 125
    #: Layout-scope variables, reachable as `Lyt.<name>`.
    variables: list = field(default_factory=list)
    contextual_pinning: bool = False

    def add_screen(self, screen: Screen) -> Screen:
        self.screens.append(screen)
        return screen

    def frames(self) -> list:
        return [f for s in self.screens for f in s.frames]

    def frame(self, name: str):
        return next((f for f in self.frames() if f.name == name), None)

    def screen_for_frame(self, name: str):
        return next((screen for screen in self.screens
                     if screen.frame(name) is not None), None)

    # --------------------------------------------------------- routing
    def route(self, level: int, from_frame: str = "") -> str:
        """Which frame a display of `level` opens in.

        The manual's order: an explicit route from the launching frame
        wins, then the frame that declares this level, then the frame
        for other displays, then a new window. Returning a NAME (or the
        `new_window` sentinel) rather than doing anything keeps this
        testable without a screen attached.
        """
        source = self.frame(from_frame) if from_frame else None
        if source is not None and source.routes_to:
            target = self.frame(source.routes_to)
            if target is not None and not target.is_static:
                return target.name
        for frame in self.frames():
            if frame.accepts(level):
                return frame.name
        for frame in self.frames():
            if frame.accepts_other:
                return frame.name
        return NEW_WINDOW

    def coordinated_frames(self) -> list:
        """Frames that follow hierarchy changes together."""
        return [f.name for f in self.frames() if f.coordinates]

    def coordination_targets(self, display_set, display: str,
                             recent_children=None) -> dict[str, str]:
        """Related display for every coordinated frame.

        Ancestors fill higher-level frames. A lower-level frame reopens the
        most recently visited child along the same branch, which is the
        manual's L1/L2/L3/L4 coordination rule without making a display know
        which physical monitor it happens to occupy.
        """
        if display_set is None or display_set.find(display) is None:
            return {}
        recent = recent_children or {}
        trail = display_set.breadcrumb(display)
        by_level = {display_set.level_of(name): name for name in trail}
        current = display
        while current in recent:
            child = recent[current]
            if display_set.parent_of(child) is None \
                    or display_set.parent_of(child).display != current:
                break
            by_level[display_set.level_of(child)] = child
            current = child
        targets = {}
        for frame in self.frames():
            if not frame.coordinates or frame.is_static:
                continue
            candidates = [(level, name) for level, name in by_level.items()
                          if frame.accepts(level)]
            if candidates:
                targets[frame.name] = max(candidates)[1]
        return targets

    def to_dict(self) -> dict:
        out = {"layout": self.name,
               "screens": [s.to_dict() for s in self.screens]}
        if self.blink_standard_ms != 500:
            out["blink_standard_ms"] = self.blink_standard_ms
        if self.blink_alternate_ms != 125:
            out["blink_alternate_ms"] = self.blink_alternate_ms
        if self.variables:
            out["variables"] = list(self.variables)
        if self.contextual_pinning:
            out["contextual_pinning"] = True
        return out

    @classmethod
    def from_dict(cls, data: dict) -> "Layout":
        return cls(name=str(data.get("layout", "")),
                   screens=[Screen.from_dict(s)
                            for s in data.get("screens", ())],
                   blink_standard_ms=int(
                       data.get("blink_standard_ms", 500)),
                   blink_alternate_ms=int(
                       data.get("blink_alternate_ms", 125)),
                   variables=list(data.get("variables", ())),
                   contextual_pinning=bool(
                       data.get("contextual_pinning")))


# ------------------------------------------------------------ display set
@dataclass
class DisplayNode:
    """One display's place in a navigation hierarchy."""

    display: str
    level: int = 1
    children: list = field(default_factory=list)
    #: A name for a display that does not exist yet. The manual allows
    #: it explicitly: create the display later, publish it, and the set
    #: picks it up without being republished.
    placeholder: bool = False

    def walk(self):
        yield self
        for child in self.children:
            yield from child.walk()

    def to_dict(self) -> dict:
        out = {"display": self.display, "level": self.level}
        if self.placeholder:
            out["placeholder"] = True
        if self.children:
            out["children"] = [c.to_dict() for c in self.children]
        return out

    @classmethod
    def from_dict(cls, data: dict) -> "DisplayNode":
        return cls(display=str(data.get("display", "")),
                   level=int(data.get("level", 1)),
                   placeholder=bool(data.get("placeholder")),
                   children=[cls.from_dict(c)
                             for c in data.get("children", ())])


class HierarchyError(ValueError):
    """A display set rule the manual states, broken."""


@dataclass
class DisplaySet:
    """What is available on one workstation, and how it is arranged."""

    name: str
    #: Zero or more hierarchies, each with its own overview display.
    roots: list = field(default_factory=list)
    #: Displays present but outside the hierarchy — reachable by
    #: search, never on the navigation bar.
    non_hierarchical: list = field(default_factory=list)

    # ------------------------------------------------------ authoring
    def add_root(self, display: str) -> DisplayNode:
        """Start a hierarchy. Multiple roots are allowed and useful —
        one plant-wide set with a hierarchy per process area."""
        self._refuse_duplicate(display)
        node = DisplayNode(display, level=1)
        self.roots.append(node)
        return node

    def add_child(self, parent: DisplayNode, display: str) -> DisplayNode:
        level = parent.level + 1
        if level > MAX_HIERARCHY_DEPTH:
            raise HierarchyError(
                f"a display set is at most {MAX_HIERARCHY_DEPTH} levels "
                f"deep; {display} would be level {level}")
        self._refuse_duplicate(display)
        node = DisplayNode(display, level=level)
        parent.children.append(node)
        return node

    def add_non_hierarchical(self, display: str) -> None:
        self._refuse_duplicate(display)
        self.non_hierarchical.append(display)

    def _refuse_duplicate(self, display: str) -> None:
        """A display appears at most once in a single set.

        It may belong to several sets, and sit at a different level in
        each — but twice in one set has no meaning, because its
        position is what decides where it opens.
        """
        if display in self.displays():
            raise HierarchyError(
                f"{display} is already in display set {self.name!r}")

    # -------------------------------------------------------- queries
    def nodes(self):
        for root in self.roots:
            yield from root.walk()

    def displays(self) -> list:
        return [n.display for n in self.nodes()] + list(
            self.non_hierarchical)

    def find(self, display: str):
        return next((n for n in self.nodes() if n.display == display),
                    None)

    def level_of(self, display: str) -> int:
        """A hierarchical display's level; 0 when non-hierarchical."""
        node = self.find(display)
        return node.level if node is not None else 0

    def parent_of(self, display: str):
        for node in self.nodes():
            if any(c.display == display for c in node.children):
                return node
        return None

    def breadcrumb(self, display: str) -> list:
        """Root-to-display path — what the navigation bar shows."""
        trail = []
        current = self.find(display)
        while current is not None:
            trail.append(current.display)
            current = self.parent_of(current.display)
        return list(reversed(trail))

    def siblings(self, display: str) -> list:
        parent = self.parent_of(display)
        if parent is not None:
            return [c.display for c in parent.children]
        return [r.display for r in self.roots]

    def depth(self) -> int:
        return max((n.level for n in self.nodes()), default=0)

    # ------------------------------------------------------ persistence
    def to_dict(self) -> dict:
        out = {"display_set": self.name,
               "hierarchy": [r.to_dict() for r in self.roots]}
        if self.non_hierarchical:
            out["non_hierarchical"] = list(self.non_hierarchical)
        return out

    @classmethod
    def from_dict(cls, data: dict) -> "DisplaySet":
        return cls(name=str(data.get("display_set", "")),
                   roots=[DisplayNode.from_dict(r)
                          for r in data.get("hierarchy", ())],
                   non_hierarchical=list(
                       data.get("non_hierarchical", ())))


def open_target(layout: Layout, display_set: DisplaySet, display: str,
                from_frame: str = "") -> str:
    """Where `display` opens: a frame name, or `new_window`.

    The two halves of the answer come from different places, which is
    the point — the SET says what level a display is, the LAYOUT says
    which region shows that level. Change the console and the displays
    do not move; change the hierarchy and they do.
    """
    level = display_set.level_of(display) if display_set else 0
    if not level:
        for frame in layout.frames():
            if frame.accepts_other:
                return frame.name
        return NEW_WINDOW
    return layout.route(level, from_frame)


@dataclass
class WorkstationAssignment:
    """The layout and selectable display sets deployed to one console."""

    workstation: str
    layout: str = ""
    display_sets: list = field(default_factory=list)
    active_display_set: str = ""

    def __post_init__(self) -> None:
        self.display_sets = list(dict.fromkeys(self.display_sets))
        if self.active_display_set not in self.display_sets:
            self.active_display_set = (
                self.display_sets[0] if self.display_sets else "")

    def to_dict(self) -> dict:
        out = {"workstation": self.workstation}
        if self.layout:
            out["layout"] = self.layout
        if self.display_sets:
            out["display_sets"] = list(self.display_sets)
        if self.active_display_set:
            out["active_display_set"] = self.active_display_set
        return out

    @classmethod
    def from_dict(cls, data: dict) -> "WorkstationAssignment":
        return cls(workstation=str(data.get("workstation", "")),
                   layout=str(data.get("layout", "")),
                   display_sets=list(data.get("display_sets", ())),
                   active_display_set=str(
                       data.get("active_display_set", "")))


class LayoutStore:
    """Layouts and display sets on disk, beside the displays.

    Sidecars rather than keys inside a display document, for the same
    reason `_displays.json` is a sidecar: a layout is not part of any
    one display, and inventing top-level keys in someone else's format
    gets them dropped on the next load.
    """

    def __init__(self, root):
        from pathlib import Path
        self.root = Path(root)

    # ---------------------------------------------------------- layouts
    @property
    def layouts_path(self):
        return self.root / "_layouts.json"

    @property
    def sets_path(self):
        return self.root / "_display_sets.json"

    @property
    def assignments_path(self):
        return self.root / "_workstations.json"

    def _read(self, path) -> list:
        import json
        if not path.exists():
            return []
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:                           # noqa: BLE001
            return []
        return data if isinstance(data, list) else []

    def _write(self, path, rows) -> None:
        import json
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(rows, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8", newline="\n")

    def layouts(self) -> list:
        found = [Layout.from_dict(d) for d in self._read(
            self.layouts_path)]
        # A station with no layout still has one, the manual's own
        # "No Layout" behaviour — named honestly rather than implied.
        return found or [default_layout()]

    def has_authored_layout(self, name: str) -> bool:
        """Whether ``name`` is persisted, excluding the virtual Default."""
        return any(data.get("layout") == name
                   for data in self._read(self.layouts_path))

    def save_layout(self, layout: Layout) -> None:
        from .configuration import InstalledItems
        InstalledItems(self.root).assert_editable("layout", layout.name)
        rows = [d for d in self._read(self.layouts_path)
                if d.get("layout") != layout.name]
        rows.append(layout.to_dict())
        self._write(self.layouts_path, rows)

    def layout(self, name: str):
        return next((one for one in self.layouts() if one.name == name),
                    None)

    def delete_layout(self, name: str) -> bool:
        from .configuration import InstalledItems
        InstalledItems(self.root).assert_editable("layout", name)
        rows = self._read(self.layouts_path)
        kept = [data for data in rows if data.get("layout") != name]
        if len(kept) == len(rows):
            return False
        self._write(self.layouts_path, kept)
        return True

    def display_sets(self) -> list:
        return [DisplaySet.from_dict(d) for d in self._read(
            self.sets_path)]

    def save_display_set(self, display_set: DisplaySet) -> None:
        from .configuration import InstalledItems
        InstalledItems(self.root).assert_editable(
            "display_set", display_set.name)
        rows = [d for d in self._read(self.sets_path)
                if d.get("display_set") != display_set.name]
        rows.append(display_set.to_dict())
        self._write(self.sets_path, rows)

    def display_set(self, name: str):
        return next((one for one in self.display_sets()
                     if one.name == name), None)

    def delete_display_set(self, name: str) -> bool:
        from .configuration import InstalledItems
        InstalledItems(self.root).assert_editable("display_set", name)
        rows = self._read(self.sets_path)
        kept = [d for d in rows if d.get("display_set") != name]
        if len(kept) == len(rows):
            return False
        self._write(self.sets_path, kept)
        return True

    # ----------------------------------------------------------- creation
    def create(self, name: str, *, kind: str):
        """Create a usable configuration without overwriting one by name."""
        name = str(name or "").strip()
        if not name:
            return None
        if kind == "layout":
            if self.layout(name) is not None:
                return None
            created = default_layout(name)
            self.save_layout(created)
            return created
        if kind == "display set":
            if self.display_set(name) is not None:
                return None
            created = DisplaySet(name)
            self.save_display_set(created)
            return created
        raise ValueError(f"unknown graphics configuration kind {kind!r}")

    # --------------------------------------------------------- workstations
    def assignments(self) -> list:
        return [WorkstationAssignment.from_dict(data)
                for data in self._read(self.assignments_path)]

    def assignment(self, workstation: str) -> WorkstationAssignment:
        found = next((one for one in self.assignments()
                      if one.workstation == workstation), None)
        return found or WorkstationAssignment(workstation)

    def assign(self, workstation: str, *, layout: str = "",
               display_sets=(), active_display_set: str = "") \
            -> WorkstationAssignment:
        """Assign one layout and one or more selectable sets to a console."""
        workstation = str(workstation or "").strip()
        if not workstation:
            raise ValueError("a workstation assignment needs an id")
        if layout and self.layout(layout) is None:
            raise ValueError(f"layout {layout!r} does not exist")
        names = list(dict.fromkeys(str(name) for name in display_sets))
        missing = [name for name in names if self.display_set(name) is None]
        if missing:
            raise ValueError(f"display set(s) do not exist: {missing}")
        assignment = WorkstationAssignment(
            workstation, layout, names, active_display_set)
        rows = [data for data in self._read(self.assignments_path)
                if data.get("workstation") != workstation]
        rows.append(assignment.to_dict())
        self._write(self.assignments_path, rows)
        return assignment

    def choose_display_set(self, workstation: str, name: str) -> bool:
        assignment = self.assignment(workstation)
        if name not in assignment.display_sets:
            return False
        self.assign(workstation, layout=assignment.layout,
                    display_sets=assignment.display_sets,
                    active_display_set=name)
        return True


def default_layout(name: str = "Default") -> Layout:
    """The console this repo already had, expressed as a layout.

    One screen, an alarm banner pinned across the top as a STATIC
    frame, a main region for levels 1-3, and a side region for
    everything else. It exists so the runtime has something to route
    against before anyone configures a layout — the manual's own
    "No Layout" behaviour, but honest about what it is.
    """
    layout = Layout(name)
    screen = layout.add_screen(Screen("Screen 1"))
    screen.add_frame(DisplayFrame(
        "Alarm banner", rect=(0.0, 0.0, 1.0, 0.06), kind=STATIC,
        initial_display="AlarmBanner"))
    screen.add_frame(DisplayFrame(
        "Main", rect=(0.0, 0.06, 0.78, 0.94), kind=DYNAMIC,
        levels=(1, 2, 3), navigation_bar=True, coordinates=True))
    screen.add_frame(DisplayFrame(
        "Side", rect=(0.78, 0.06, 0.22, 0.94), kind=DYNAMIC))
    return layout
