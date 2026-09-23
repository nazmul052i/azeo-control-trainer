# Troubleshooting

## The engine froze and the status bar says FROZEN (ERROR)

A model raised on 25 consecutive steps. The engine stopped and marked every AI
and DI **bad**, deliberately, so a connected DCS sees a communications failure
rather than stale good data.

Open the **Log** panel or `logs/azeoplant.log`. The first three occurrences carry a
full traceback naming the unit. Fix the cause, then **Simulation ▸ Run** — you
will be asked to confirm, because resuming with the fault still present will
simply freeze again.

## A tag will not move

Work through this in order.

1. **Is the engine running?** Status bar, far left.
2. **Is it an AI or DI?** Those are model outputs. If the model has no reason to
   move it, it will not move.
3. **Is there a local override on it?** Overridden tags show `LOCAL OVERRIDE
   ACTIVE` on the faceplate. **Simulation ▸ Clear all local overrides** removes
   them all. This is the single most common cause after a debugging session.
4. **Is the client actually writing?** The OPC UA panel counts client writes. If
   that counter is not moving, the problem is upstream of the simulator.
5. **Is it downstream of something shut?** A charge flow of zero with a healthy
   valve usually means `XV-1001` is closed or the pump is not running.

## The valve position does not match what I wrote

It should not, immediately. Valves have stroke times of 2 to 15 seconds and MOVs
travel for 15 to 30 seconds. Watch `ZT-1001` rather than `FCV-1001` — the first
is feedback, the second is command. If they track instantly you have configured
your client to read the command back.

Stiction or hysteresis malfunctions will also hold the stem short of the
command. Check the Malfunctions panel.

## The client cannot connect

- Check the endpoint in the **OPC UA** panel matches what the client is using.
  Binding to `0.0.0.0` listens on every interface; the client still needs the
  host's actual address.
- Port 48420 must be open through any firewall.
- If **Status** shows STOPPED, the server thread failed. `Last error` in the
  same panel and the log will say why. A port already in use is the usual cause.
- On Windows a bind can also be refused with *"an attempt was made to access a
  socket in a way forbidden by its access permissions"* (WinError 10013) even
  when nothing is listening. The port sits in a range Windows has reserved,
  usually by Hyper-V or WSL. `netsh interface ipv4 show excludedportrange
  protocol=tcp` lists those ranges; pick a free port outside them with
  `--endpoint`. This is why the default is 48420 rather than the OPC UA
  standard 4840, which is frequently reserved on engineering workstations.

## Quality shows bad or uncertain when I expect good

- **Uncertain** means the reading is off-scale. That is a legitimate model
  output, not a fault — the transmitter is pinned.
- **Bad** means a failed or out-of-service transmitter, or the engine's
  fail-safe. Check the Malfunctions panel first.

## Overrun counter climbing

The engine cannot complete a step within its period. Either the speed factor is
too high for the machine or something else is loading the CPU — the status bar
shows both host CPU and this process's memory. Reduce the speed factor, or
increase `dt` a little. The models stay stable at larger steps, but above about
0.25 s the MOV travel timers and burner sequence lose resolution.

## The display stopped updating but the simulation is still running

The refresh timer suspends itself if it raises, rather than throwing an error
dialog five times a second. You will have seen one dialog explaining this. The
engine and OPC UA server are unaffected and a connected DCS still has live data.
The traceback is in the log. Restart the window to recover.

## Where the logs are

`logs/azeoplant.log`, rotating at 4 MB with five backups. The file is at DEBUG, the
console at INFO. `--verbose` puts the console at DEBUG too. asyncua is
deliberately turned down to WARNING; raise `azeoplant.opc` on its own when
investigating a session problem rather than turning everything up.

## The OPC UA server did not start: port already in use

Another simulator instance (often a forgotten `--headless` run) is holding
port 48420. The UI keeps working without OPC in that case. Find and stop
the other instance, or start this one with a different `--endpoint`.

## Loops all show MAN and PV 0.00

The engine is frozen: press Run (F5). If the engine is running and the
boxes still read MAN, check Simulation, Closed loop control (F9) - the
plant may deliberately be in open loop.
