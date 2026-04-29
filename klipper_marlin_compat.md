# Marlin Feature → Klipper Compatibility Map

This document maps every Marlin feature required for full BTT TFT35 touch-mode
operation to its Klipper equivalent and notes the implementation status.

**Legend:**
- ✅ Supported — works natively or via macro in `klipper_tft.cfg`
- ⚠️ Partial — works with caveats documented below
- ❌ Not supported — no Klipper equivalent exists
- 🔧 Via macro — requires a G-code macro (included in `klipper_tft.cfg`)

---

## General (always required)

| Marlin feature | Klipper equivalent | Status | Notes |
|---|---|---|---|
| `EEPROM_SETTINGS` | `SAVE_CONFIG` | ⚠️ Partial | M500→`SAVE_CONFIG`, M501/M502 stubbed (no restore/reset), M503 stubbed |
| `BABYSTEPPING` | `SET_GCODE_OFFSET Z_ADJUST=` | ✅ | M290 Z → `SET_GCODE_OFFSET Z_ADJUST={z} MOVE=1` |
| `AUTO_REPORT_TEMPERATURES` | Proactive temp reports | ✅ | Klipper emits temp lines proactively; M155 stubbed (TFT polls M105) |
| `AUTO_REPORT_POSITION` | M114 on demand | ✅ | Klipper responds to M114; M154 stubbed (TFT polls M114) |
| `EXTENDED_CAPABILITIES_REPORT` | M115 macro | ✅ | M115 reports FIRMWARE_NAME + Cap: lines including AUTOLEVEL, Z_PROBE |
| `M115_GEOMETRY_REPORT` | M115 macro | ✅ | M115 appends `X_MIN/MAX Y_MIN/MAX Z_MIN/MAX` from toolhead limits |
| `M114_DETAIL` | Klipper M114 | ✅ | Klipper responds with X/Y/Z/E positions |
| `REPORT_FAN_CHANGE` | — | ❌ | Klipper does not send unsolicited fan-change messages; TFT polls fan state |

---

## Onboard media

| Marlin feature | Klipper equivalent | Status | Notes |
|---|---|---|---|
| `SDSUPPORT` | `[virtual_sdcard]` | ⚠️ Partial | TFT physical SD card works natively; Klipper virtual_sdcard files are accessible only via remote-host print (Mainsail/Fluidd) |
| `LONG_FILENAME_HOST_SUPPORT` | `[virtual_sdcard]` | ✅ | Klipper virtual_sdcard supports long filenames |
| `AUTO_REPORT_SD_STATUS` | M27 macro | ✅ | M27 → `SD printing byte {pos}/{size}` from `print_stats` |
| `SDCARD_CONNECTION ONBOARD` | — | ❌ | No Klipper equivalent; use remote host print for Klipper-managed files |

---

## Dialog with host

| Marlin feature | Klipper equivalent | Status | Notes |
|---|---|---|---|
| `EMERGENCY_PARSER` | Klipper native | ✅ | M112 handled immediately; M108 stubbed |
| `SERIAL_FLOAT_PRECISION 4` | Klipper native | ✅ | Klipper outputs floats with sufficient precision |
| `HOST_ACTION_COMMANDS` | `RESPOND PREFIX="//action:..."` | ✅ | Implemented in `_TFT_MONITOR` delayed_gcode and `tftbridge.py` reactor timer |
| `HOST_PROMPT_SUPPORT` | `RESPOND PREFIX="//action:prompt_*"` | ✅ | M292→`prompt_end`; tftbridge sends `prompt_begin/text/button/show` for error dialogs |
| `HOST_STATUS_NOTIFICATIONS` | `RESPOND PREFIX="//action:notification"` | ✅ | Progress, time, layer notifications sent every 3 s during print |

---

## M73 print progress (SET_PROGRESS_MANUALLY + M73_REPORT)

Prerequisite: dialog with host (above)

| Marlin feature | Klipper equivalent | Status | Notes |
|---|---|---|---|
| `SET_PROGRESS_MANUALLY` | `display_status.progress` | ✅ | M73 handled natively by Klipper (`[display_status]`); sets `printer.display_status.progress` from slicer P value |
| `M73_REPORT` | M27 macro | ✅ | M27 returns byte progress; `_TFT_MONITOR` sends `Data Left` notifications |

---

## ADVANCED_OK

| Marlin feature | Klipper equivalent | Status | Notes |
|---|---|---|---|
| `ADVANCED_OK` | — | ❌ | Klipper sends plain `ok`; does not echo `N<line> P<buf> B<buf>`. Disable `advanced_ok` in TFT Feature Settings when using tftbridge |

---

## M600 / filament change (NOZZLE_PARK_FEATURE + ADVANCED_PAUSE_FEATURE)

Prerequisite: dialog with host (above)

| Marlin feature | Klipper equivalent | Status | Notes |
|---|---|---|---|
| `NOZZLE_PARK_FEATURE` | G91/G1 Z/G90 in macros | ✅ | CANCEL_PRINT parks at rear right; M600 raises Z 10 mm before pausing |
| `ADVANCED_PAUSE_FEATURE` | `PAUSE` / `RESUME` | ✅ | M0/M1/M25→`PAUSE`, M24→`RESUME`, M524→`CANCEL_PRINT` |
| `PARK_HEAD_ON_PAUSE` | M600 macro | ✅ | M600 raises Z 10 mm if homed before calling `PAUSE` |
| `FILAMENT_LOAD_UNLOAD_GCODES` | M701 / M702 macros | ✅ | Macros present; user fills in their load/unload moves |

---

## Bed leveling (full menu support)

| Marlin feature | Klipper equivalent | Status | Notes |
|---|---|---|---|
| `Z_MIN_PROBE_REPEATABILITY_TEST` | `PROBE_ACCURACY` | 🔧 | M48 P<n> → `PROBE_ACCURACY SAMPLES={n}` |
| `G26_MESH_VALIDATION` | — | ❌ | No Klipper equivalent; M26 stub responds with redirect to `BED_MESH_CALIBRATE` |
| `Z_STEPPER_AUTO_ALIGN` | `Z_TILT_ADJUST` / `QUAD_GANTRY_LEVEL` | 🔧 | G34 → `Z_TILT_ADJUST` if `[z_tilt]` configured, or `QUAD_GANTRY_LEVEL` if `[quad_gantry_level]` configured |

---

## Summary of gaps

| Gap | Impact | Workaround |
|---|---|---|
| `EEPROM_SETTINGS` restore/reset | M501/M502 are no-ops | Edit `printer.cfg` manually; re-run `PROBE_CALIBRATE` / PID tune |
| `REPORT_FAN_CHANGE` | TFT fan display may lag | TFT polls fan state on screen open; display updates on next poll |
| `SDCARD_CONNECTION ONBOARD` | Can't browse Klipper virtual SD from TFT | Use Mainsail/Fluidd to start prints; TFT shows progress via remote host |
| `ADVANCED_OK` | Must be disabled in TFT | Turn off in TFT Feature Settings → Advanced OK |
| `G26` mesh validation | TFT button does nothing useful | Stub responds with message; run `BED_MESH_CALIBRATE` instead |
