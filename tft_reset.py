from gpiozero import Button
import requests
import logging

logging.basicConfig(
    filename='/home/pi/tft_reset.log',
    level=logging.INFO,
    format='%(asctime)s %(message)s'
)

MOONRAKER = 'http://localhost:7125'

def is_printing():
    try:
        r = requests.get(
            f'{MOONRAKER}/printer/objects/query?print_stats',
            timeout=2)
        state = r.json()['result']['status']['print_stats']['state']
        return state in ('printing', 'paused')
    except Exception:
        return False

def on_press():
    if is_printing():
        logging.info('TFT reset pressed -- print active, triggering emergency stop')
        try:
            requests.post(f'{MOONRAKER}/printer/emergency_stop', timeout=5)
        except Exception as e:
            logging.error('emergency stop failed: %s', e)
    else:
        logging.info('TFT reset pressed -- triggering FIRMWARE_RESTART')
        try:
            requests.post(f'{MOONRAKER}/printer/firmware_restart', timeout=5)
        except Exception as e:
            logging.error('restart failed: %s', e)

btn = Button(18, pull_up=True, bounce_time=0.1)
btn.when_pressed = on_press

from signal import pause
pause()
