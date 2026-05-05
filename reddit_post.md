# BTT TFT35 with Klipper using stock firmware — the complete guide

*I used AI to help write and organize this post. The experience, testing, and code are all mine.*

I spent the last few days getting the BTT TFT35 V3.0 working *properly* with Klipper on an Ender 5 Plus — not hacked together, but fully functional: case light menu controls real NeoPixels, babystep Z works during prints, filament runout delays intelligently before parking, the screen shows live layer/time/data progress, and there's a NeoPixel heat-up animation system. Here's everything that actually worked, including some things I had to dig into firmware source code to figure out.

I'm running **stock BTT firmware** on the TFT (not the Klipper fork) because it's more stable and better tested. All the Klipper compatibility is handled on the Pi side.

All files are at: **https://github.com/hselomein/tftbridge-klipper-enhancements**

---

## Hardware

- Ender 5 Plus
- BTT SKR Mini E3 V3.0 (STM32G0B1)
- Micro Swiss Direct Drive + 0.6mm nozzle
- CR Touch
- BTT TFT35 V3.0
- Raspberry Pi 4
- 18x NeoPixel GRB strip on case light header (PA8)

---

## The Core Problem

The TFT35 speaks Marlin serial. Klipper doesn't. The TFT connects to the Pi's UART (`/dev/ttyAMA0`) via the P1 connector and expects a full Marlin conversation — M115 firmware ID, M105 temperature polling, M27 print progress, G-code commands for pause/resume/cancel. None of that works out of the box.

---

## Step 1 — The Serial Bridge

I used [tftbridge](https://github.com/oldhui-uk/tftbridge) by K. Hui as the foundation — a Klipper extras plugin that opens both serial ports (TFT UART and Klipper's `klippy.serial` PTY) and forwards traffic between them. It drops into `~/klipper/klippy/extras/` and configures in `printer.cfg`:

```ini
[tftbridge]
tft_device: /dev/ttyAMA0
tft_baud: 250000
klipper_device: /home/pi/printer_data/comms/klippy.serial
klipper_baud: 250000
```

On top of the original I added seven enhancements:

**1. Race condition fix**
The original checks `self.tftSerial` for None then uses it — another thread can set it to None between the two. Snapshotted to local vars first:
```python
tftSer = self.tftSerial
klipSer = self.klipperSerial
if tftSer != None and klipSer != None:
    # use local vars only
```

**2. Command queue unblock for remote host printing**
When printing from Mainsail, Klipper's proactive temp reports don't include `ok`. The TFT's command queue stalls waiting for acknowledgment. Detect bare temp lines with a regex and prepend `ok`:
```python
_TEMP_RE = re.compile(r'^[BT]\d*:')
if not line.startswith('ok ') and _TEMP_RE.match(line):
    line = 'ok ' + line
```

**3. Print state monitor (reactor timer)**
A `_monitor_callback` runs every 1.5s during prints, 5s at idle. It polls `print_stats` and `virtual_sdcard` and sends action lines the TFT needs to switch into printing mode:
```
//action:print_start
//action:pause
//action:resume
//action:print_end
//action:cancel
```
It also pushes live progress as TFT notifications every cycle: `Layer Left N/M Z2.34mm`, alternating `Data Left pos/size` and `Time Left 1h23m45s`. Round-robining the last two reduces serial writes from 3 to 2 per interval, keeping the TFT's receive buffer clear.

**4. ACK timeout prevention**
During high-acceleration moves Klipper is too busy to respond to M105 in time, triggering "ACK timedout" popups. Fix: push a fresh `ok T:... B:...` proactively from `_monitor_callback` every 1.5s by reading heater temps directly from Klipper's objects:
```python
self._tft_write('ok T:%.1f /%.1f B:%.1f /%.1f @:0 B@:0\n' % (
    e_st['temperature'], e_st['target'],
    b_st['temperature'], b_st['target']))
```

**5. TFT_NOTIFY G-code command**
`RESPOND MSG="//action:notification ..."` does NOT reach tftbridge when printing from Mainsail — `RESPOND` goes to Moonraker's socket, not `klippy.serial`. Added a G-code command that writes directly to the TFT serial port:
```python
def cmd_TFT_NOTIFY(self, gcmd):
    msg = gcmd.get('MSG', '')
    self._tft_write('//action:notification ' + msg + '\n')
```
Now any macro can pop a notification on the TFT: `TFT_NOTIFY MSG="Load filament then resume"`

**6. Suppressing noisy notifications**
"Pending gcode released" was popping up on the TFT after every motion error. Filtered in both the `!! ` and `// ` paths:
```python
if 'pending gcode' in msg.lower():
    continue
```

**7. TFT reboot detection**
When the TFT resets mid-print it sends M115 on boot. Without handling this, the monitor never re-sends `//action:print_start`. Watch for M115 and reset the state machine — TFT re-enters print mode on the next 1.5s callback without interrupting the print.

---

## Step 2 — TFT config.ini Settings

Key settings that differ from stock defaults:

```ini
serial_port:P1:8         ; 250000 baud UART to Pi
advanced_ok:0            ; Klipper sends plain ok, not N<line> P<buf> B<buf>
command_checksum:0       ; Klipper rejects checksummed commands
long_filename:0          ; no Marlin long filename protocol over serial
auto_load_leveling:0     ; don't auto-send M420 S1 at boot
ack_notification:0       ; suppress echo: spam
size_max:X350 Y350 Z400  ; adjust for your bed size
notification_m117:1
layer_disp_type:2
```

---

## Step 3 — The Marlin Compatibility Layer

A single `[include klipper_tft.cfg]` drop-in intercepts every Marlin command the TFT sends and maps it to the Klipper equivalent. Designed to be printer-agnostic.

| Command | What it does |
|---|---|
| `M115` | Firmware ID + full capability flags |
| `M27` | Print progress (`SD printing byte n/n`) |
| `M105` | Temperature report |
| `M104/M109/M140/M190` | Heater set/wait |
| `M106/M107` | Part fan |
| `M220/M221` | Speed/flow override |
| `M290` | Live Z babystep → `SET_GCODE_OFFSET Z_ADJUST` |
| `M355` | Case light on/off/brightness |
| `M150` | NeoPixel color (Marlin format, `U`=green) |
| `M500` | `SAVE_CONFIG` |
| `M503` | Stub that unlocks ABL menu + reports real probe Z offset |
| `M600` | Full filament change |
| `M24/M25/M524` | Resume/pause/cancel |
| `M420` | Load/clear bed mesh |
| `G34` | Auto-detects `Z_TILT_ADJUST` or `QUAD_GANTRY_LEVEL` |
| `M48` | `PROBE_ACCURACY` |
| `M851` | Get/set probe Z offset (reads real saved value from configfile) |
| `M118` | Intercepts TFT remote-host button presses |
| `M75/M76/M77` | Print timer stubs (Orca Slicer sends these) |

**M115 capability flags** unlock TFT menu items — if a cap is missing the option is greyed out or hidden:

```
Cap:TOGGLE_LIGHTS:1          # case light on/off toggle
Cap:CASE_LIGHT_BRIGHTNESS:1  # brightness slider
Cap:BABYSTEPPING:1           # live Z babystep during print
Cap:SDCARD:1                 # file browser
Cap:AUTOREPORT_SD_STATUS:1   # print state events
Cap:LONG_FILENAME:1          # full filename support
Cap:EMERGENCY_PARSER:1       # emergency stop passthrough
```

**Gotcha — ABL menu disappears**: `Cap:AUTOLEVEL:1` in M115 is not enough. The TFT binary also looks for the string "Auto Bed Leveling" in the M503 response:
```ini
[gcode_macro M503]
gcode:
  RESPOND MSG="echo:; Auto Bed Leveling:"
  RESPOND MSG="echo:  M420 S1 Z0.00"
  RESPOND MSG="echo:; Probe Z Offset:"
  RESPOND MSG="echo:  M851 Z{'%.3f' % printer.configfile.settings.bltouch.z_offset}"
```

**Gotcha — MACHINE_TYPE shows blank**: Dug into the TFT firmware C source. The parser extracts everything between `MACHINE_TYPE:` and the next `KINEMATICS:` or `EXTRUDER_COUNT:` keyword. With nothing after it the length is zero. Fix: append `EXTRUDER_COUNT:1` as the closing delimiter:
```
FIRMWARE_NAME:Klipper ... MACHINE_TYPE:Ender 5 Plus EXTRUDER_COUNT:1
```

**TFT button presses** during remote-host print send `M118 P0 A1 action:notification remote pause/resume/cancel`. The M118 macro intercepts those and calls `PAUSE` / `RESUME` / `CANCEL_PRINT`.

---

## Step 4 — Layer Tracking (Slicer Setup)

**Orca Slicer:**
- Machine start G-code: `SET_PRINT_STATS_INFO TOTAL_LAYER={total_layer_count}`
- Layer change G-code: `SET_PRINT_STATS_INFO CURRENT_LAYER={current_layer}`

**PrusaSlicer/SuperSlicer:**
- Start G-code: `SET_PRINT_STATS_INFO TOTAL_LAYER=[total_layer_count]`
- Before layer change: `SET_PRINT_STATS_INFO CURRENT_LAYER=[current_layer]`

---

## Step 5 — Filament Runout with Tube-Length Delay

The stock sensor fires the moment filament clears the switch — but there's ~900mm of guide tube between the sensor and the nozzle. Pausing immediately wastes that filament and jams in the tube.

My runout handler lets the print continue while `_RUNOUT_MONITOR` polls consumed filament every 2 seconds. A `TFT_NOTIFY` popup fires immediately ("Runout detected - 900mm remaining") and updates every 2s while counting down. Once 900mm consumed, M600 runs:

1. Retract 2mm, lift Z safely, park at front corner
2. Unload 100mm
3. Save nozzle temp, extend idle timeout to 12 hours, turn off nozzle (bed stays on, steppers stay enabled)
4. Push "Load filament then resume" to TFT

**Resume after filament change:** waits for bed if it dropped, reheats nozzle to saved temp, purges 50mm at bed level, primes 5mm, restores position.

**Key behaviour:** if a print completes mid-countdown or Klipper reboots, the remaining distance is persisted via Klipper's `[save_variables]` and restored on boot. The next print start automatically resumes the countdown from where it left off rather than restarting from 900mm — critical if there's only 300mm of filament left in the tube.

**Gotcha — cold nozzle resume:** If the hotend cools below `min_extrude_temp` during a long pause (idle timeout, waiting for filament), pressing Resume on the TFT would immediately error with "extrude below minimum temp" and cancel the job. Fixed by checking `printer.extruder.can_extrude` at resume time and reheating automatically before any movement.

---

## Step 6 — Filament Poop Purge

For filament swaps or color changes, purge a blob onto the bed corner. Purge volume is calculated automatically from a color transition matrix × type compatibility multiplier:

| Transition | Volume |
|---|---|
| Same color, same type | 80 mm³ |
| Dark → Light, same type | 140 mm³ |
| Different type (e.g. PLA → PETG) | 1.5× multiplier |
| Incompatible types (e.g. PLA → ABS) | 2.0× multiplier |

```
FILAMENT_POOP                                          # 80mm³ default
FILAMENT_POOP FROM_COLOR=dark TO_COLOR=light           # 140mm³
FILAMENT_POOP FROM_TYPE=petg TO_TYPE=pla FROM_COLOR=dark TO_COLOR=light  # 210mm³
FILAMENT_POOP AMOUNT=80                                # manual mm override
```

Purge speed is calculated from `max_volumetric_flow` (mm³/s) and actual filament diameter — so the extruder never tries to push faster than the hotend can melt. For a standard Creality-style hotend at 200°C, 5 mm³/s is a safe ceiling.

The poop sequence: base blob at Z0.25 → body rising to Z2.5 → peak at Z5.0 → tip shaping (5 retract pairs) → bed drops to snap string → lateral wipe.

---

## Step 7 — NeoPixel LED System

18x NeoPixel GRB controlled via [klipper-led_effect](https://github.com/julianschill/klipper-led_effect).

**States:**
- **Idle**: warm white breathing (autostart on Klipper boot)
- **Heat-up**: strip splits in two — LEDs 1–9 are the hotend gauge, LEDs 10–18 are the bed gauge. Each is a `heatergauge` fill bar going blue → orange as the heater approaches its target.
- **Printing**: solid white
- **Paused**: solid amber
- **Done**: three green flashes → fade into idle breathing
- **Error/cancel**: solid red

**Timelapse flash**: wrapped `TIMELAPSE_TAKE_FRAME` to blast the strip to full white for 250ms before capture — no head parking needed.

**Case light menu**: M355 S1/S0 on/off, M355 P<0-255> brightness. With `Cap:TOGGLE_LIGHTS:1` and `Cap:CASE_LIGHT_BRIGHTNESS:1` in M115, the TFT case light menu is fully functional.

**Gotcha 1 — layer name**: Plugin class is `layerHeaterGauge` but the config name is `heatergauge` (all lowercase). Spent longer than I'd like to admit on this.

**Gotcha 2 — cutoff**: Default `cutoff=1` makes the effect deactivate when the heater reaches target — LEDs go dark. Set `cutoff=0` to hold at the final orange color.

**Gotcha 3 — two-stage hotend heating**: Orca Slicer does `M104 S150` for homing preheat then `M109 S[final]`. Without a guard, the watcher fires at 150°C and transitions to solid white. Fixed with `he.target >= 160` threshold.

---

## Step 8 — StealthChop Threshold Tuning

Set `stealthchop_threshold` so the drivers switch to SpreadCycle above that speed — quiet StealthChop during slow prints, accurate SpreadCycle during fast travels and homing. Test each axis separately since Y drives more mass than X.

---

## Bonus — TFT Reset Button → Emergency Stop

TFT35 reset button wired to GPIO18 on the Pi, configured in `moonraker.conf`:

```ini
[button tft_reset]
type: gpio
pin: gpio18
on_press:
  {% do call_method("printer.emergency_stop") %}
```

Moonraker runs separately from Klipper so this works even when Klipper is crashed.

---

## What Still Doesn't Work / Not Yet Tested

| Feature | Status |
|---|---|
| Remote host print start/pause/resume/cancel | ✅ Confirmed |
| Live temp display | ✅ Confirmed |
| Progress bar + time remaining | ✅ Confirmed |
| Layer counter | ✅ Confirmed |
| NeoPixel heating animations | ✅ Confirmed |
| Case light menu (on/off/brightness) | ✅ Confirmed |
| Live Z babystep during print | ✅ Confirmed |
| ABL from TFT menu | ✅ Confirmed |
| Emergency stop | ✅ Confirmed |
| TFT reset button | ✅ Confirmed |
| TFT_NOTIFY from G-code | ✅ Confirmed |
| Cold nozzle auto-reheat on resume | ✅ Confirmed |
| Filament runout delay + M600 | ✅ Confirmed |
| Filament poop purge | ✅ Confirmed |
| Bed mesh view/load from TFT | ❓ Not tested |
| Filament poop purge matrix (color/type) | ❓ Not tested |
| Print from TFT SD card | ❓ Not tested |
| TFT terminal screen | ⚠️ Inconsistent |

---

All files: **https://github.com/hselomein/tftbridge-klipper-enhancements**
