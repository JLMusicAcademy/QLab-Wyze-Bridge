"""Art-Net (DMX-over-Ethernet) listener that drives Wyze bulbs.

Lets QLab control the bulbs as if they were RGB DMX fixtures, so you can use
QLab's Light cues — including the color picker and intensity faders.

Each bulb is patched to a DMX start address and uses 4 consecutive channels:

    channel + 0 : Red       (0-255)
    channel + 1 : Green     (0-255)
    channel + 2 : Blue      (0-255)
    channel + 3 : Dimmer    (0 = off, 1-255 = brightness)

Because Wyze bulbs are cloud-controlled (not local DMX), incoming frames are
sampled and throttled: the listener only pushes a change to a bulb when its
values actually change, and no faster than `min_interval`. Static looks, on/off
and slow fades work well; fast chases will look stepped and lag — that's a
limitation of the Wyze cloud, not the bridge.
"""

import logging
import socket
import threading
import time

from .wyze_controller import rgb_to_hex

log = logging.getLogger("qlab_wyze_bridge.artnet")

ARTNET_PORT = 6454
ARTNET_HEADER = b"Art-Net\x00"
OP_DMX = 0x5000


def parse_artdmx(data):
    """Parse an ArtDMX packet. Returns (universe, channel_bytes) or None."""
    if len(data) < 18 or data[:8] != ARTNET_HEADER:
        return None
    opcode = data[8] | (data[9] << 8)  # little-endian
    if opcode != OP_DMX:
        return None
    sub_uni = data[14]
    net = data[15]
    universe = (net << 8) | sub_uni
    length = (data[16] << 8) | data[17]  # big-endian
    length = max(0, min(length, len(data) - 18))
    return universe, data[18:18 + length]


class _Fixture:
    def __init__(self, bulb, universe, base):
        self.bulb = bulb
        self.universe = universe
        self.base = base          # 0-based index of the Red channel
        self.last_on = None
        self.last_hex = None
        self.last_bri = None


class ArtNetServer:
    def __init__(self, controller, config, fade_min_interval=0.25):
        self.controller = controller
        self.host = config.get("host", "127.0.0.1")
        self.port = int(config.get("port", ARTNET_PORT))
        self.default_universe = int(config.get("universe", 0))
        # Don't poll faster than this (per dispatch cycle). Default a bit slower
        # than the OSC fade rate to be gentle on Wyze's cloud.
        self.min_interval = max(0.1, float(
            config.get("min_interval", max(0.3, fade_min_interval))))

        self._fixtures = []
        self._build_patch(config.get("patch", {}) or {})

        self._dmx = {}           # universe -> bytearray(512)
        self._received = set()   # universes we've actually seen a frame for
        self._lock = threading.Lock()
        self._sock = None
        self._stop = threading.Event()

    def _build_patch(self, patch):
        for name, spec in patch.items():
            bulbs = self.controller.resolve(name)
            if not bulbs:
                log.warning("Art-Net patch: unknown bulb '%s' — skipping.", name)
                continue
            if isinstance(spec, dict):
                universe = int(spec.get("universe", self.default_universe))
                start = int(spec.get("channel", spec.get("address", 1)))
            else:
                universe = self.default_universe
                start = int(spec)
            if start < 1 or start > 509:
                log.warning("Art-Net patch: bad start channel %s for '%s' "
                            "(need 1-509).", start, name)
                continue
            for bulb in bulbs:
                self._fixtures.append(_Fixture(bulb, universe, start - 1))
                log.info("Art-Net patch: %s -> universe %d, channels %d-%d "
                         "(R,G,B,Dimmer)", bulb.name, universe, start, start + 3)

    def start(self):
        if not self._fixtures:
            log.warning("Art-Net enabled but no fixtures patched — not starting.")
            return
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        except OSError:
            pass
        self._sock.bind((self.host, self.port))
        threading.Thread(target=self._receive_loop, daemon=True).start()
        threading.Thread(target=self._dispatch_loop, daemon=True).start()
        log.info("Art-Net listening on %s:%d — %d fixture(s), throttle %.2fs",
                 self.host, self.port, len(self._fixtures), self.min_interval)

    def stop(self):
        self._stop.set()
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass

    def _receive_loop(self):
        while not self._stop.is_set():
            try:
                data, _ = self._sock.recvfrom(1024)
            except OSError:
                break
            parsed = parse_artdmx(data)
            if not parsed:
                continue
            universe, dmx = parsed
            with self._lock:
                buf = self._dmx.get(universe)
                if buf is None:
                    buf = bytearray(512)
                    self._dmx[universe] = buf
                buf[:len(dmx)] = dmx
                self._received.add(universe)

    def _dispatch_loop(self):
        while not self._stop.is_set():
            time.sleep(self.min_interval)
            for fx in self._fixtures:
                with self._lock:
                    if fx.universe not in self._received:
                        continue
                    buf = self._dmx[fx.universe]
                    r, g, b, dim = (buf[fx.base], buf[fx.base + 1],
                                    buf[fx.base + 2], buf[fx.base + 3])
                try:
                    self._apply(fx, r, g, b, dim)
                except Exception:  # noqa: BLE001 - never let one bulb stop the loop
                    log.exception("Art-Net update failed for %s", fx.bulb.name)

    def _apply(self, fx, r, g, b, dim):
        c = self.controller
        if dim <= 0:
            if fx.last_on is not False:
                log.info("Art-Net: %s OFF", fx.bulb.name)
                c._power(fx.bulb, False)
                fx.last_on, fx.last_hex, fx.last_bri = False, None, None
            return

        hexstr = rgb_to_hex(r, g, b)
        bri = int(round(dim / 255.0 * 100)) or 1
        if fx.last_on is not True:
            log.info("Art-Net: %s ON  #%s @ %d%%", fx.bulb.name, hexstr, bri)
            c._power(fx.bulb, True)
            c._apply_color(fx.bulb, hexstr)
            c._apply_brightness(fx.bulb, bri)
            fx.last_on, fx.last_hex, fx.last_bri = True, hexstr, bri
            return

        if hexstr != fx.last_hex:
            log.debug("Art-Net: %s color #%s", fx.bulb.name, hexstr)
            c._apply_color(fx.bulb, hexstr)
            fx.last_hex = hexstr
        if bri != fx.last_bri:
            log.debug("Art-Net: %s brightness %d%%", fx.bulb.name, bri)
            c._apply_brightness(fx.bulb, bri)
            fx.last_bri = bri
