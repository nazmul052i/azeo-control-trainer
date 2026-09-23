# Azeo Process-Control Worked Example

A complete, loadable Control Designer area that reproduces the plant and the
workshops for the bundled two-area training process. The modules, verification
steps, and executable tests in this directory are the maintained source of
truth; no external course document is required.

Regenerate with:

```
.\.venv\Scripts\python.exe tools/gen_azeo_training.py
```

Verify with:

```
.\.venv\Scripts\python.exe tests/_smoke_azeo_training.py
```

The test does not merely load the modules — it walks the **Module 6 verification
scenario** against the live MTR-102:
open XV-101, fill Tank101 to 500 gal, start the pump, close XV-101 and confirm
the interlock, then drop the level to 10 gal and confirm the second interlock.

## Layout

| Area | Module | Training lesson |
|------|--------|-------------|
| PLANT_AREA_A | `control/LI-101.json` | Module 2 — Tank101 level transmitter, HI/HI-HI/LO/LO-LO alarms |
| PLANT_AREA_A | `control/XV-101.json` | Module 6 — inlet block valve, two-state Device Control |
| PLANT_AREA_A | **`control/MTR-102.json`** | **Module 6 — Workshop "Creating MTR-102"** |
| PLANT_AREA_A | `control/XV-OPTION.json` | Module 6 — optional workshop (Flush/Hold) |
| PLANT_AREA_A | `control/FIC-102.json` | Module 7 — discharge flow control |
| PLANT_AREA_B | `control/LIC-201.json` | Module 9 — Tank201 level (cascade primary) |
| PLANT_AREA_B | `control/MTR-203.json` | Module 11 — Tank201 transfer pump |
| PLANT_AREA_B | `equipment/Plant_Startup_Shutdown.json` | Module 11 — plant start-up / shutdown EM |

## MTR-102 — pump start/stop with permissives and interlocks

This module teaches interlocking with Condition blocks, a Boolean Fan Input
(BFI) first-out trap, and OR blocks for bypass indication. It is built from
exactly those blocks — `DEVCTL` (the DC block),
two `CND` conditions, a `BFI` first-out trap, and two `OR` blocks:

```
DI XV-101 position ─► CND1  "XV-101 is closed"          ─┬─► OR_ILK ─► NOT ─► DC1.INTERLOCK
AI LI-101 level    ─► CND2  "Tank101 level is <50", 4 s ─┘                    (True = permit)
                            │
                            └────────────► BFI1 ─► FIRST_OUT   (which cause tripped first)

DI bypass 1, bypass 2 ─► OR_BYPASS ─► "bypass active" lamp
                      └► CND1/CND2 DISABLE   (a bypassed condition cannot assert)
```

The two conditions are configured in step 4 of the workshop:

| Block | DESC | Time Duration | Azeo expression |
|-------|------|---------------|-------------------|
| CND1 | XV-101 is closed | — | `'//XV-101/DI1/PV_D.CV'=0` |
| CND2 | Tank101 level is <50 | 4 Secs | `'//LI-101/AI1/PV.CV'<50` |

### One deliberate divergence

Azeo resolves `'//MODULE/BLOCK/PARAM.CV'` external references inside the
expression text. This platform **wires signals between blocks** instead, so the
expressions are transcribed onto the wired `IN1` terminal — `IN1 == 0` and
`IN1 < 50` — rather than pasted verbatim. The `TIME_TRUE` parameter carries the
workshop's 4-second duration unchanged. See `ConditionBlock`'s docstring,
section "Expression language (CND-5)".

`DEVCTL.INTERLOCK` follows the Azeo polarity: **True = permit**. The CND
outputs assert on the *bad* condition, so the OR of the active conditions is
inverted before it reaches the DC block.

## Store tags

The modules read and write plain store tags (`LI-101.PV`, `XV-101.PV_D`,
`MTR-102.cmd_start`, …). They are not bound to a simulation engine — this area
is a teaching example for Control Designer, so drive the tags by hand, from a
script, or from the Watch window.
