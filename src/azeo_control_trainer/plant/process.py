"""What a plant *is*, as far as the trainer is concerned.

`DECISIONS.md` D2 says the trainer ships the engineering environment and tag
values come from outside, behind ``SharedDataStore``. This module is the shape
of that "outside": a plant declares which tags it reads, which it writes, and
how to advance one step. Nothing above this line knows what process is behind
it — swapping the shipped example for an OPC UA client or real I/O means
supplying the same two lists.

The read/write split is the load-bearing part, and it is not symmetrical with
the tag database's:

*   The plant **writes** measurements and feedbacks — the things instruments
    report. Those are tags the controller reads and nothing in the controller
    drives.
*   The plant **reads** the controller's outputs — contactors, solenoids,
    valve demands.
*   The plant touches **neither** operator commands (a faceplate's START
    button, a bypass switch) nor tags a controller output already owns.
    Writing those would mean the plant and the operator fighting over one
    point, and the operator losing every scan.

``verify_against(tagdb)`` checks that split against the configuration rather
than trusting the author of a plant to have got it right.
"""
from __future__ import annotations

import logging
from typing import Iterable

log = logging.getLogger("plant.process")


class Plant:
    """Base class for a simulated process behind the tag store."""

    #: Short identifier used to select a plant from the command line.
    key: str = ""
    #: Human-facing name.
    display_name: str = ""
    #: One-line description of the process.
    description: str = ""
    #: The strategy area this plant is built to drive, if any.
    area: str = ""

    def reads(self) -> list[str]:
        """Store tags this plant consumes — controller outputs."""
        raise NotImplementedError

    def writes(self) -> list[str]:
        """Store tags this plant drives — measurements and feedbacks."""
        raise NotImplementedError

    def initial_values(self) -> dict:
        """Tag values to seed before the first scan.

        Engines publish their first values after the strategy is already
        online, which is why the bridge has a startup grace scan. Seeding here
        closes that window for a plant that starts with the trainer.
        """
        return {}

    def step(self, dt: float, inputs: dict) -> dict:
        """Advance the process by ``dt`` seconds.

        ``inputs`` holds the current value of every tag in :meth:`reads`.
        Returns the new value of every tag in :meth:`writes`.
        """
        raise NotImplementedError

    def reset(self) -> None:
        """Return to initial conditions."""

    #: Upsets this plant can inject, as ``{key: description}``. A plant with
    #: none simply offers no scenarios.
    UPSETS: dict = {}

    def inject(self, upset: str, active: bool = True) -> bool:
        """Start or clear a process upset. False if it is not offered."""
        return False

    def clear_upsets(self) -> None:
        """Return every injected upset to normal."""

    # ------------------------------------------------------------ checking
    def verify_against(self, tagdb) -> list[str]:
        """Complaints about how this plant lines up with the configuration.

        Returns a list of human-readable problems; empty means the plant and
        the loaded modules agree. This is what stops the two drifting: a
        module rewired to a new tag shows up here rather than as a signal that
        silently stopped moving.
        """
        problems: list[str] = []
        field_tags = tagdb.field_tags()

        # Every tag the controller reads and nothing drives must be supplied.
        controller_reads: set[str] = set()
        controller_writes: set[str] = set()
        for tag, paths in field_tags.items():
            for path in paths:
                entry = tagdb.lookup(path)
                if entry is None:
                    continue
                if entry.direction == "input":
                    controller_reads.add(tag)
                elif entry.direction == "output":
                    controller_writes.add(tag)

        writes = set(self.writes())
        reads = set(self.reads())

        for tag in sorted(writes & controller_writes):
            problems.append(
                f"{tag}: the controller drives this tag; the plant must not "
                f"write it too")
        for tag in sorted(writes - controller_reads):
            problems.append(
                f"{tag}: the plant writes it, but no input block reads it")
        for tag in sorted(reads - controller_writes):
            problems.append(
                f"{tag}: the plant reads it, but no output block writes it")

        # A tag the controller reads that neither the plant nor another module
        # drives is an operator command — a START button, a bypass switch, a
        # field permissive. Those are supplied by a person, so the plant is
        # right not to drive them. It still has to *seed* them: an unseeded
        # tag reads as missing, and past the startup grace window a missing
        # tag is Bad, so a student's first sight of the module would be a wall
        # of bad quality that has nothing to do with the process.
        seeded = set(self.initial_values())
        for tag in sorted(controller_reads - writes - controller_writes):
            if tag not in seeded:
                problems.append(
                    f"{tag}: an operator command that the plant never seeds — "
                    f"it will read Bad until someone writes it")
        return problems

    def operator_tags(self, tagdb) -> list[str]:
        """Tags a person owns: read by the controller, driven by nobody.

        Useful to a UI that wants to offer exactly the field pushbuttons this
        process leaves to the operator.
        """
        driven = set(self.writes())
        owned: list[str] = []
        for tag, paths in tagdb.field_tags().items():
            entries = [tagdb.lookup(p) for p in paths]
            entries = [e for e in entries if e is not None]
            if not entries or tag in driven:
                continue
            if all(e.direction == "input" for e in entries):
                owned.append(tag)
        return sorted(owned)


class PlantRegistry:
    """The plants this build knows how to run."""

    def __init__(self) -> None:
        self._plants: dict[str, type[Plant]] = {}

    def register(self, cls: type[Plant]) -> type[Plant]:
        if not cls.key:
            raise ValueError(f"{cls.__name__} has no key")
        self._plants[cls.key] = cls
        return cls

    def get(self, key: str) -> type[Plant] | None:
        return self._plants.get(key)

    def keys(self) -> list[str]:
        return sorted(self._plants)

    def all(self) -> Iterable[type[Plant]]:
        return [self._plants[k] for k in self.keys()]


registry = PlantRegistry()
