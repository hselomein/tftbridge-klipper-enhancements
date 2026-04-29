# Remote Host Print Control

## What It Does

When a print is started from Klipper, Mainsail, or Fluidd rather than from the TFT's own SD card, the TFT would normally show nothing. This feature bridges that gap: the TFT automatically switches to the printing screen, shows live progress and remaining time, tracks the current layer, and lets the user pause, resume, or cancel from the TFT touch buttons — all without OctoPrint.

This is implemented entirely in [`klipper_tft.cfg`](klipper_tft.cfg) using standard Klipper macros and the BTT TFT host-action serial protocol.

---

## How It Works

### Klipper → TFT notifications

A `[delayed_gcode _TFT_MONITOR]` polls `printer.print_stats.state` every 3 seconds while a print is active (5 seconds when idle). When it detects a state transition it sends a `//action:` line to the TFT over the serial connection:

| Event | Line sent to TFT |
|---|---|
| Print started | `//action:print_start` |
| Print paused | `//action:pause` |
| Print resumed | `//action:resume` |
| Print finished | `//action:print_end` |
| Print cancelled | `//action:cancel` |

While printing, every 3-second poll also sends:

| Data | Line sent to TFT |
|---|---|
| Byte progress | `//action:notification Data Left <pos>/<size>` |
| Remaining time | `//action:notification Time Left <h>h<m>m<s>s` |
| Layer (if available) | `//action:notification Layer Left <current>/<total>` |

The TFT firmware parses lines that begin with `//action:` (no space) from the mainboard serial. `RESPOND PREFIX="//action:..." MSG="..."` produces that exact prefix — the space Klipper inserts between the prefix and message body is irrelevant to the parser.

### TFT → Klipper button presses

When the TFT is in remote-host mode (after receiving `//action:print_start`) and the user presses Pause, Resume, or Cancel, the TFT sends:

```
M118 P0 A1 action:notification remote pause
M118 P0 A1 action:notification remote resume
M118 P0 A1 action:notification remote cancel
```

A `[gcode_macro M118]` in `klipper_tft.cfg` intercepts these and calls `PAUSE`, `RESUME`, or `CANCEL_PRINT` in Klipper. All other M118 calls are forwarded to the original Klipper handler unchanged.

After the Klipper state changes, `_TFT_MONITOR` detects the new `print_stats.state` on its next poll and sends the corresponding `//action:pause` / `//action:resume` / `//action:cancel` back to confirm the state change on the TFT's UI.

### When remote-host mode is NOT used

If the print was started from the TFT's own SD card, the TFT manages the print itself and sends `M25` / `M24` / `M524` directly for pause/resume/cancel. The `_TFT_MONITOR` still runs in that case — it detects the state changes in `print_stats` and sends progress/time/layer updates, but the lifecycle `//action:print_start` / `//action:print_end` messages are not needed (the TFT is already on the printing screen).

---

## Slicer Setup for Layer Tracking

Layer tracking requires the slicer to call `SET_PRINT_STATS_INFO` at print start and at each layer change. If these calls are absent, the layer counter is simply not shown on the TFT — everything else still works.

### PrusaSlicer / SuperSlicer

**Printer Settings → Custom G-code → Start G-code** — add at the end:
```
SET_PRINT_STATS_INFO TOTAL_LAYER=[total_layer_count]
```

**Printer Settings → Custom G-code → Before layer change G-code** — add:
```
SET_PRINT_STATS_INFO CURRENT_LAYER=[current_layer]
```

### Orca Slicer / Bambu Studio

**Printer → Machine G-code → Machine start G-code** — add at the end:
```
SET_PRINT_STATS_INFO TOTAL_LAYER={total_layer_count}
```

**Printer → Machine G-code → Layer change G-code** — add:
```
SET_PRINT_STATS_INFO CURRENT_LAYER={current_layer}
```

Note: Orca Slicer uses `{curly_braces}` for variables; PrusaSlicer uses `[square_brackets]`.

### Cura

Cura does not have a dedicated "layer change G-code" field. Options:

**Option A — Machine start G-code only (total layers, no per-layer update):**
Add to Machine Settings → Start G-code:
```
SET_PRINT_STATS_INFO TOTAL_LAYER={layer_count}
```
This populates the total layer count but the current-layer counter will not advance.

**Option B — Search and Replace post-processor:**
Use Extensions → Post Processing → Modify G-Code → Add a script → Search and Replace:
- Search: `;LAYER:(\d+)`
- Replace: `;LAYER:\1\nSET_PRINT_STATS_INFO CURRENT_LAYER=\1`
- Enable "Use Regular Expressions"

This inserts a `SET_PRINT_STATS_INFO` call after every `;LAYER:N` comment Cura emits.

---

## Timing and Polling

| State | Poll interval |
|---|---|
| Printing or paused | 3 seconds |
| Idle | 5 seconds |

The TFT receives state confirmations within one poll cycle (up to 3 s) after a state transition. This is typically imperceptible since the pause/resume animations on the TFT are themselves a second or two.

`_TFT_MONITOR` starts automatically 1 second after Klipper boots (`initial_duration: 1`) and reschedules itself indefinitely using `UPDATE_DELAYED_GCODE`.

---

## Debugging

All `RESPOND PREFIX="//action:..."` calls are visible in Klipper's console (Mainsail/Fluidd terminal) as `//action:...` lines. If the TFT is not responding to them, check:

1. The TFT serial connection — TFT P1 must be wired to the Pi's UART and `serial_port:P1:8` set in `config.ini` (value `8` = 250000 baud)
2. The baud rate — `serial_port` for the chosen port must match the Pi UART baud rate (typically `8` = 250000)
3. That `[include klipper_tft.cfg]` is present in `printer.cfg` and Klipper restarted after the include was added
