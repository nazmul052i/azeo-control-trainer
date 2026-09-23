"""Qt-free model layer: alarms, tags, quality, expressions.

The ISA-18.2 alarm state machine, the tag definition/snapshot
pair, signal quality and the display-expression evaluator. All of
it is Qt-free and unit-testable on purpose — a state machine that
can only be exercised through a running console is one nobody
exercises.

Promoted out of the DynaLive package when that stack was archived.
"""
