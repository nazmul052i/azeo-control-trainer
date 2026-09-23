"""Load errors.

One hierarchy, in its own module because the loader spans three files and all
three raise from it. Everything derives from :class:`DisplayLoadError` so a
caller that only wants "this file is not loadable" catches one class, while
Studio can catch the specific one and put the cursor on the offending object.

The design rule behind all of them: **a display file is either understood or
refused.** There is no partial load. A silently dropped object is a missing
process value on a console, and nobody is watching a place where nothing is
drawn.
"""
from __future__ import annotations


class DisplayLoadError(ValueError):
    """A display file could not be loaded. Never raised without a message
    naming what was wrong and where."""


class UnknownSchemaVersion(DisplayLoadError):
    """`meta.schema` is missing or is not a version this build understands.

    Deliberately not a guess. A file from a future version may look loadable
    and mean something different — the one field that changed could be the
    one that says which tag a valve is bound to.
    """


class UnknownObjectType(DisplayLoadError):
    """An object names a type that is not in the dynamo registry."""


class UnknownProperty(DisplayLoadError):
    """A prop is not in the vocabulary, or is not accepted by this type."""


class StyleFieldRejected(UnknownProperty):
    """A prop that `docs/03-display-schema.md` deliberately excludes.

    Its own class because the message has to explain invariant I2 rather than
    read as a typo: someone reaching for `colour` is not making a mistake,
    they are asking for something the file format will not do.
    """


class PropertyValueError(DisplayLoadError):
    """A prop has the right name and the wrong type or an out-of-range value."""


class DuplicateObjectId(DisplayLoadError):
    """Two objects share an id within one display.

    Ids need only be unique per file — templates will reuse them across files
    — but within a file they address an object, and two answers is no answer.
    """
