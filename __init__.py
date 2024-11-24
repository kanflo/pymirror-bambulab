"""A PyMirror module that connects to a Bambulab printer and the Bambulab cloud
to display printing progress. The following configuration is needed in your mirror
config file:

    [bambulab]
    source = bambulab
    top = <top location for module>
    left = <...>
    width = <width, -1 for entire screen width
    height = <...>
    device_type = <device type, eg P1S>
    serial = <your serial>
    host = <ip address>
    access_code = <your access code>
    auth_token = <bambulab api token>
"""
from typing import *
import logging
import threading
import pymirror
import bambulab.remiapp as remiapp
from bambulab.bambudisplay import BambuDisplay

remi_port: int = 30000
remi_qr_file: str = "/tmp/remiqr.png"

logger = logging.getLogger("bambulab")

# TODO: Why does this not work?
#logging.getLogger("bambulab").setLevel(logging.DEBUG)

qr_code_width: int = 250


def bambu_remi_thread(bambu_display: BambuDisplay):
    logger.info(f"Starting REMI app")
    remiapp.start(bambu_display, 30000)  # Never returns


def init(mirror: pymirror.Mirror, config: dict):
    logger.info(f"Hello world from the BambuLab module with config {config}")
    display: BambuDisplay = BambuDisplay(mirror, config)
    remi_thread = threading.Thread(target=bambu_remi_thread, args=(display,), daemon=True)
    remi_thread.start()
    return display

def draw(mirror: pymirror.Mirror, bambu_display: BambuDisplay):
    bambu_display.draw(mirror)


def get_debug_info(locals: dict) -> dict:
    """Get debug info of this module.

    Args:
        locals: The module locals

    Returns:
        dict: Dictionary with debug information
    """
    #if weather_mod and xkcd_mod:
    #    weather_debug = weather_mod._module.get_debug_info(None)
    return None
    return {
        "last_topic_time": last_topic_time,
        "state": state,
        "extruder_temp": extruder_temp,
        "extruder_target": extruder_target,
        "bed_temp": bed_temp,
        "bed_target": bed_target,
        "state": state,
        "progress": progress,
        "print_time": print_time,
        "remaining_time": remaining_time,
        "estimated_print_time": estimated_print_time,
        "last_print_time": last_print_time,
        "file_name": file_name,
        "filemant_length": filemant_length,
        "weather_active": state is None or state == "",
        "weather_debug": weather_debug
    }
