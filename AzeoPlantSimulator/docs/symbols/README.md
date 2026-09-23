# AzeoPlant symbol library

One standalone SVG per process symbol, split out of `docs/ProcessSymbols.svg`
by `tools/split_symbols.py`. Regenerate with:

    python tools/split_symbols.py --sheet

`_contact_sheet.svg` shows every symbol on one page. `manifest.json` lists the
name, file, source draw.io shape and native size of each.

## What the split does

Each file is cropped to the symbol's own bounding box, so its `viewBox` is
`0 0 w h` and it can be placed by its top-left corner at any scale. Two
things are cleaned on the way out, without which the symbols are only usable
inside draw.io:

- **Adaptive colour CSS is stripped.** draw.io writes
  `style="fill: var(--ge-adaptive-bg, #ffffff); stroke: light-dark(...)"` for
  dark mode. Only a browser resolves those, so every other renderer falls back
  to black and the symbol becomes a filled silhouette. The plain `fill` and
  `stroke` presentation attributes underneath are correct and are kept.
- **Placeholder labels are removed.** Tag text such as "TI ##" was rendered as
  `<switch>`/`<foreignObject>` HTML, which most renderers drop anyway. The
  symbols are clean geometry; the drawing that places them supplies its own
  text.

Every emitted file is checked to be well-formed XML and to load in a real SVG
renderer before the tool reports success.

## The symbols

| Group | Files |
|---|---|
| Instruments | `instrument_discrete_room`, `instrument_discrete_field`, `instrument_shared_control_room`, `instrument_shared_control_inaccessible` |
| Indicators | `indicator_instrument`, `indicator_control`, `indicator_function`, `indicator_plc` |
| Valves | `valve_gate`, `valve_control_diaphragm`, `valve_control_balanced_diaphragm`, `valve_motor_operated` |
| Vessels | `pressurized_vessel`, `vessel_horizontal`, `vessel_horizontal_2`, `tank`, `tank_domed` |
| Columns | `column_common`, `column_fixed`, `column_baffle` |
| Exchangers | `shell_and_tube_1`, `shell_and_tube_2`, `shell_and_tube_3`, `heat_exchanger`, `condenser`, `heater`, `reboiler`, `air_cooler` |
| Rotating | `centrifugal_pump_1`, `centrifugal_pump_2`, `centrifugal_pump_3`, `gas_blower`, `compressor`, `fan` |
| Fired and misc | `furnace`, `stack`, `steam_boiler`\* |

\* `steam_boiler.svg` is hand drawn in the library's style rather than
extracted from `ProcessSymbols.svg`, which has no boiler. It is a package
boiler: furnace body, steam drum dome, steam outlet riser on top and a
feedwater nozzle on the left. `split_symbols.py` only writes the names it
knows, so re-running the split leaves it alone.

## Using one

The files are plain SVG with no external references, so a symbol can be
inlined into a larger drawing by placing its contents inside a transform:

```xml
<g transform="translate(cx - w/2, cy - h/2) scale(s)">
  <!-- contents of pressurized_vessel.svg -->
</g>
```

`tools/make_pfd.py` does exactly this when it builds the process flow diagram.
