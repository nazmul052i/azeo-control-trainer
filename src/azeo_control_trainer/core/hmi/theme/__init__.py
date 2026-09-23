"""The theme system: Standards as roles, one value per theme.

Azeo displays never name a colour; they name a **Standard**, and
each theme supplies that standard's value. `Role` is the standard
name here and `THEMES[theme][Role]` the value, which is why a
theme switch can re-skin a running session (`service.py`).

Promoted out of the DynaLive package when that stack was archived:
it was never DynaLive's, both consoles read it.
"""
