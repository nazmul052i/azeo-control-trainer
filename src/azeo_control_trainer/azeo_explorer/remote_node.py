"""Remote controller nodes — the Control Network's other members.

A second trainer on another machine is another node on the control
network: its PK serves the whole module namespace over OPC UA
(`Modules/<MODULE>/<BLOCK>/<TERMINAL>`, quality as StatusCode), and
this side browses it — the same `UaSession` the EIOC browser dialog
uses, so there is one client codepath.

The v1 contract is **monitor and operate**: browse its modules live,
subscribe with quality, write only its declared-writable field points.
Engineering is performed at that node's own station — our UA terminals
are deliberately read-only on the wire (a wire re-asserts a written
terminal), and a remote download transport does not exist. The
Properties dialog says so rather than hiding it.

An unreachable node stays in the tree with its error spelled out: a
node you configured and cannot reach is information, not a failure to
suppress. Runtime-only for now, like the keylock — nothing here
serializes.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class RemoteNode:
    name: str
    endpoint: str
    connected: bool = False
    error: str = ""
    modules: list = field(default_factory=list)

    @property
    def host(self) -> str:
        try:
            return self.endpoint.split("//", 1)[1].split(":", 1)[0]
        except IndexError:
            return self.endpoint

    def browse(self, timeout: float = 6.0) -> bool:
        """Connect, list the Modules folder, disconnect. Sets
        connected/modules on success, error on failure — both are
        the honest answer."""
        from .opcua_browser import (
            UaSession,
        )
        session = UaSession()
        try:
            session.connect(self.endpoint)
            modules_id = next(
                (child["id"]
                 for child in session.children(None)
                 if child["name"] == "Modules"), None)
            if modules_id is None:
                raise RuntimeError(
                    "no Modules folder — not a PK namespace")
            self.modules = sorted(
                child["name"]
                for child in session.children(modules_id)
                if not child["variable"])
            self.connected = True
            self.error = ""
            return True
        except Exception as error:                  # noqa: BLE001
            self.connected = False
            self.error = str(error) or type(error).__name__
            self.modules = []
            return False
        finally:
            try:
                session.disconnect()
            except Exception:                       # noqa: BLE001
                pass
