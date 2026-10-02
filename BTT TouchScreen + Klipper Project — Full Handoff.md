# BTT TouchScreen + Klipper Project — Full Handoff

Oct 2, 2026 · @Corey Davis

## Project overview

The goal is to make a BigTreeTech (BTT) touchscreen work with a Klipper-controlled 3D printer by editing the touchscreen firmware so it sends Klipper commands directly, removing the need for a translator plugin on the Pi.

The BTT TouchScreen firmware is built around Marlin G-code. When a button like "Auto Bed Level" is pressed, it sends `G29`, which Klipper does not understand; Klipper expects `BED_MESH_CALIBRATE`. A collaborator proposed editing the firmware's C source to swap Marlin command strings for their Klipper equivalents.

This document is a complete handoff of the project so far: the approach, where to look in the source, the full command mapping, the parsing problem, recommended macros, and next steps.

## Approaches considered

Direct firmware editing was chosen as the approach to explore, because it bakes the translation into the TFT and needs no extra service running on the Pi.

| Approach | How it works | Pros | Cons |
| --- | --- | --- | --- |
| Direct firmware edit (chosen) | Change the G-code strings in the TFT's C source so buttons emit Klipper commands | No middleware; cleaner runtime | Must rebuild and reflash firmware; response parsing still needs work |
| Translator middleware (e.g. `klipper-serial-btt`) | A service on the Pi intercepts serial traffic and swaps commands | No firmware changes | Extra service to run and maintain |
| Config-file mapping (main repo issue #1555) | The firmware reads a mapping file such as `G29=BED_MESH_CALIBRATE` | Most flexible; no hardcoding | Not implemented upstream; would need building |

Some commands map cleanly: G0/G1 and most temperature and fan commands work in both systems. Others need full replacement or a Klipper macro.

## Firmware source layout

Three directories in the [BIGTREETECH-TouchScreenFirmware](https://github.com/bigtreetech/BIGTREETECH-TouchScreenFirmware/) repo hold most of what needs changing.

| Directory | What it holds |
| --- | --- |
| `TFT/src/Libraries/Marlin/` | Where most G-code strings are defined |
| `TFT/src/User/API/` | Higher-level button action handlers that call those commands |
| `TFT/src/User/Menu/` | Individual menu screens that trigger specific commands |

Workflow: search the source for a Marlin string (e.g. `"G29"`) and replace it with the Klipper equivalent (e.g. `"BED_MESH_CALIBRATE"`), using the mapping below.

## Marlin-to-Klipper command mapping

Most motion, temperature, fan, stepper-disable and speed commands already work in Klipper unchanged; bed leveling, print control, EEPROM and info commands need replacing or a macro. Status key: Same = works as-is, Partial = needs attention or a macro, None = no equivalent.

| Category | Marlin | Klipper | Status | Notes |
| --- | --- | --- | --- | --- |
| Motion | `G0` / `G1` | `G0` / `G1` | Same |  |
| Motion | `G2` / `G3` | `G2` / `G3` | Same | Needs `[gcode_arcs]` in printer.cfg |
| Motion | `G4 Pnnn` | `G4 Pnnn` | Same | Dwell in ms |
| Motion | `G28`, `G28 X/Y/Z` | `G28`, `G28 X/Y/Z` | Same |  |
| Motion | `G90` / `G91` | `G90` / `G91` | Same |  |
| Motion | `G92 E0` | `G92 E0` | Same |  |
| Bed leveling | `G29` | `BED_MESH_CALIBRATE` | Partial | Or define a `G29` macro |
| Bed leveling | `G29 L1` (load mesh) | `BED_MESH_PROFILE LOAD=default` | Partial | Profile name must match config |
| Bed leveling | `G29 S1` (save mesh) | `BED_MESH_PROFILE SAVE=default` | Partial | Profile name must match config |
| Bed leveling | `M420 S1` | `BED_MESH_PROFILE LOAD=default` | Partial | No direct toggle |
| Bed leveling | `M420 S0` | `BED_MESH_CLEAR` | Same |  |
| Bed leveling | `G30` | `PROBE` | Partial | Probes at current XY |
| Bed leveling | `M48` | `PROBE_ACCURACY` | Same |  |
| Z offset | `M851 Znnn` | `SET_GCODE_OFFSET Z=nnn` | Partial | Not persistent; save with `SAVE_CONFIG` |
| Z offset | `M290 Znnn` (babystep) | `SET_GCODE_OFFSET Z_ADJUST=nnn MOVE=1` | Partial | Relative to current offset |
| Z offset | `M290 S1` | none | None |  |
| Temperature | `M104 Snnn` | `M104 Snnn` or `SET_HEATER_TEMPERATURE HEATER=extruder TARGET=nnn` | Same | Klipper accepts M104 |
| Temperature | `M109 Snnn` | `M109 Snnn` | Same |  |
| Temperature | `M140 Snnn` | `M140 Snnn` or `SET_HEATER_TEMPERATURE HEATER=heater_bed TARGET=nnn` | Same | Klipper accepts M140 |
| Temperature | `M190 Snnn` | `M190 Snnn` | Same |  |
| Temperature | `M106 Snnn` / `M107` | `M106 Snnn` / `M107` | Same |  |
| Temperature | `M155 S` (auto-report) | none | None | Klipper reports on its own schedule |
| Filament | `M600` | `PAUSE` + manual swap | Partial | Define `M600` macro |
| Filament | `M701` / `M702` | custom macro | None | Define in printer.cfg |
| Filament | `G10` / `G11` | `G10` / `G11` | Partial | Needs `[firmware_retraction]` |
| Filament | `M207` | `SET_RETRACTION RETRACT_LENGTH=nnn` | Partial | Needs `[firmware_retraction]` |
| Filament | `M208` | `SET_RETRACTION UNRETRACT_EXTRA_LENGTH=nnn` | Partial | Needs `[firmware_retraction]` |
| Print control | `M0` / `M1` | `PAUSE` | Partial | Define macros |
| Print control | `M24` (resume) | `RESUME` | Partial | Define `M24` macro |
| Print control | `M25` (pause) | `PAUSE` | Partial | Define `M25` macro |
| Print control | `M26` | none | None |  |
| Print control | `M27` (SD status) | `printer.print_stats` | None | Read via Moonraker API |
| Print control | `M524` (abort) | `CANCEL_PRINT` | Partial | Define `M524` macro |
| EEPROM | `M500` | `SAVE_CONFIG` | Partial | Writes printer.cfg; restarts Klipper |
| EEPROM | `M501` | none | None | Config read at startup only |
| EEPROM | `M502` | manual printer.cfg edit | None |  |
| EEPROM | `M503` | none | None |  |
| Steppers | `M18` / `M84` | `M18` / `M84` | Same |  |
| Steppers | `M17` | none | None | Enabled automatically on move |
| Steppers | `M906 Xnnn` | `SET_TMC_CURRENT STEPPER=stepper_x CURRENT=nnn` | Partial | Needs TMC drivers configured |
| Speed | `M220 Snnn` / `M221 Snnn` | `M220 Snnn` / `M221 Snnn` | Same |  |
| Endstops | `M119` | `QUERY_ENDSTOPS` | Partial | Output format differs |
| Endstops | `M401` / `M402` | `BLTOUCH_DEBUG COMMAND=pin_down` / `pin_up` | Partial | BLTouch only |
| PID | `M303 E0 Snnn` | `PID_CALIBRATE HEATER=extruder TARGET=nnn` | Partial | Then `SAVE_CONFIG` |
| PID | `M303 E-1 Snnn` | `PID_CALIBRATE HEATER=heater_bed TARGET=nnn` | Partial | Then `SAVE_CONFIG` |
| Info | `M115` | see notes | Partial | TFT uses this to detect firmware; verify Klipper's reply |
| Info | `M114` | `GET_POSITION` | Partial | Output format differs |
| Info | `M112` | `M112` | Same | Triggers Klipper shutdown |
| Info | `M108` | none | None |  |

The hardest gaps are `M503`, `M501`, `M27` and `M115`: the TFT likely uses their responses to fill UI elements, so those responses may need stubbing or the UI logic reworking.

## Response parsing differences

Fixing outgoing commands is only half the job: the TFT also parses incoming serial responses, and any format Klipper sends differently will break displays like temperature, position and print progress.

| Data | Marlin format | Klipper format (as noted earlier; verify) |
| --- | --- | --- |
| Temperature | `T:210.5 /210.0 B:60.0 /60.0 @:0 B@:0` | `ok T=210.5 /210.0 B=60.0 /60.0` |
| Position | `X:0.00 Y:0.00 Z:0.00 E:0.00 Count X:0 Y:0 Z:0` | `mcu: stepper_x:0 stepper_y:0 ...` |
| Acknowledgment | `ok` or `ok Tnnn` | `ok` |
| Firmware info | `FIRMWARE_NAME:Marlin ...` | differs; check live output |
| SD progress | `SD printing byte nnn/nnn` | Not available via serial |

The Klipper column was written from reference notes, not a live capture. Klipper deliberately mimics some Marlin output (temperature reports in particular may already use `T:`), so capture real serial traffic from your printer before editing the parsers.

## Klipper macro strategy

Defining Marlin-named macros in `printer.cfg` lets Klipper handle the TFT's commands gracefully, so fewer strings need changing in the firmware itself. Add these to `printer.cfg`:

```
[gcode_macro M600]
gcode:
  PAUSE

[gcode_macro M24]
gcode:
  RESUME

[gcode_macro M25]
gcode:
  PAUSE

[gcode_macro M524]
gcode:
  CANCEL_PRINT

[gcode_macro M701]
gcode:
  # Add your filament load sequence here
  M117 Load filament...

[gcode_macro M702]
gcode:
  # Add your filament unload sequence here
  M117 Unload filament...

[gcode_macro G29]
gcode:
  BED_MESH_CALIBRATE
```

`PAUSE`, `RESUME` and `CANCEL_PRINT` require `[pause_resume]` and matching macros in your config. Also enable `[gcode_arcs]` and `[firmware_retraction]` if the TFT uses arcs or G10/G11.

## Reference implementations

The [neverhags/BIGTREETECH-TouchScreenFirmware-Klipper](https://github.com/neverhags/BIGTREETECH-TouchScreenFirmware-Klipper) fork already targets Klipper and supports the TFT35 V5, possibly other variants. Diffing it against the main repo shows exactly what someone else changed, which avoids re-solving the same problems.

Two other references are worth knowing: issue #1555 on the main repo discusses the config-file mapping idea, and `klipper-serial-btt` is an example of the middleware approach.

## Status and next steps

As of April 2026 the project is at the research stage: the approach is chosen and the command mapping is written, but no firmware code has been changed yet.

- [ ] Clone the main repo and the neverhags fork; diff them to see what was changed for Klipper
- [ ] Capture live serial output from Klipper (M105, M114, M115, ok replies) to confirm the real response formats
- [ ] Search `TFT/src/` for each Marlin string in the mapping and list where it is used
- [ ] Add the recommended macros to `printer.cfg`
- [ ] Decide how to handle `M503`, `M501`, `M27` and `M115` (stub the response or rework the UI)
- [ ] Update the response parsers, starting with temperature
- [ ] Build, flash and test on the touchscreen

Open questions:

- Which TFT model and hardware version is the target? The neverhags fork is confirmed only for the TFT35 V5.
- Will print status come from serial, or should the TFT pull it from the Moonraker API?

## Resources

- [BIGTREETECH-TouchScreenFirmware (main repo)](https://github.com/bigtreetech/BIGTREETECH-TouchScreenFirmware/)
- [neverhags Klipper fork](https://github.com/neverhags/BIGTREETECH-TouchScreenFirmware-Klipper)
- Main repo issue #1555: config-file command mapping discussion
- `klipper-serial-btt`: translator middleware alternative
- Klipper `printer.cfg` macro system (`[gcode_macro]`)
- Original mapping file from the project: `marlin-klipper-mapping.md` (its full contents are in the mapping and macro sections above)
