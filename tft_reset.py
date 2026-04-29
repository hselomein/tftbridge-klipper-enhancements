from gpiozero import Button
import requests
import logging

logging.basicConfig(
    filename='/home/pi/tft_reset.log',
    level=logging.INFO,
    format='%(asctime)s %(message)s'
)

def on_press():
    logging.info('TFT reset pressed -- triggering FIRMWARE_RESTART')
    try:
        requests.post('http://localhost:7125/printer/firmware_restart')
    except Exception as e:
        logging.error('restart failed: %s', e)

btn = Button(18, pull_up=True, bounce_time=0.1)
btn.when_pressed = on_press

from signal import pause
pause()
