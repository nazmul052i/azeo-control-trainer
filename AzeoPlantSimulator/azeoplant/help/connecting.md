# Connecting a DCS

## Endpoint and security

```
opc.tcp://<simulator host>:48420/plant_sim/
```

Security policy is **None** with anonymous access. That is appropriate for an
isolated training network and nothing else. Do not put this server on a plant
network without adding a security policy and user tokens first.

## Node identifiers

Every tag is a variable whose NodeId is the **tag name at namespace 2**:

```
ns=2;s=LT-1001
ns=2;s=FCV-1001
ns=2;s=ZSO-MOV1001A
ns=2;s=XY-P101A-STR
```

The browse hierarchy exists only so a human can navigate. It plays no part in
addressing:

```
Objects
  AzeoPlant
    U100
      AI
        LT-1001                    ns=2;s=LT-1001
          PctVal                   ns=2;s=LT-1001.PctVal
          Status                   ns=2;s=LT-1001.Status
          EU                       ns=2;s=LT-1001.EU
      AO
        FCV-1001                   ns=2;s=FCV-1001
      DI
      DO
    SIM
      Run, SpeedFactor, Heartbeat, ScanTimeMs, SimTime, Overruns, ModelErrors
```

### Companion nodes

| Node | Type | Purpose |
|---|---|---|
| `<TAG>` | Double or Boolean | Engineering-unit value, with status code |
| `<TAG>.PctVal` | Double | 0–100 percent of span, maps to `FIELD_VAL_PCT` |
| `<TAG>.Status` | UInt16 | 0 good, 1 uncertain, 2 bad |
| `<TAG>.EU` | String | Engineering units, read only |

`PctVal` supplies a percent-of-span value for controllers whose simulation
input expects 0–100. Use the main tag node when the client expects engineering
units. Confirm the receiving parameter's scale before connecting either one.

## Quality

Values are written as a full `DataValue` carrying a status code and a source
timestamp:

| Simulated quality | OPC UA status code |
|---|---|
| Good | `Good` |
| Uncertain | `UncertainLastUsableValue` |
| Bad | `BadNoCommunication` |

A transmitter reading off-scale reports **uncertain**; a failed or
out-of-service transmitter reports **bad**. If the engine exceeds its error
budget it freezes and marks every AI and DI **bad**, which is what a DCS would
see if the field bus died. Make sure your client is configured to raise a
quality alarm rather than silently hold the last value.

## Direction of every signal

| Kind | Direction | Writable by the client |
|---|---|---|
| AI | Simulator to DCS | No |
| DI | Simulator to DCS | No |
| AO | DCS to simulator | Yes |
| DO | DCS to simulator | Yes |

Writes to AO and DO reach the model within one publish period, and the model
then applies actuator dynamics on top: writing `FCV-1001` to 72.5 does not put
the valve there instantly, because the valve has a six second stroke.

## Engine control from the client

The `SIM` folder lets a client drive the simulator without touching the window:

| Node | Access | Purpose |
|---|---|---|
| `SIM.Run` | Read/write | True runs, false freezes |
| `SIM.SpeedFactor` | Read/write | 0.1 to 20 times real time |
| `SIM.Heartbeat` | Read | Increments every step. **Use this as a watchdog.** |
| `SIM.ScanTimeMs` | Read | Actual step execution time |
| `SIM.SimTime` | Read | Simulated seconds since start |
| `SIM.Overruns` | Read | Steps that could not keep up with real time |
| `SIM.ModelErrors` | Read | Cumulative model exceptions |

`Run` and `SpeedFactor` are bidirectional. A write commands the engine; a change
made from the window is published back. Build a watchdog on `Heartbeat` before
you build anything else — a frozen simulator with stale good-quality data is the
one failure mode that teaches a trainee the wrong lesson.

## Connect a controller

**Azeo's embedded training project** uses the configured Local Virtual I/O
provider behind the shared tag store. Start it from Explorer's Virtual I/O
commands; this route does not require a separate OPC UA server.

**An external controller or workstation** can subscribe to the simulator's
OPC UA endpoint and canonical `ns=2;s=<TAG>` nodes. Configure AI/DI as reads and
AO/DO as writes, verify engineering units or percent scaling, then check quality
and timestamps before enabling a training module. Azeo's OPC UA catalogue
import creates that same read/write map for a network connection.

The exported `controller_mapping` section contains optional simulation-route
metadata. The main `name`, `node_id`, `kind`, range and quality fields define the
connection. Azeo's importer also accepts older catalogs because it reads those
canonical fields independently of optional metadata. Controller-specific field
parameters must be checked against the receiving system's actual configuration.

## Sanity check before you build modules

1. Browse to `ns=2;s=LT-1001` and confirm you get a value with good quality and
   a moving source timestamp.
2. Watch `SIM.Heartbeat` increment.
3. Write `ns=2;s=FCV-1001` to 20, then to 80, and watch `ZT-1001` follow over
   about six seconds. If the position jumps instantly you are reading the
   command back, not the feedback.
4. Activate malfunction MF-014 and confirm `LT-1001` goes uncertain at the
   client, not just on the simulator display.
