"""OSC server that listens for QLab messages and routes them to the controller.

Address scheme:  /<prefix>/<target>/<command> [args...]
  prefix   - configurable, defaults to "wyze"
  target   - a bulb name, a group name, or "all"
  command  - on | off | toggle | brightness | fade | color | colorfade |
             colortemp | hsv | sunmatch | away
"""

import logging

from pythonosc.dispatcher import Dispatcher
from pythonosc.osc_server import ThreadingOSCUDPServer

from .wyze_controller import rgb_to_hex

log = logging.getLogger("qlab_wyze_bridge.osc")


def _color_from_args(args):
    """Accept color as a single hex string/int, or as r g b (0-255)."""
    if len(args) >= 3:
        return rgb_to_hex(args[0], args[1], args[2])
    if len(args) == 1:
        value = args[0]
        if isinstance(value, str):
            return value.lstrip("#").lower()
        # Treat a lone integer as a packed 0xRRGGBB value.
        return "{:06x}".format(int(value) & 0xFFFFFF)
    return "ffffff"


class OscBridge:
    def __init__(self, controller, host="0.0.0.0", port=9000, prefix="wyze"):
        self.controller = controller
        self.host = host
        self.port = int(port)
        self.prefix = prefix.strip("/").lower()
        self.dispatcher = Dispatcher()
        self.dispatcher.set_default_handler(self._route)
        self.server = None

    def serve_forever(self):
        self.server = ThreadingOSCUDPServer((self.host, self.port), self.dispatcher)
        log.info("Listening for QLab OSC on %s:%d  (address: /%s/<target>/<command>)",
                 self.host, self.port, self.prefix)
        self.server.serve_forever()

    def shutdown(self):
        if self.server is not None:
            self.server.shutdown()

    # ----------------------------------------------------------------- routing
    def _route(self, address, *args):
        # A bad message must never take down the server.
        try:
            self._handle(address, list(args))
        except Exception:  # noqa: BLE001 - log and keep serving
            log.exception("Error handling OSC message %s %s", address, list(args))

    def _handle(self, address, args):
        parts = [p for p in address.strip("/").split("/") if p != ""]
        if not parts or parts[0].lower() != self.prefix:
            log.debug("Ignoring message outside prefix: %s", address)
            return
        if len(parts) < 3:
            log.warning("Malformed address (need /%s/<target>/<command>): %s",
                        self.prefix, address)
            return

        target = parts[1]
        command = parts[2].lower()
        c = self.controller

        if command == "on":
            c.turn_on(target)
        elif command == "off":
            c.turn_off(target)
        elif command == "toggle":
            c.toggle(target)
        elif command == "brightness":
            c.set_brightness(target, _arg(args, 0, 100))
        elif command == "fade":
            c.fade_brightness(target, _arg(args, 0, 100), _arg(args, 1, 1.0))
        elif command == "color":
            c.set_color(target, _color_from_args(args))
        elif command == "colorfade":
            # /colorfade <hex> <secs>  OR  /colorfade <r> <g> <b> <secs>
            if len(args) >= 4:
                c.fade_color(target, rgb_to_hex(args[0], args[1], args[2]),
                             _arg(args, 3, 1.0))
            else:
                c.fade_color(target, _color_from_args(args[:1]), _arg(args, 1, 1.0))
        elif command == "colortemp":
            c.set_color_temp(target, _arg(args, 0, 4000))
        elif command == "hsv":
            c.set_hsv(target, _arg(args, 0, 0), _arg(args, 1, 100), _arg(args, 2, 100))
        elif command in ("sunmatch", "sun_match"):
            c.set_sun_match(target, _arg(args, 0, True))
        elif command == "away":
            c.set_away_mode(target, _arg(args, 0, True))
        else:
            log.warning("Unknown command '%s' in %s", command, address)


def _arg(args, index, default):
    return args[index] if len(args) > index else default
