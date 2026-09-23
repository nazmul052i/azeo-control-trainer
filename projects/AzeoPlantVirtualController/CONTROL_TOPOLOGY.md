# Azeo Plant Virtual Controller control topology

This file is generated with the APVC graphs. It is the human-readable companion
to `engineering/control_schemes.json`; edit the generator or source engineering,
not this generated copy.

## Forward setpoint ownership

Every slave setpoint has one writer. Percent demands are converted explicitly
to the slave's engineering-unit range before `SP_HANDOFF` unless the owning
coordinator already calculates engineering units.

| Forward coordinator | Demand/master | Slave SP | Installed path |
| --- | --- | --- | --- |
| `LIC-1001` | `LIC-1001` with `PIC-1001` override | `FIC-1001` | low select percent, then charge-flow EU |
| `PIC-2001` | `PIC-2001` | `SIC-2001` | percent to speed EU |
| `FY-3002` | `TIC-3001` | `FIC-3001` | H1 fuel cross-limit in flow EU |
| `FY-3002` | `TIC-3001` | `FIC-3003` | H1 air cross-limit in flow EU |
| `ZC-3001` | `ZC-3001` | `PIC-3001` | percent to draft-pressure EU |
| `TIC-4001` | `TIC-4001` | `FIC-4001` | percent to quench-flow EU |
| `AIC-4001` | `AIC-4001` | `TIC-4001` | percent to temperature EU |
| `TIC-5001` | `TIC-5001` | `FIC-5001` | reflux demand plus feed FF/cutback |
| `TIC-5002` | `TIC-5002` | `FIC-5002` | steam demand plus FF/decoupling/limits |
| `AIC-5001` | `AIC-5001` | `TIC-5001` | temperature EU plus pressure compensation |
| `AIC-5002` | `AIC-5002` | `TIC-5002` | temperature EU plus pressure compensation |
| `TIC-6001` | `TIC-6001` | `FIC-6001` | reflux demand plus feed FF/cutback |
| `TIC-6002` | `TIC-6002` | `FIC-6002` | steam demand plus FF/decoupling/limits |
| `AIC-6001` | `AIC-6001` | `TIC-6001` | temperature EU plus pressure compensation |
| `AIC-6002` | `AIC-6002` | `TIC-6002` | temperature EU plus pressure compensation |
| `LIC-7001` | `LIC-7001` | `FIC-7001` | percent to drum-feed EU |
| `FY-7002` | `PIC-7001` | `FIC-7002` | B1 fuel cross-limit in flow EU |
| `FY-7002` | `PIC-7001` | `FIC-7003` | B1 air cross-limit in flow EU |
| `LIC-8001` | `LIC-8001` | `FIC-8001` | percent to effluent-flow EU |

## External reset

The 19 paths have 17 distinct masters because `TIC-3001` and `PIC-7001` each
drive two coordinated slaves. External reset uses the first slave below:

| Master | Typed reset source |
| --- | --- |
| `LIC-1001` | `ctrl.FIC-1001.BKCAL_OUT` |
| `PIC-2001` | `ctrl.SIC-2001.BKCAL_OUT` |
| `TIC-3001` | `ctrl.FIC-3001.BKCAL_OUT` |
| `ZC-3001` | `ctrl.PIC-3001.BKCAL_OUT` |
| `TIC-4001` | `ctrl.FIC-4001.BKCAL_OUT` |
| `AIC-4001` | `ctrl.TIC-4001.BKCAL_OUT` |
| `TIC-5001` | `ctrl.FIC-5001.BKCAL_OUT` |
| `TIC-5002` | `ctrl.FIC-5002.BKCAL_OUT` |
| `AIC-5001` | `ctrl.TIC-5001.BKCAL_OUT` |
| `AIC-5002` | `ctrl.TIC-5002.BKCAL_OUT` |
| `TIC-6001` | `ctrl.FIC-6001.BKCAL_OUT` |
| `TIC-6002` | `ctrl.FIC-6002.BKCAL_OUT` |
| `AIC-6001` | `ctrl.TIC-6001.BKCAL_OUT` |
| `AIC-6002` | `ctrl.TIC-6002.BKCAL_OUT` |
| `LIC-7001` | `ctrl.FIC-7001.BKCAL_OUT` |
| `PIC-7001` | `ctrl.FIC-7002.BKCAL_OUT` |
| `LIC-8001` | `ctrl.FIC-8001.BKCAL_OUT` |

Each reset source is a typed `REMOTE_ANALOG` with `.quality` and `.limit`
companions. An EU-to-percent scaler precedes `BKCAL_IN`; the slave returns PV
through `BKCAL_OUT`. `TIC-3001` feedforward from `FT-1001` and `LIC-7001`
feedforward from `FT-7001` enter `PID.FF_VAL` and are excluded from reset.

## Cross-module supervisory sources

These are the only installed typed supervisory controller references:

| Source | Consumer |
| --- | --- |
| `PIC-1001.OUT` | U100 charge-flow low select |
| `PIC-2002.OUT`, `TIC-2001.OUT` | U200 recycle high select |
| `AIC-3001.OUT`, `TIC-3004.OUT` | H1 excess-air trim and skin-temperature fuel limit |
| `TIC-4002.OUT` | U400 runaway-quench high select |
| `PDIC-5001.OUT`, `PDIC-6001.OUT` | column steam flooding low selects |
| `PIC-7001.OUT`, `AIC-7001.OUT`, `AIC-7002.OUT` | B1 firing demand, excess-air trim, and CO air floor |

## Exact installed calculations

H1 uses `excess = 1.05 + 0.002*AIC-3001.OUT` and
`firing = TIC-3001.OUT/100*2500`. Fuel is the minimum of firing demand,
air-available fuel, `TIC-3004` skin limit, and the `PT-0102/2` supply factor.
Air is `max(firing, FT-3001) * 9.6 * excess / 1000`.

B1 uses the same excess-air form with `PIC-7001` demand and a 4000 flow scale.
Fuel is limited by firing, air availability, and `PT-0103/3`; air is also
floored by `AIC-7002.OUT/100*55`.

Each column applies `7.5 * (pressure - snapshot_pressure)` temperature
compensation. Flow coordinators apply 0.6 feed feedforward; the steam path adds
the documented 0.12 reflux decoupler. Reflux-drum level below 30% cuts back
reflux demand. Steam demand is the minimum of calculated demand, the PDIC
flooding limit, and 16.5 t/h.

Split ranges are explicit: `PIC-0101` drives flare `PCV-0102` on the reverse
first leg and import `PCV-0101` on the forward second leg; `PIC-3001` uses two
forward legs. `AIC-8001` drives acid in reverse over 0..45%, a 45..55% neutral
gap, fine caustic over 55..80%, and coarse caustic over 80..100%.

## Startup exceptions

| Constraint | Engineering SP | Released `out_init` |
| --- | ---: | ---: |
| `PIC-1001` | 24 | 100% |
| `PIC-2002` | 56 | 0% |
| `TIC-2001` | 185 | 0% |
| `TIC-3004` | 600 | 100% |
| `TIC-4002` | 392 | 0% |
| `PDIC-5001` | 340 | 100% |
| `PDIC-6001` | 330 | 100% |
| `AIC-7002` | 400 | 0% |
| `PIC-7002` | 37.5 | restored output demand |

Ordinary PID SP values adopt snapshot PV. `PIC-5001` is a separate engineered
setpoint exception at 8.5 bar. Its `out_init=50%` is a deliberate neutral-point,
non-bumpless commissioning deviation from the snapshot's `PCV-5001=100%`;
the Manual AOs must be checked before transfer. Cascade-master `out_init` values adopt the first
slave's as-found PV normalized to percent; direct loops and trims adopt their
as-found demand. Handoffs are armed, but remain non-owning until the downstream
loop is deliberately transferred to `CAS`.

## Evidence gaps and reserved outputs

`FY-0101`, `FY-2001`, and `PY-0101` remain
`MONITOR_ONLY_UNSPECIFIED`: the authoritative module document and simulator
strategy contain no equations for them. `SIC-7001` is
`MONITOR_ONLY_SUPERSEDED`; `FIC-7003` owns `SC-7001`.

Writable-route ownership is 157 APVC strategy outputs, 6 simulator-SIS outputs,
and 10 reserved outputs. The 16 CT1 commands belong to linked APVC Control
Module class instances; the remaining reserved paths retain restored values
because the current APVC engineering basis does not assign them a producer.
