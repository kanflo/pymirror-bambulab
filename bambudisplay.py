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
    access_code = <your printer access code>
    region = <your region, eg. EU>
    email = <email used to log in to Bambulab/Bambu Studio>
    password = <and password>
    username = <your user name>
    auth_token_file = /tmp/.authtoken

The `auth_token_file` is used to store your cloud authentication token when logged in.
"""

from typing import *
import logging
import time
import re
import sys
import datetime
import threading
import pymirror
import pygame
from pathlib import Path
try:
    import bambulab.pybambu
except ModuleNotFoundError:
    print("Cannot find the pybambu module. You need to run the following commands in the bambulab directory:")
    print("\% git submodule init && git submodule update")
    print("\% ln -s ha-bambulab/custom_components/bambu_lab/pybambu pybambu")
    sys.exit(1)
from bambulab.pybambu.models import (
    Device,
    AMSTray,
    Device,
    PrintJob,
)
import asyncio
import bambulab.remiapp as remiapp

remi_port: int = 30000
remi_qr_file: str = "/tmp/remiqr.png"

logger = logging.getLogger("bambulab")

qr_code_width: int = 250

async def bambu_connect(c: bambulab.pybambu.BambuClient):
    """Pybambu init"""
    await c.connect(None)

def get_public_ip() -> str|None:
    """Fetch our public IP address, requires internet access

    Returns:
        str|None: IP address or None if we for some reason failed.
    """
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.connect(("8.8.8.8", 80))
    ip: str|None = None
    try:
        ip = s.getsockname()[0]
    except Exception as e:
        logger.error("Failed to obtain my own IP address", exc_info=e)
    s.close()
    return ip


def generate_remi_qr_code(url: str):
    import qrcode
    img: qrcode.image.pil.PilImage = qrcode.make(url)
    img.convert("RGB").save(remi_qr_file)


def bambu_download_cover_thread(bambu_display: "BambuDisplay"):
    """Download cover for latest job from the cloud, save to `cover_fname` and update
    `locals` for the main thread to learn that the cover is available

    Args:
        client (bambulab.pybambu.BambuClient): Bambu client
        locals (any): Module locals
    """
    logger.info("Downloading cover from the cloud")
    cover_fname: str = f"/tmp/cover-{str(time.time())}.png"
    try:
        task = bambu_display._client.bambu_cloud.get_latest_task_for_printer(bambu_display._client._serial)
        cover: bytes = bambu_display._client.bambu_cloud.download(task["cover"])
        with open(cover_fname, "wb+") as f:
            f.write(cover)
        logger.info(f"🟢🟢🟢 Cover downloaded to {cover_fname} 🟢🟢🟢")
        bambu_display._cover_fname = cover_fname
        bambu_display._cover_downloaded = True
    except Exception as e:
        logger.error("Bambu cover download caused exception", exc_info=e)

class BambuDisplay:
    _config: dict[str: str|int] = None

    def __init__(self, mirror: pymirror, config: dict):
        self._config = config
        self._extruder_icon: pygame.Surface|None = None
        self._bed_icon: pygame.Surface|None = None
        self._humidity_icons: list[pygame.Surface] = []
        self._client: bambulab.pybambu.BambuClient
        self._cloud_connected: bool = False  # Set to True once authenticated with the Bambu cloud
        self._first_print_job_start = None

        (self._extruder_icon, _, _) = mirror.load_image("images/iconfinder_Extruder_SVG_981319.png", invert = True, width = 130)
        (self._bed_icon, _, _)  = mirror.load_image("images/iconfinder_Heated_Bed_Plate_SVG_981316.png", invert = True, width = 130)
        for i in range(0, 6):
            (temp, _, _)  = mirror.load_image(f"humidity-{i}.png", invert = True, width = 80)
            self._humidity_icons.append(temp)

        self._device_type: str = config["device_type"]
        self._serial: str = config["serial"]
        self._host: str = config["host"]
        self._access_code: str = str(config["access_code"])
        self._username: str = "bblp"
        self._region: str = config["region"]
        self._email: str = config["email"]
        self._username: str = config["username"]
        self._password: str = config["password"]
        self._token_path: Path = Path.home() / config["auth_token_file"]

        self._print_job: PrintJob|None = None
        self._cover_downloaded: bool = False
        self._cover_image: pygame.Surface|None = None
        self._print_job_start: datetime

        remi_url: str = f"http://{get_public_ip()}:{remi_port}"
        generate_remi_qr_code(remi_url)
        logger.info(f"REMI running on {remi_url}")

        (self._qr_code, _, _)  = mirror.load_image(remi_qr_file, invert = True, width = qr_code_width)
        auth_token: str|None = None
        if self._token_path.is_file():
            logger.debug(f"Read auth token from {self._token_path}")
            auth_token = self._token_path.read_text().rstrip()

        client_config = {
            "host": self._host,
            "access_code": self._access_code,
            "local_mqtt": True,
            "device_type": self._device_type,
            "serial": self._serial,
            "username": self._username,
            "enable_camera": False,
            "region": self._region,
            "email": self._email,
            "username": self._username,
            "password": self._password,
        }
        if auth_token:
            client_config["auth_token"] = auth_token

        self._client: bambulab.pybambu.BambuClient = bambulab.pybambu.BambuClient(client_config)
        # TODO: Attempt cloud connection and if failing, display a QR code leading to a remi app to enter the auth code
        asyncio.run(bambu_connect(self._client))
        self.update_cloud_state()


    def update_cloud_state(self):
        logger.info("☁️  Updating cloud status ☁️")
        self._cloud_connected = False
        try:
            devices: list[dict[str: str|int]] = self._client.bambu_cloud.get_device_list()
        except ValueError as e:
            logger.error("⛈️ Not authenticated with Bambulab cloud API", exc_info=e)
        else:
            if devices is None:  # API is kinda undefined here...
                logger.error("⛈️ Not authenticated with Bambulab cloud API")
            else:
                logger.debug(f"Cloud Devices: {devices}")
                if len(devices) > 0:
                    self._cloud_connected = True
                    logger.info(f"☀️  Connected to Bambu cloud, found {devices[0]['name']} {'🟢' if devices[0]['online'] else '🔴'}")

    def timestamp_to_hms(self, timestamp: int, skip_seconds: bool = False, counting_down: bool = False) -> str:
        """Convert a timestamp in seconds to an HMS representation of "XXh YYm ZZs"
        "YYm ZZs" or "ZZs"

        Arguments:
            timestamp {int} -- Well, a timestamp. In seconds.

        Returns:
            str -- HMS representation
        """
        h: int = 0
        m: int = 0
        s: int = 0
        timestamp = int(timestamp)
        if timestamp >= 3600:
            h = timestamp/3600
            timestamp = timestamp%3600
        if timestamp >= 60:
            m = timestamp/60
            timestamp = timestamp%60
        s = timestamp

        if h > 0:
            if skip_seconds:
                return "%dh %02dm" % (h, m)
            else:
                return "%dh %02dm %02ds" % (h, m, s)
        elif m > 0:
            if skip_seconds:
                return "%dm" % (m)
            else:
                return "%dm %02ds" % (m, s)
        elif s > 0:
            if skip_seconds:
                return "< 1m"
            else:
                return "%ds" % (s)
        else:
            if skip_seconds:
                if counting_down:
                    return "soon"
                else:
                    return "now"
            else:
                return "now"


    def draw_ams(self, mirror: pymirror.Mirror):
        device: Device = self._client.get_device()
        if not device or not device.ams or len(device.ams.data) == 0 or device.ams.data[0] is None:
            # Either we have no AMS or we have not reveiced the complete AMS data yet.
            return

        slot_count: int = len(device.ams.data)
        # We subtract from 6 to match the new Bambu Handy/Studio presentation of 1 = dry, 5 = wet while the printer sends 1 = wet, 5 = dry
        ams_humidity = 6 - device.ams.data[0].humidity_index

        try:
            mirror.blit_image(self._humidity_icons[ams_humidity], mirror.width - 80, 9)
        except IndexError:
            pass

        slot_width: int = 240
        color_width: int = 75
        color_height: int = 30
        slot_spacing: int = 10
        ams_width: int = slot_count * (slot_width + slot_spacing) - slot_spacing
        x_start: int = (mirror.width - ams_width) // 2

        tray: AMSTray
        index: int = 0
        for tray in device.ams.data[0].tray:
            x: int = x_start + (slot_width - color_width) // 2
            if not tray.empty:
                m = re.match("^([a-fA-F0-9]{2})([a-fA-F0-9]{2})([a-fA-F0-9]{2})([a-fA-F0-9]{2})$", tray.color)
                if m:
                    r = int(m[1], 16)
                    g = int(m[2], 16)
                    b = int(m[3], 16)
                    a = int(m[4], 16)
                else:
                    r = g = b = a = 0

                mirror.draw_text(f"{tray.name}", x_start+slot_width//2, 60, adjustment = pymirror.Adjustment.Center, size = 40, width=slot_width)
                if not tray.empty:
                    mirror.fill_rect(x, 2, color_width, color_height, (r,g,b))
                    if index == device.ams.tray_now:
                        mirror.draw_rect(x-2, 1, color_width+4, color_height+2, (0,255,0))
                else:
                    mirror.draw_rect(x, 2, color_width, color_height, (128,128,128))
            else:
                mirror.draw_text(f"Empty", x_start+slot_width//2, 60, adjustment = pymirror.Adjustment.Center, size = 40)
                mirror.draw_rect(x, 2, color_width, color_height, (128,128,128))
            x_start += slot_width + slot_spacing
            index += 1


    def draw(self, mirror: pymirror.Mirror):
        font_size: int = 100
        font_size_small: int = 70
        state_y_pos: int = 150
        temp_y_pos: int = 275
        job_name_y_pos: int = 450
        cover_y_pos: int = 600
        layer_y_pos: int = 1100
        time_y_pos: int = 1300

        if not self._cloud_connected:
            mirror.blit_image(self._qr_code, (mirror.width - qr_code_width) // 2, cover_y_pos)
            mirror.draw_text("Scan to log in to Bambu Cloud", mirror.width//2, cover_y_pos + qr_code_width + 50, adjustment = pymirror.Adjustment.Center, size = font_size_small)

        if not self._client or not self._client.connected:
            # Show weather forecast in case there is nothing printing
            #global weather_mod
            #global xkcd_mod
            #global xkcd_globals
            #if weather_mod and xkcd_mod:
            #    weather_mod._module.draw(mirror, weather_mod.locals)
            #    xkcd_mod._module.draw(mirror, xkcd_mod.locals)
            return

        #mirror.blit_image(self._qr_code, mirror.width - qr_code_width - 20, mirror.height - qr_code_width - 180)

        self.draw_ams(mirror)

        device: Device = self._client.get_device()

        if self._print_job:
            logger.debug(f"Job: {self._print_job}")
        if self._print_job is None and device.print_job is not None and device.print_job.gcode_state not in ("IDLE", "FINISH", "FAILED"):
            logger.info(f"New print job started")
            self._print_job = device.print_job
            if not self._cover_downloaded:
                self._cover_image = None
                t = threading.Thread(target=bambu_download_cover_thread, args=(self,))
                t.isDaemon = False
                t.start()
            self._first_print_job_start = device.print_job.start_time
            self._print_job_start = device.print_job.start_time if device.print_job.start_time else datetime.datetime.now()
        elif self._print_job is not None and (device.print_job is None or device.print_job.gcode_state in ("IDLE", "FINISH", "FAILED")):
            logger.info("Print job ended")
            self._cover_downloaded = False
            self._cover_image = None
            self._print_job = self._print_job_start = None

        if self._first_print_job_start is None and device.print_job.start_time is not None:
            # Sometimes the first start time is None
            logger.debug("Got updated start time")
            self._first_print_job_start = self._print_job_start = device.print_job.start_time

        if device.hms.error_count > 0:
            logging.error(f"HMS: {device.hms}")
            # HMS: HMSList(_count=1, _errors={'Count': 1, '1-Error': 'HMS_0700_2000_0003_0001: AMS1 Slot1 filament has run out. Please wait while old filament is purged.', '1-Wiki': 'https://wiki.bambulab.com/en/x1/troubleshooting/hmscode/0700_2000_0003_0001', '1-Severity': 'common'})

        if device.print_error.on:
            logging.error(f"Print Error: {device.print_error}")

        nozzle_temp: int|None = device.temperature.nozzle_temp
        nozzle_target_temp: int|None = device.temperature.target_nozzle_temp
        bed_temp: int|None = device.temperature.bed_temp
        bed_target_temp: int|None = device.temperature.target_bed_temp
        color_heating: tuple[int, int, int] = (255,0,0)
        color_cooling: tuple[int, int, int] = (0,196,255)
        heat_limit: int = 45  # Use white text when temperature below this limit

        current_state: str|None = device.stage.description
        if current_state:
            if device.print_job.print_type == "idle":
                # If aborting during headbed preheating, `device.stage.description` might still say
                # "Headbead preheating"
                current_state = device.print_job.print_type
            current_state = current_state.replace("_", " ").title()
            mirror.draw_text(current_state, mirror.width/2, state_y_pos, adjustment = pymirror.Adjustment.Center, size = font_size_small)

        if nozzle_temp is not None:
            nozzle_temp = int(nozzle_temp)
            color: tuple[int, int, int] = (255,255,255)
            if nozzle_target_temp is not None:
                nozzle_target_temp = int(nozzle_target_temp)
                if nozzle_target_temp > nozzle_temp:
                    color = color_heating
                elif nozzle_target_temp < nozzle_temp and nozzle_temp > heat_limit:
                    color = color_cooling
            mirror.blit_image(self._extruder_icon, 100, temp_y_pos)
            mirror.draw_text(f"{nozzle_temp}°", 250, temp_y_pos+10, color, adjustment = pymirror.Adjustment.Left, size = font_size)
            # TODO: Handle target as "warming up" or "cooling down"
        if bed_temp is not None:
            bed_temp = int(bed_temp)
            color: tuple[int, int, int] = (255,255,255)
            if bed_target_temp is not None:
                bed_target_temp = int(bed_target_temp)
                if bed_target_temp > bed_temp:
                    color = color_heating
                elif bed_target_temp < bed_temp and bed_temp > heat_limit:
                    color = color_cooling
            mirror.blit_image(self._bed_icon, mirror.width - 430, temp_y_pos-10)
            mirror.draw_text(f"{bed_temp}°", mirror.width - 270, temp_y_pos+10, color, adjustment = pymirror.Adjustment.Left, size = font_size)

        # Only draw temperature and time unless we're idle
        #if device.stage.description == "idle":
        if not self._cloud_connected:
            mirror.blit_image(self._qr_code, (mirror.width - qr_code_width) // 2, cover_y_pos)
            mirror.draw_text("Scan to log in to Bambu Cloud", mirror.width//2, cover_y_pos + qr_code_width + 50, adjustment = pymirror.Adjustment.Center, size = font_size_small)
            return

        if self._print_job is not None:
            file_name: str = device.print_job.subtask_name.replace("_", " ")
            mirror.draw_text(f"{file_name}", mirror.width/2, job_name_y_pos, adjustment = pymirror.Adjustment.Center, size = font_size_small, width=mirror.width-200)

            if self._cover_image:
                mirror.blit_image(self._cover_image, (mirror.width - self._cover_w) // 2, cover_y_pos)

            current_layer: int = device.print_job.current_layer
            layer_count: int = device.print_job.total_layers
            progress: int = device.print_job.print_percentage
            mirror.draw_text(f"Layer {current_layer} of {layer_count} ({progress}%)", mirror.width/2, layer_y_pos, adjustment = pymirror.Adjustment.Center, size = font_size_small)

            remaining_time: int = device.print_job.remaining_time
            remaining_time = int(remaining_time)
            remaining_time *= 60
            mirror.draw_text("Remaining", mirror.width-50, time_y_pos-font_size_small, adjustment = pymirror.Adjustment.Right, size = 50)
            if remaining_time > 0:
                mirror.draw_text(self.timestamp_to_hms(remaining_time, skip_seconds=True, counting_down=True), mirror.width-50, time_y_pos, adjustment = pymirror.Adjustment.Right, size = font_size_small)
            else:
                mirror.draw_text("Any second now", mirror.width-50, time_y_pos, adjustment = pymirror.Adjustment.Right, size = font_size_small)

            if self._print_job_start:
                mirror.draw_text("Elapsed", mirror.width/2-100, time_y_pos-font_size_small, adjustment = pymirror.Adjustment.Right, size = 50)
                print_start_time = int(time.mktime(self._print_job_start.timetuple()))
                mirror.draw_text(self.timestamp_to_hms(int(time.time()) - print_start_time, skip_seconds=True), mirror.width/2-100, time_y_pos, adjustment = pymirror.Adjustment.Right, size = font_size_small)

            if self._cover_downloaded and self._cover_image is None:
                # Load cover last as it may take time to scale it
                (self._cover_image, self._cover_w, self._cover_h) = mirror.load_image(self._cover_fname, 512)
                logger.info(f"Cover loaded")

        return


    #    if estimated_print_time is not None:
    #        logger.debug("  Estimated       %d", estimated_print_time)
    #    if last_print_time is not None:
    #        logger.debug("  Last            %d", last_print_time)

        if 0:  # TODO: is this interesting to show?
            if filemant_length is not None:
                if filemant_length > 1000:
                    msg = "%dm" % round(filemant_length/1000.0)
                elif filemant_length > 100:
                    msg = "%dcm" % round(filemant_length/100.0)
                elif filemant_length > 10:
                    msg = "%ddm" % round(filemant_length/10.0)
                else:
                    msg = "%dmm" % (filemant_length/1)
                mirror.draw_text(msg + " of filament required", mirror.width/2, 1300, adjustment = pymirror.Adjustment.Center, size = 100)



def init(mirror: pymirror.Mirror, config: dict):

    logger.info(f"Hello world from the BambuLab module with config {config}")
    return BambuDisplay(mirror, config)

    if "weather_and_xkcd" in info:
        # This is a bit hackish. We load other modules from here and display them
        # manually when the 3D printer is idle.
        weather_conf = {
            "mqtt_host": "127.0.0.1",
            "text_size": 40,
            "skip_night": True
        }
        weather_mod = pymirror.Module(mirror, "Weather", "modules/weather", weather_conf, 0, 0, -1, 400)
        weather_mod.load()
        weather_mod.locals = weather_mod._module.init(mirror, weather_conf)

        xkcd_conf = {
            "image_name": "../xkcdScience.png",
            "x_offset": 350,
            "y_offset": 550,
            "height": 500,
            "width": 500,
            "invert": False
        }
        xkcd_mod = pymirror.Module(mirror, "XKCD", "modules/pngimage.py", xkcd_conf, 550, 350, 500, 500)
        xkcd_globals = xkcd_mod.load()
        xkcd_mod.locals = xkcd_mod._module.init(mirror, xkcd_conf)

    config = {
        "host": host,
        "access_code": access_code,
        "local_mqtt": True,
        "device_type": device_type,
        "serial": serial,
        "username": username,
        "enable_camera": False,
        "region": info["region"],
        "email": info["email"],
        "username": info["username"],
        "password": info["password"],
        "auth_token_file": info["auth_token_path"],
    }
    logger.warning(f"Bambu config: {config}")
    client: bambulab.pybambu.BambuClient = bambulab.pybambu.BambuClient(config)
    # TODO: Attempt cloud connection and if failing, display a QR code leading to a remi app to enter the auth code
    asyncio.run(bambu_connect(client))

    return {"state": None,
            "counter": 0,
            "print_job": None,
            "client": client,
            "cover_downloaded": False,
            "remi_thread": remi_thread,
            "qr_code": qr_code,
            }

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
