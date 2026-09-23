# Reference drawing package (archived)

Conceptual training P&IDs and high performance HMI layout sheets from
`AzeoPlant_Process_HMI_Assets` (Azeo Control Designer), kept for design
provenance. Vector redraws at a 1448 × 1086 viewBox with
`preserveAspectRatio="xMidYMid meet"`; `SOURCE_README.md` is the
package's own description.

These sheets were once shown in-app by a View → Reference drawings
viewer. That viewer was removed: the live unit P&ID tabs, the generated
high-fidelity P&ID set in `docs/PID/` and the live exports from
`tools/export_pids.py` cover the same ground with the real tag schedule
and live values, whereas these sheets carry **concept tag numbers**
(`FC1`, `FV-101`) that do not exist in the simulator tag database, show
**fixed example values** baked into the SVG text, and contain no element
ids that could ever be bound to a tag. Keeping them in the operator's
reach invited trainees to look up tags that do not exist.

## Sheets

| Sheet | Live equivalent |
| --- | --- |
| `AzeoPlant_Plant_Overview` | Plant overview tab |
| `AzeoPlant_D1_Feed_Drum_PID` | U100 |
| `AzeoPlant_C1_Compressor_PID` | U200 |
| `AzeoPlant_H1_Fired_Heater_PID` | U300 |
| `AzeoPlant_R1_Reactor_PID` | U400 |
| `AzeoPlant_D3_Separator_PID` | U400 |
| `AzeoPlant_T1_Column_PID` | U500 |
| `AzeoPlant_T2_Column_PID` | U600 |
| `AzeoPlant_B1_Steam_Boiler_PID` | U700 |
| `AzeoPlant_HMI_L1_Overview` | none (style reference) |
| `AzeoPlant_HMI_L2_Area_Overview` | none (style reference) |
| `AzeoPlant_HMI_L3_Heater_Reactor_Detail` | none (style reference) |
| `AzeoPlant_HMI_L4_Support_Diagnostics` | none (style reference) |

No sheet covers U010 (fuel gas header) or U800 (effluent and SIS), which
the live displays do cover.
