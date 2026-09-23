"""What an operator station is entitled to run, and when it changes.

Between the engineer pressing **Publish** and an operator seeing a new
graphic there is a step that every real DCS has and that this runtime did
not: the console decides. `PublishRegistry` modelled all of it — releases,
workstation targeting, what each console has accepted — and nothing read it,
so a console loaded whatever display file was on disk and "deploy" meant
nothing at all.

Two rules carried over from `docs/11` §11.6, and they are the whole point:

- **Publishing does not change a screen.** The console shows an
  update-available indicator; the operator takes it when they are not in the
  middle of something. A graphic that redraws mid-task has taken the
  operator's attention at a moment they did not choose.
- **A console runs a revision, not a file.** What it is showing is a number
  it accepted, so "what is this station running?" has an answer that does not
  depend on when someone last touched the disk.

**This is the ONE deployment model.** A second one grew in
`pvms/deployment.py` over the PVM display store before anyone noticed
this existed; the rules live here, and a store that needs them adapts to
this class rather than restating them. Two implementations of one set of
rules is two chances to disagree — the same defect the condition table
had, one subsystem over.

An area with no `publish.json` is not an error. It is an area nobody has
published yet, and the honest thing is to run what is there and say that no
revision is under management — refusing to start would make publishing
mandatory for a training rig where it is not.
"""
from __future__ import annotations

import logging


log = logging.getLogger("azeo.console.deployment")


class DeploymentOffline(RuntimeError):
    """Asked to refresh a station that cannot see the database."""

#: The default station name. A single-console rig should not have to invent
#: one, and `ALL`-targeted releases reach it either way.
DEFAULT_WORKSTATION = "OPS-01"


#: What a station is doing about one display.
STATE_CURRENT = "current"        # holds the newest published revision
STATE_AVAILABLE = "available"    # newer exists; the operator has not taken it
STATE_UNCACHED = "uncached"      # never opened here; fetches newest on use


class DeploymentRules:
    """Azeo Operator Station's publish/pull rules, with no store in them.

    The shipping `pvms.publishing.DisplayStore` binds to these rules by
    answering four questions. The interface stays store-neutral so the
    publish/pull decision cannot leak into a document store:

        `_latest(display_id)`   the newest revision published TO US
        `_held(display_id)`     the revision this station is showing
        `_hold(display_id, r)`  record that it now shows `r`
        `_known()`              every display this station holds

    Everything below derives from those, so a second store cannot grow
    a second opinion about when a screen is allowed to change.
    """

    #: False while this station cannot reach the configuration
    #: database. It keeps running on what it holds and catches up on
    #: reconnect — worth teaching, because an operator whose console
    #: goes quiet needs to know it is still showing real values from a
    #: real controller, just not the newest *configuration*.
    communicating = True

    # ----------------------------------------------- store questions
    def _latest(self, display_id: str):
        raise NotImplementedError

    def _held(self, display_id: str):
        raise NotImplementedError

    def _hold(self, display_id: str, revision: int) -> None:
        raise NotImplementedError

    def _known(self) -> tuple:
        raise NotImplementedError

    # ------------------------------------------------------ the rules
    def state_of(self, display_id: str) -> str:
        held = self._held(display_id)
        if held is None:
            return STATE_UNCACHED
        latest = self._latest(display_id)
        if latest is None or held >= latest:
            return STATE_CURRENT
        return STATE_AVAILABLE

    def pending(self) -> tuple:
        """Displays with a newer revision waiting.

        **Only ones this station HOLDS.** An unopened display fetches
        newest on first use, so it is not something the operator can
        act on; counting it would light the indicator permanently, and
        an indicator that is always lit is one nobody reads.
        """
        return tuple(sorted(
            display_id for display_id in self._known()
            if self.state_of(display_id) == STATE_AVAILABLE))

    def has_updates(self) -> bool:
        return bool(self.pending())

    def open(self, display_id: str):
        """The revision to SHOW when the operator opens this display.

        Uncached adopts the newest silently; cached keeps what it
        holds. That second half is the whole model: a display somebody
        is looking at does not change because someone published — it
        changes when they press Refresh.
        """
        held = self._held(display_id)
        if held is not None:
            return held
        latest = self._latest(display_id)
        if latest is not None and self.communicating:
            self._hold(display_id, latest)
        return latest

    def refresh(self) -> dict:
        """Take every waiting update. Returns `{display_id: (was, now)}`.

        Refuses while not communicating: telling a station that cannot
        see the database it has no updates is a lie the operator acts
        on, and the truth is "cannot tell".
        """
        if not self.communicating:
            raise DeploymentOffline(
                "%s is not communicating; it retrieves published "
                "configuration when it reconnects"
                % getattr(self, "workstation", "this station"))
        moved = {}
        for display_id in self.pending():
            was = self._held(display_id)
            now = self._latest(display_id)
            self._hold(display_id, now)
            moved[display_id] = (was, now)
        return moved

    def reconnect(self) -> tuple:
        """Back online. Reports what is waiting; accepts nothing."""
        self.communicating = True
        return self.pending()



# `Deployed` and `Deployment` — the binding of these rules to DynaLive's
# `PublishRegistry` — moved to `archive/dynalive/` with the rest of that
# stack. The RULES stay here and are shared: a store binds by answering
# the four questions above, and a second implementation of them is two
# chances to disagree (which is what the condition table already had
# once). The PVM store binds them in `pvms/station/deployment.py`.
