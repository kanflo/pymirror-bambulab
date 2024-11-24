#!/usr/bin/env python

"""Demonstrate usage of the generic dialog, starting a thread when dismissed."""

import logging
import threading
import time
from enum import Enum

import remi
import remi.gui as gui
from remi import App, tools

import pymirror

import bambulab.pybambu as pybambu

from bambulab.bambudisplay import BambuDisplay

logger = logging.getLogger(__name__)

_bambu_display: BambuDisplay

#    auth_token: str|None = None
#    token_path: Path = Path.home() / info["auth_toke_file"]
#    if token_path.is_file():
#        logger.info("Reading auth token")
#        auth_token = token_path.read_text().rstrip()

class CloudState(Enum):
    UNKNOWN = 1
    LOGGED_OUT = 2
    CODE_SENT = 3
    LOGGING_IN = 4
    LOGGED_IN = 5
    BLOCKED = 6

class MyApp(App):
    def __init__(self, *args):
        self.init: bool = False
        self.cloud_state: CloudState = CloudState.UNKNOWN
        super(MyApp, self).__init__(*args)

    def do_init(self):
        if self.init:
            return
        """Do init of this object, see comment at the of of file why this is needed"""
        self.bambu_display: BambuDisplay = _bambu_display
        self.client: pybambu.BambuClient = self.bambu_display._client
        self.cloud: pybambu.BambuCloud = self.client.bambu_cloud
        self.update_cloud_state()
        self.init = True

    def set_cloud_state(self, new_state: CloudState):
        logger.debug(f"Changing cloud state from {self.cloud_state} -> {new_state}")
        self.cloud_state = new_state
        self.bambu_display._cloud_connected = self.cloud_state == CloudState.LOGGED_IN

    def update_cloud_state(self):
        logger.info("☁️  Updating cloud status ☁️")
        self.set_cloud_state(CloudState.UNKNOWN)

        try:
            devices: list[dict[str: str|int]] = self.cloud.get_device_list()
        except ValueError as e:
            self.set_cloud_state(CloudState.LOGGED_OUT)
            logger.error("Not authenticated with Bambulab cloud API", exc_info=e)
        else:
            if devices is None:  # API is kinda undefined here...
                self.set_cloud_state(CloudState.LOGGED_OUT)
                logger.error("Not authenticated with Bambulab cloud API")
            else:
                self.set_cloud_state(CloudState.LOGGED_IN)
                logger.debug(f"Cloud Devices: {devices}")
                if len(devices) > 0:
                    name = f"{devices[0]['name']} is connected"

    def update_ui(self):
        self.main_container.empty()
        self.top_container.empty()
        self.bottom_container.empty()
        self.main_container.append(self.top_label)
        self.spinner.stop()

        match self.cloud_state:
            case CloudState.UNKNOWN:
                self.top_label.set_text("Unknown state")
            case CloudState.LOGGED_OUT:
                self.top_label.set_text("Logged out")
                self.bottom_container.append(self.log_in_button)
            case CloudState.CODE_SENT:
                self.top_label.set_text("Enter authentication code")
                self.top_container.append(self.auto_code_field)
                self.bottom_container.append(self.enter_code_button)
            case CloudState.LOGGING_IN:
                self.top_label.set_text("Logging in")
                self.top_container.append(self.spinner)
                self.spinner.start()
            case CloudState.LOGGED_IN:
                self.top_label.set_text("Logged in")
                self.bottom_container.append(self.log_out_button)
            case CloudState.BLOCKED:
                self.top_label.set_text("Blocked by CloudFlare")
                self.bottom_container.append(self.log_in_button)
        self.main_container.append([self.top_container, self.bottom_container])

    def main(self):
        logger.info("MyApp main")
        if not self.init:
            self.do_init()

        self.update_cloud_state()
        #self.container = gui.Container(width = 540, margin = "0px auto", style = {"display": "block", "overflow": "hidden"})
        self.main_container = gui.VBox()
        self.top_container = gui.HBox()
        self.bottom_container = gui.HBox()

        self.top_label = gui.Label("", width=200, height=30, margin="10px")

        self.log_in_button = gui.Button("Log In", width=150, height=30, margin="10px")
        self.log_in_button.onclick.do(self.login_button_pressed)

        self.send_code_button = gui.Button("Send Code", width=150, height=30, margin="10px")
        self.send_code_button.onclick.do(self.send_code_button_pressed)

        self.enter_code_button = gui.Button("OK", width=150, height=30, margin="10px")
        self.enter_code_button.onclick.do(self.enter_code_button_pressed)

        self.log_out_button = gui.Button("Log Out", width=150, height=30, margin="10px")
        self.log_out_button.onclick.do(self.logout_button_pressed)

        self.auto_code_field = gui.TextInput(width=150, height=30, margin="10px")

        self.spinner = gui.Spinner(size = 20, color = "#000")

        self.update_ui()
        return self.main_container

    def login_button_pressed(self, widget: gui.Widget):
        logger.info("Login button pressed")
        region: str = self.bambu_display._region
        email: str = self.bambu_display._email
        password: str = self.bambu_display._password
        try:
            self.cloud.login(region, email, password)
        except pybambu.bambu_cloud.CloudflareError:
            logger.error(f"Blocked by Cloudflare, sorry")
            self.set_cloud_state(CloudState.BLOCKED)
        except pybambu.bambu_cloud.EmailCodeRequiredError:
            logger.info(f"Getting email verification code")
            self.cloud._get_email_verification_code()
            self.set_cloud_state(CloudState.CODE_SENT)
        self.update_ui()

    def logout_button_pressed(self, widget: gui.Widget):
        logger.info("Logout button pressed")
        self.set_cloud_state(CloudState.LOGGED_OUT)
        self.update_ui()

    def send_code_button_pressed(self, widget: gui.Widget):
        logger.info("Send code button pressed")
        self.set_cloud_state(CloudState.CODE_SENT)
        self.update_ui()

    def enter_code_button_pressed(self, widget: gui.Widget):
        logger.info("Enter code button pressed")
        self.set_cloud_state(CloudState.LOGGING_IN)
        self.update_ui()
        code: str = self.auto_code_field.get_text()
        logger.info(f"Using code {code}")
        try:
            self.cloud.login_with_verification_code(code)
            token = self.cloud._auth_token
            logger.info("Got new token")
            with open(self.bambu_display._token_path, "w+") as f:
                f.write(token)
            self.set_cloud_state(CloudState.LOGGED_IN)
        except pybambu.bambu_cloud.EmailCodeExpiredError:
            logger.info("Email code expired, requesting new.")
            self.cloud.bambu_cloud._get_email_verification_code()
            self.set_cloud_state(CloudState.CODE_SENT)
        except ValueError:
            self.top_label.set_text("Failed to verify code, requested new")
            self.cloud.bambu_cloud._get_email_verification_code()
            self.set_cloud_state(CloudState.CODE_SENT)
        except pybambu.bambu_cloud.EmailCodeIncorrectError:
            self.top_label.set_text("Incorrect code, try again")
        self.update_ui()

def start(bambu_display: BambuDisplay, port: int):
    # I am not too happy about the life cycle of the REMI apps.
    # The application object is created when a client accesses
    # the URL, in a completely different context meaning we cannot
    # set `bambu_module` as an argument to the MyApp constructor.
    global _bambu_display
    _bambu_display = bambu_display
    remi.start(MyApp,
                title = "Workshop Bambu Mirror",
                debug = False,
                address = "0.0.0.0",
                port = port,
                start_browser = False,
            )
