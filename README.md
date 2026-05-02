# BTT TFT35 with Klipper: Stock Firmware + tftbridge

*I used AI to help write up and organize this post. The experience, testing, and code are all mine.*

I got my BTT TFT35 V3.0 fully working with Klipper on an Ender 5 Plus using the stock BTT firmware and tftbridge. There's a lot of incomplete info out there, so I'm documenting everything that actually worked, including some enhancements I made to tftbridge and a hardware reset button mod. Hopefully this saves someone else the pain.

I'm running **stock BTT firmware** (not the Klipper fork) because it's more stable and better tested. The bridge is [tftbridge](https://github.com/oldhui-uk/tftbridge) by K. Hui. This repo has an enhanced version plus all the companion config you need.

**Further reading:**
- [remote_host.md](remote_host.md): how the print monitoring protocol works and slicer setup for layer tracking
- [klipper_marlin_compat.md](klipper_marlin_compat.md): full Marlin feature to Klipper compatibility map for TFT35 touch mode

---

## Hardware

- **Printer**: Ender 5 Plus
- **MCU**: BTT SKR Mini E3 V3.0 (STM32G0B1)
- **Extruder**: Micro Swiss Direct Drive
- **Probe**: CR Touch
- **Screen**: BTT TFT35 V3.0
- **Host**: Raspberry Pi 4 running Klipper + Moonraker + Mainsail

---

## The Core Problem

The TFT35 speaks Marlin serial. Klipper doesn't. The TFT connects to the Pi's UART (`ttyAMA0`) via the P1 connector and you need something to bridge between it and Klipper's virtual serial socket (`klippy.serial`).

That's what tftbridge does. But the original has some gaps when you're printing from Mainsail instead of the TFT's own SD card. Here's what I added.

---

## Files

| File | Destination on Pi | Purpose |
|---|---|---|
| `tftbridge.py` | `~/klipper/klippy/extras/tftbridge.py` | Enhanced Klipper extra |
| `klipper_tft.cfg` | `~/printer_data/config/klipper_tft.cfg` | Companion macros |
| `printer.cfg` | `~/printer_data/config/printer.cfg` | Example config (Ender 5 Plus) |
| `tft_reset.py` | `/home/pi/tft_reset.py` | GPIO reset button script |
| `tft-reset.service` | `/etc/systemd/system/tft-reset.service` | systemd service for reset button |
| `gcode/` | `~/printer_data/gcodes/` | Motion test files |

---

## tftbridge Enhancements

### 1. Race Condition Fix

The original checks `self.tftSerial` and `self.klipperSerial` for None and then uses them, but on disconnect, another thread can set them to None between the check and the write. Fix is to snapshot to local variables first:

```python
tftSer = self.tftSerial
klipSer = self.klipperSerial
if tftSer != None and klipSer != None:
    # use tftSer and klipSer, not self.tftSerial
```

### 2. Command Queue Unblock for Remote Host Printing

When printing from Mainsail, the TFT is just an observer. It still polls with M105 for temperatures, but Klipper's proactive temp reports don't include an `ok` prefix. That stalls the TFT's command queue and it stops updating.

Fix is to detect bare temp lines and prepend `ok`:

```python
_TEMP_RE = re.compile(r'^[BT]\d*:')

# in klipper2tft, after other filtering:
if not line.startswith('ok ') and _TEMP_RE.match(line):
    line = 'ok ' + line
```

### 3. Print State Monitoring

The TFT needs `//action:print_start`, `//action:pause`, `//action:resume`, `//action:print_end`, and `//action:cancel` to switch into printing mode and show the pause/cancel buttons. I added a reactor timer that polls Klipper's `print_stats` and `virtual_sdcard` objects and sends those at the right times:

```python
def _monitor_callback(self, eventtime):
    ps  = self.printer.lookup_object('print_stats').get_status(eventtime)
    vsd = self.printer.lookup_object('virtual_sdcard').get_status(eventtime)
    state = ps.get('state', 'standby')

    if state == 'printing' and last != 'printing':
        self._tft_write('//action:print_start\n')
    elif state == 'paused' and last == 'printing':
        self._tft_write('//action:pause\n')
    # ... etc

    # progress notifications
    if state in ('printing', 'paused'):
        file_size = int(vsd.get('file_size', 0))
        file_pos  = int(vsd.get('file_position', 0))
        if file_size > 0:
            self._tft_write('//action:notification Data Left %d/%d\n' % (file_pos, file_size))
        # time remaining, layer counter...

    interval = 3.0 if state in ('printing', 'paused') else 5.0
    return eventtime + interval
```

This gives you a live progress bar, time remaining, and layer counter on the TFT during Mainsail prints. Layer counter requires your slicer to emit `SET_PRINT_STATS_INFO CURRENT_LAYER=x TOTAL_LAYER=y`. See [remote_host.md](remote_host.md) for slicer setup.

### 4. TFT_NOTIFY G-code Command

This one tripped me up. `RESPOND MSG="//action:notification ..."` does NOT reach tftbridge. When printing from Mainsail, `RESPOND` goes to Moonraker's socket, not `klippy.serial` which tftbridge is reading. So I added a G-code command that writes directly to the TFT serial port:

```python
gcode = self.printer.lookup_object('gcode')
gcode.register_command('TFT_NOTIFY', self.cmd_TFT_NOTIFY,
                       desc='Send a notification pop-up to the TFT35')

def cmd_TFT_NOTIFY(self, gcmd):
    msg = gcmd.get('MSG', '')
    self._tft_write('//action:notification ' + msg + '\n')
```

Now from G-code or the Mainsail console:
```
TFT_NOTIFY MSG="Hello TFT"
```

### 5. Suppressing Noisy Notifications

"Pending gcode released" was popping up on the TFT every time Klipper flushed its queue after a motion error. It's useful in the logs but not as a pop-up every time. I filtered it out in both the `!! ` and `// ` prefix paths — Klipper routes it through both depending on context:

```python
if line.startswith('!! '):
    msg = line[3:].strip()
    if 'pending gcode' in msg.lower():
        continue  # already logged, don't forward to TFT
    elif 'must home' in msg.lower():
        # show as dismissible prompt dialog
        ...
    else:
        line = '//action:notification ' + msg + '\n'
elif line.startswith('// '):
    if 'pending gcode' in line.lower():
        continue
    line = line[3:]
```

### 6. ACK Timeout Prevention

The TFT sends M105 periodically and expects `ok` back within its ACK timeout. During high-acceleration motion Klipper is too busy to respond to M105 in time, which triggers an "ACK timedout" popup on the TFT.

Fix is to proactively push a fresh `ok T:... B:...` to the TFT from `_monitor_callback` every 1.5 seconds by reading heater temps directly from Klipper's objects — no waiting for M105 to be processed:

```python
try:
    e_st = self.printer.lookup_object('extruder').get_status(eventtime)
    b_st = self.printer.lookup_object('heater_bed').get_status(eventtime)
    self._tft_write('ok T:%.1f /%.1f B:%.1f /%.1f @:0 B@:0\n' % (
        e_st['temperature'], e_st['target'],
        b_st['temperature'], b_st['target']))
except Exception:
    pass
```

The callback interval is 1.5s during printing (down from 3s) so the heartbeat fires well within any reasonable ACK timeout window.

Progress notifications (Data Left / Time Left / Layer Left) are round-robined — Layer Left sends every cycle, Data Left and Time Left alternate — reducing serial writes from 3 to 2 per interval to keep the TFT's receive buffer clear.

### 7. TFT Reboot Detection

When the TFT resets mid-print (physical button or power glitch), it sends M115 on boot. Without handling this, `_monitor_callback` never re-sends `//action:print_start` because `last_print_state` is still `'printing'` and there's no state transition to detect.

Fix is to watch for M115 in `tft2klipper` and reset `last_print_state`:

```python
if b'M115' in line:
    self.last_print_state = 'standby'
```

On the next 1.5s callback the monitor sees a `standby → printing` transition and re-sends `print_start` followed immediately by `ok T:...`. The TFT re-enters print mode without interrupting the print.

---

## config.ini for Stock BTT Firmware + tftbridge

Key settings that differ from stock defaults:

```ini
serial_port:P1:8          ; 250000 baud UART to Pi
advanced_ok:0             ; Klipper doesn't send slot counts
command_checksum:0        ; Klipper rejects checksummed commands
onboard_sd:2              ; auto-detect; setting to 0 hides the ABL button
long_filename:0           ; no Marlin long filename protocol
auto_load_leveling:0      ; don't auto-send M420 S1 at boot
ack_notification:0        ; suppress echo: spam
size_max:X350 Y350 Z400   ; adjust for your bed size
min_temp:150
notification_m117:1
layer_disp_type:2
probing_z_offset:0        ; if your probe IS the Z endstop, no offset needed
```

### Getting the ABL Menu to Appear

This one took me a while. The ABL menu button disappears if the TFT doesn't detect bed leveling. The stock BTT binary looks for the string "Auto Bed Leveling" in the M503 response. `Cap:AUTOLEVEL:1` from M115 is not enough. Add this stub to `klipper_tft.cfg`:

```ini
[gcode_macro M503]
description: Stub M503 -- returns enough for TFT to detect bed leveling type
gcode:
  RESPOND MSG="echo:; Auto Bed Leveling:"
  RESPOND MSG="echo:  M420 S1 Z0.00"
```

---

## StealthChop Threshold Tuning

This is machine-specific, but it's worth knowing about if you're running TMC drivers in StealthChop mode.

The TFT triggers fast travel moves: homing, parking after cancel, moving to mesh probe points. Without `stealthchop_threshold` set, those moves stay in StealthChop at high speed, where PWM regulation gets unstable and you get audible noise. Set `stealthchop_threshold` to your noise onset speed and it switches to SpreadCycle above that, quiet StealthChop below it during printing.

To find your threshold, use `gcode/x_noise_threshold_test.gcode`. It steps X-only from 200mm/s to 350mm/s in fine increments, 4 passes each, with a `TFT_NOTIFY` pop-up at each level so you know exactly which speed you're hearing. Note where the noise starts and set that as your threshold. Y axis may be different from X if it drives more mass or multiple belts, so test each one separately.

One thing I'll mention: I spent a lot of time chasing step skipping that turned out to be damaged hardware. My screen shorted and took out the MCU board and PSU with it. After I replaced them everything improved dramatically. If you're seeing unexplained skipping or erratic TMC behavior, rule out hardware damage before you go deep on tuning.

---

## Hardware Mod: TFT Reset Button to Klipper FIRMWARE_RESTART

The TFT35 has a reset button. I wired it to GPIO18 (physical pin 12) on the Pi so pressing it resets both the TFT and Klipper at the same time.

I went with the Moonraker API approach rather than a Klipper `[button]`. The reason: if Klipper is crashed or frozen, which is exactly when you need the reset button, the host MCU won't respond either. Moonraker runs in a completely separate process and can restart Klipper from the outside even when Klipper is halted.

The script checks print state before acting:
- **Mid-print**: calls `emergency_stop` — stops the print immediately, same as M112
- **Not printing**: calls `firmware_restart` — full clean restart as before

### Setup

```bash
sudo apt install python3-lgpio
sudo cp tft_reset.py /home/pi/tft_reset.py
sudo cp tft-reset.service /etc/systemd/system/tft-reset.service
sudo systemctl enable tft-reset.service
sudo systemctl start tft-reset.service
```

**Wiring**: GPIO18 (pin 12) to one side of the TFT reset button, GND (pin 14) to the other. The script uses the internal pull-up. Press the button, the TFT resets and Klipper does a FIRMWARE_RESTART via Moonraker. Mainsail reconnects automatically.

---

## Test G-code Files

I wrote three test files that are useful beyond this specific setup.

**`gcode/speed_pattern_test.gcode`**: holds accel constant and sweeps speed across X/Y lines, diagonals, squares, and circles. Finds the speed limit for your machine.

**`gcode/accel_limit_test.gcode`**: holds speed constant and sweeps accel from 3000 to 10000mm/s² using short 50mm oscillating moves, so every reversal is a full accel/decel cycle. Uses `TFT_NOTIFY` to pop the current level on the TFT so you know exactly which accel was running when a skip happens.

**`gcode/x_noise_threshold_test.gcode`**: X-only moves stepping in fine increments. Finds your exact StealthChop noise threshold for X. Adapt it for Y by swapping the axis and fixing the other one.

All three use `TFT_NOTIFY` for status pop-ups and restore velocity limits at the end.

---

## Filament Runout and Change

My Bowden tube is about 1m long, so I sized the runout delay to match. When the sensor triggers, I don't want to stop the print immediately -- I want to keep printing until the remaining filament in the tube is consumed. That way there's still something to grip when I go to pull the old spool out, and I don't end up with a random mid-layer pause with a half-meter of filament still sitting in the tube.

The math is based on Marlin's `FILAMENT_RUNOUT_DISTANCE_MM`. I set it to 900mm, which leaves about 100mm of stub at the nozzle end -- enough to grab, not so much that it's in the way.

### How it works

The sensor uses `pause_on_runout: false` and calls `FILAMENT_RUNOUT` immediately. That macro records `printer.print_stats.filament_used` at that moment and kicks off a `_RUNOUT_MONITOR` delayed_gcode that polls every 2 seconds. When the consumed amount hits 900mm, it calls M600.

The TFT gets a pop-up when runout fires ("Runout detected - 900mm remaining") and a progress update every 2 seconds while it counts down.

### M600 sequence

1. Save gcode state
2. Pause
3. Retract 2mm fast, raise Z by 20mm (clamped to max), park head at X10 Y10
4. Unload 100mm at retract speed
5. Save the nozzle target temp, set idle timeout to 12 hours, turn off the nozzle
6. Pop up "Load filament then resume" on the TFT

The bed stays on. Steppers stay enabled. The custom `[idle_timeout]` section checks whether a filament change is pending before doing anything -- if it is, it just logs a message and leaves everything alone. This matters because the default idle_timeout would turn off the heaters and disable the steppers, which would let the part cool and release from the bed.

### Resume sequence

1. Clear the pending flag, restore the normal idle timeout
2. If the bed dropped more than 5C below target, wait for it to come back up (M190)
3. Reheat the nozzle to what it was before (M109, blocking)
4. Purge 50mm at 180mm/min
5. Prime 5mm at 300mm/min
6. Restore gcode state (no move -- head is already at the park position)
7. Hand off to RESUME_BASE

No homing. The Ender 5 Plus homes Z to 0 which is the bed, so homing during a filament change would drive the nozzle into the part.

---

## What Still Doesn't Work

- Print from TFT SD card, not tested, not a priority when I have Mainsail
- Some menu items are stubs (M92 e-steps, M501/M502 EEPROM restore/reset)
- Terminal screen in the TFT menu, sends commands but responses are inconsistent

---

## Summary

| Feature | Status |
|---|---|
| Remote host print start/pause/resume/cancel | ✅ Working |
| Live temp display during print | ✅ Working |
| Progress bar + time remaining | ✅ Working |
| Layer counter (requires slicer support) | ✅ Working |
| ABL / Bed Mesh from TFT menu | ✅ Working |
| Load/unload filament | ✅ Working |
| Emergency stop | ✅ Working |
| Manual leveling corners | ✅ Working |
| TFT reset button to Klipper restart | ✅ Working |
| TFT_NOTIFY from G-code | ✅ Working |
| Filament runout with 900mm delay | ✅ Working |
| Print from TFT SD | ❓ Not tested |

Happy to answer questions. Full config and files are at https://github.com/hselomein/tftbridge-klipper-enhancements
