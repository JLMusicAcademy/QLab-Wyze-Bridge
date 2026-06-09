"""Wraps the Wyze SDK and turns high-level commands into bulb API calls.

Responsibilities:
  * Authenticate to Wyze (auto re-login if the access token expires).
  * Discover bulbs and resolve friendly names / groups / "all" to devices.
  * Execute on/off/brightness/color/color-temp/etc. commands.
  * Run software fades (the Wyze API has no native fade, so we interpolate
    brightness or RGB over time in a background thread).
"""

import colorsys
import json
import logging
import os
import re
import threading
import time

log = logging.getLogger("qlab_wyze_bridge.wyze")


class Bulb:
    """A controllable bulb: a friendly name plus the MAC + model the API needs."""

    def __init__(self, name, mac, model, nickname=""):
        self.name = name
        self.mac = mac
        self.model = model
        self.nickname = nickname or name

    def __repr__(self):
        return f"Bulb(name={self.name!r}, mac={self.mac!r}, model={self.model!r})"


def slug(text):
    """Normalize a name for matching: lowercase, spaces/punctuation -> underscore."""
    text = str(text).strip().lower()
    text = re.sub(r"[\s\-]+", "_", text)
    text = re.sub(r"[^a-z0-9_]", "", text)
    return text


def _clamp(value, low, high):
    return max(low, min(high, value))


def _to_int(value):
    try:
        return int(round(float(value)))
    except (TypeError, ValueError):
        return 0


def _truthy(value):
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "on", "yes", "y")
    return bool(value)


def hex_to_rgb(hexstr):
    hexstr = str(hexstr).lstrip("#")
    if len(hexstr) != 6:
        return (255, 255, 255)
    try:
        return tuple(int(hexstr[i:i + 2], 16) for i in (0, 2, 4))
    except ValueError:
        return (255, 255, 255)


def rgb_to_hex(r, g, b):
    return "{:02x}{:02x}{:02x}".format(
        _clamp(_to_int(r), 0, 255),
        _clamp(_to_int(g), 0, 255),
        _clamp(_to_int(b), 0, 255),
    )


# Friendly color names usable anywhere a color is expected, e.g.
#   /wyze/gh1/color blue   or   /wyze/gh1/colorfade amber 3.0
# For accurate whites, prefer the colortemp command (Kelvin); the white-ish
# entries here are RGB approximations.
NAMED_COLORS = {
    "red": "ff0000", "green": "00ff00", "lime": "80ff00", "blue": "0000ff",
    "navy": "000080", "cyan": "00ffff", "aqua": "00ffff", "teal": "008080",
    "magenta": "ff00ff", "fuchsia": "ff00ff", "pink": "ff69b4", "rose": "ff007f",
    "purple": "8000ff", "violet": "ee82ee", "indigo": "4b0082", "lavender": "b57edc",
    "yellow": "ffff00", "gold": "ffd700", "amber": "ffbf00", "orange": "ff7000",
    "white": "ffffff", "warmwhite": "ff8a3d", "warm": "ff8a3d",
    "daylight": "cfe2ff", "coolwhite": "cfe2ff",
}


def resolve_color(value):
    """Turn a color name or hex string into a 6-digit hex string.

    Accepts: a named color ("blue"), 6-digit hex ("ff0000" or "#ff0000"), or
    3-digit shorthand hex ("f00"). Falls back to white on anything unrecognized.
    """
    if value is None:
        return "ffffff"
    s = str(value).strip().lower()
    if s in NAMED_COLORS:
        return NAMED_COLORS[s]
    s = s.lstrip("#")
    hexchars = set("0123456789abcdef")
    if len(s) == 6 and all(c in hexchars for c in s):
        return s
    if len(s) == 3 and all(c in hexchars for c in s):
        return "".join(c * 2 for c in s)
    log.warning("Unknown color %r; defaulting to white. "
                "Use a name (%s) or hex like ff0000.",
                value, ", ".join(sorted(NAMED_COLORS)))
    return "ffffff"


class WyzeController:
    def __init__(self, credentials, bulbs_config=None, groups=None,
                 fade_min_interval=0.25, simulate=False):
        self._credentials = credentials or {}
        self._bulbs_config = bulbs_config or {}
        self._groups = {slug(k): [slug(n) for n in v]
                        for k, v in (groups or {}).items()}
        self._fade_min_interval = max(0.05, float(fade_min_interval))
        self._simulate = simulate

        self._client = None
        self._api_lock = threading.Lock()

        self.bulbs = {}        # slug name -> Bulb
        self._by_mac = {}      # mac -> Bulb

        # In-memory state used as the starting point for fades.
        self._brightness_state = {}   # mac -> 0..100
        self._color_state = {}        # mac -> "rrggbb"

        self._fades = {}              # mac -> threading.Event (cancel signal)
        self._fade_lock = threading.Lock()

        # Cache the Wyze session here so restarts don't re-hit the rate-limited
        # login endpoint. Lives next to the project, locked to mode 600.
        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self._token_cache_path = os.path.join(project_root, ".wyze_token.json")

    # ------------------------------------------------------------------ setup
    def connect(self):
        """Log in (unless simulating) and build the bulb registry."""
        if self._simulate:
            log.warning("SIMULATE mode — no Wyze connection; commands are logged only.")
        else:
            self._login()
            self._discover()
        self._load_configured_bulbs()
        if not self.bulbs:
            log.warning("No bulbs registered. Check --list-devices or your config.")
        else:
            log.info("Registered %d bulb(s): %s",
                     len(self.bulbs), ", ".join(sorted(self.bulbs)))

    def _login(self):
        """Establish a Wyze client, preferring a cached session over a fresh
        login (Wyze rate-limits the login endpoint aggressively)."""
        from wyze_sdk import Client

        creds = self._credentials
        if creds.get("token"):
            self._client = Client(token=creds["token"],
                                  refresh_token=creds.get("refresh_token"))
            log.info("Connected to Wyze using a configured access token.")
            return

        cached = self._load_token_cache()
        if cached and cached.get("access_token"):
            # Reuse the saved session; validity is checked lazily and refreshed
            # on demand, so a normal restart costs zero login calls.
            self._client = Client(token=cached["access_token"],
                                  refresh_token=cached.get("refresh_token"))
            log.info("Reusing cached Wyze session (no login needed).")
            return

        self._full_login()

    def _full_login(self):
        """Perform a fresh email/password login and cache the resulting tokens."""
        from wyze_sdk import Client

        creds = self._credentials
        if not (creds.get("email") and creds.get("password")):
            raise RuntimeError(
                "Wyze credentials missing. Provide email + password (and "
                "key_id + api_key) in config.yaml or via environment variables.")

        kwargs = {"email": creds["email"], "password": creds["password"]}
        for opt in ("key_id", "api_key", "totp_key"):
            if creds.get(opt):
                kwargs[opt] = creds[opt]
        self._client = Client(**kwargs)
        log.info("Logged in to Wyze as %s.", creds["email"])
        self._save_tokens()

    def _reauth(self):
        """Recover an expired session: try a cheap token refresh first, then
        fall back to a full login. Returns True on success."""
        try:
            if self._client is not None and getattr(self._client, "_refresh_token", None):
                self._client.refresh_token()
                self._save_tokens()
                log.info("Refreshed Wyze access token.")
                return True
        except Exception as err:  # noqa: BLE001
            log.warning("Token refresh failed (%s); attempting full login.", err)
        try:
            self._full_login()
            return True
        except Exception as err:  # noqa: BLE001
            log.error("Wyze re-login failed: %s", err)
            return False

    def _load_token_cache(self):
        try:
            with open(self._token_cache_path, "r", encoding="utf-8") as fh:
                return json.load(fh)
        except (OSError, ValueError):
            return None

    def _save_tokens(self):
        try:
            token = getattr(self._client, "_token", None)
            if not token:
                return
            data = {
                "access_token": token,
                "refresh_token": getattr(self._client, "_refresh_token", None),
                "saved_at": time.time(),
            }
            with open(self._token_cache_path, "w", encoding="utf-8") as fh:
                json.dump(data, fh)
            os.chmod(self._token_cache_path, 0o600)
        except Exception as err:  # noqa: BLE001
            log.debug("Could not write token cache: %s", err)

    def _discover(self):
        from wyze_sdk.errors import WyzeApiError

        try:
            devices = self._client.devices_list()
        except WyzeApiError as err:
            # A cached token may have expired — refresh/re-login and retry once.
            log.info("Device list failed (%s); re-authenticating.", err)
            if not self._reauth():
                raise
            devices = self._client.devices_list()

        for dev in devices:
            model = (getattr(getattr(dev, "product", None), "model", None)
                     or getattr(dev, "product_model", "") or "")
            dev_type = (getattr(dev, "type", "") or "").lower()
            nickname = getattr(dev, "nickname", "") or ""
            # Keep lights/bulbs; skip cameras, plugs, sensors, etc.
            is_light = dev_type in ("light", "mesh light") or model.upper().startswith(
                ("WLPA", "HL_", "WLST", "LWA"))
            if not is_light:
                continue
            name = slug(nickname) or slug(dev.mac)
            bulb = Bulb(name=name, mac=dev.mac, model=model, nickname=nickname)
            self.bulbs[name] = bulb
            self._by_mac[dev.mac] = bulb
            log.info("Discovered bulb '%s' (mac=%s model=%s)", name, dev.mac, model)

    def _load_configured_bulbs(self):
        for raw_name, info in self._bulbs_config.items():
            if not info or "mac" not in info:
                log.warning("Skipping bulb '%s' in config: missing 'mac'.", raw_name)
                continue
            name = slug(raw_name)
            bulb = Bulb(name=name, mac=info["mac"],
                        model=info.get("model", "WLPA19C"),
                        nickname=info.get("nickname", raw_name))
            self.bulbs[name] = bulb
            self._by_mac[bulb.mac] = bulb

    # -------------------------------------------------------------- resolving
    def resolve(self, target):
        """Return the list of Bulbs a target string refers to."""
        key = slug(target)
        if key in ("all", "everyone", ""):
            return list(self.bulbs.values())
        if key in self._groups:
            return [self.bulbs[n] for n in self._groups[key] if n in self.bulbs]
        if key in self.bulbs:
            return [self.bulbs[key]]
        if target in self._by_mac:
            return [self._by_mac[target]]
        log.warning("Unknown target '%s' — no matching bulb, group, or 'all'.", target)
        return []

    def describe(self):
        """Human-readable list of registered bulbs, for --list-devices."""
        if not self.bulbs:
            return "No bulbs registered."
        lines = ["Registered bulbs (use the name as the OSC target):"]
        for name in sorted(self.bulbs):
            b = self.bulbs[name]
            lines.append(f"  {name:<20} mac={b.mac}  model={b.model}  ({b.nickname})")
        if self._groups:
            lines.append("Groups:")
            for g, members in self._groups.items():
                lines.append(f"  {g:<20} -> {', '.join(members)}")
        return "\n".join(lines)

    # ------------------------------------------------------------ API helpers
    def _call(self, fn):
        """Run a Wyze API call with a single re-login retry on auth failure."""
        if self._simulate or self._client is None:
            return
        from wyze_sdk.errors import WyzeApiError

        with self._api_lock:
            try:
                fn(self._client)
            except WyzeApiError as err:
                log.warning("Wyze API error (%s); re-authenticating and retrying.", err)
                if self._reauth():
                    fn(self._client)

    # ------------------------------------------------------------- commands
    def turn_on(self, target):
        for b in self.resolve(target):
            log.info("ON  %s", b.name)
            self._cancel_fade(b.mac)
            self._call(lambda c, b=b: c.bulbs.turn_on(
                device_mac=b.mac, device_model=b.model))

    def turn_off(self, target):
        for b in self.resolve(target):
            log.info("OFF %s", b.name)
            self._cancel_fade(b.mac)
            self._call(lambda c, b=b: c.bulbs.turn_off(
                device_mac=b.mac, device_model=b.model))

    def toggle(self, target):
        for b in self.resolve(target):
            is_on = self._read_is_on(b)
            log.info("TOGGLE %s (currently %s)", b.name, "on" if is_on else "off")
            (self.turn_off if is_on else self.turn_on)(b.name)

    def set_brightness(self, target, value):
        value = _clamp(_to_int(value), 0, 100)
        for b in self.resolve(target):
            self._cancel_fade(b.mac)
            log.info("BRIGHTNESS %s -> %d%%", b.name, value)
            self._apply_brightness(b, value)

    def fade_brightness(self, target, value, duration=1.0):
        value = _clamp(_to_int(value), 0, 100)
        duration = max(0.0, float(duration))
        for b in self.resolve(target):
            log.info("FADE %s -> %d%% over %.2fs", b.name, value, duration)
            self._start_fade(b, "brightness", target_val=value, duration=duration)

    def set_color(self, target, color):
        hexstr = resolve_color(color)
        for b in self.resolve(target):
            self._cancel_fade(b.mac)
            log.info("COLOR %s -> #%s", b.name, hexstr)
            self._apply_color(b, hexstr)

    def fade_color(self, target, color, duration=1.0):
        target_rgb = hex_to_rgb(resolve_color(color))
        duration = max(0.0, float(duration))
        for b in self.resolve(target):
            log.info("COLORFADE %s -> #%s over %.2fs",
                     b.name, rgb_to_hex(*target_rgb), duration)
            self._start_fade(b, "color", target_rgb=target_rgb, duration=duration)

    def set_color_temp(self, target, kelvin):
        kelvin = _clamp(_to_int(kelvin), 1800, 6500)
        for b in self.resolve(target):
            self._cancel_fade(b.mac)
            log.info("COLORTEMP %s -> %dK", b.name, kelvin)
            self._call(lambda c, b=b: c.bulbs.set_color_temp(
                device_mac=b.mac, device_model=b.model, color_temp=kelvin))

    def set_hsv(self, target, h, s, v):
        """Color-wheel control. Hue+saturation set the color; value -> brightness."""
        h = float(h) % 360.0
        s = _clamp(float(s), 0.0, 100.0)
        v = _clamp(_to_int(v), 0, 100)
        r, g, b_ = colorsys.hsv_to_rgb(h / 360.0, s / 100.0, 1.0)
        hexstr = rgb_to_hex(r * 255, g * 255, b_ * 255)
        for bulb in self.resolve(target):
            self._cancel_fade(bulb.mac)
            log.info("HSV %s -> h%.0f s%.0f v%d (#%s)", bulb.name, h, s, v, hexstr)
            self._apply_color(bulb, hexstr)
            self._apply_brightness(bulb, v)

    def set_sun_match(self, target, on):
        on = _truthy(on)
        for b in self.resolve(target):
            log.info("SUNMATCH %s -> %s", b.name, on)
            self._call(lambda c, b=b: c.bulbs.set_sun_match(
                device_mac=b.mac, device_model=b.model, sun_match=on))

    def set_away_mode(self, target, on):
        on = _truthy(on)
        for b in self.resolve(target):
            log.info("AWAY %s -> %s", b.name, on)
            self._call(lambda c, b=b: c.bulbs.set_away_mode(
                device_mac=b.mac, device_model=b.model, away_mode=on))

    # ----------------------------------------------------- low-level applies
    def _power(self, bulb, on):
        """Turn a resolved Bulb on/off without logging at INFO or touching
        fades — used by the Art-Net driver. Cancels any in-progress fade."""
        self._cancel_fade(bulb.mac)
        if on:
            self._call(lambda c: c.bulbs.turn_on(
                device_mac=bulb.mac, device_model=bulb.model))
        else:
            self._call(lambda c: c.bulbs.turn_off(
                device_mac=bulb.mac, device_model=bulb.model))

    def _apply_brightness(self, bulb, value):
        self._call(lambda c, v=value: c.bulbs.set_brightness(
            device_mac=bulb.mac, device_model=bulb.model, brightness=v))
        self._brightness_state[bulb.mac] = value

    def _apply_color(self, bulb, hexstr):
        self._call(lambda c, h=hexstr: c.bulbs.set_color(
            device_mac=bulb.mac, device_model=bulb.model, color=h))
        self._color_state[bulb.mac] = hexstr

    def _read_is_on(self, bulb):
        if self._simulate or self._client is None:
            return False
        from wyze_sdk.errors import WyzeApiError
        try:
            info = self._client.bulbs.info(device_mac=bulb.mac)
            return bool(getattr(info, "is_on", False))
        except WyzeApiError as err:
            log.warning("Could not read state for %s: %s", bulb.name, err)
            return False

    # --------------------------------------------------------------- fading
    def _cancel_fade(self, mac):
        with self._fade_lock:
            event = self._fades.pop(mac, None)
        if event:
            event.set()

    def _start_fade(self, bulb, kind, target_val=None, target_rgb=None, duration=1.0):
        self._cancel_fade(bulb.mac)
        event = threading.Event()
        with self._fade_lock:
            self._fades[bulb.mac] = event
        thread = threading.Thread(
            target=self._run_fade,
            args=(bulb, kind, target_val, target_rgb, duration, event),
            daemon=True,
        )
        thread.start()

    def _run_fade(self, bulb, kind, target_val, target_rgb, duration, event):
        interval = self._fade_min_interval
        steps = max(1, int(round(duration / interval))) if duration > 0 else 1

        if kind == "brightness":
            start = self._brightness_state.get(bulb.mac, 0)
            for i in range(1, steps + 1):
                if event.is_set():
                    return
                cur = int(round(start + (target_val - start) * i / steps))
                self._apply_brightness(bulb, _clamp(cur, 0, 100))
                if i < steps:
                    time.sleep(interval)

        elif kind == "color":
            sr, sg, sb = hex_to_rgb(self._color_state.get(bulb.mac, "ffffff"))
            tr, tg, tb = target_rgb
            for i in range(1, steps + 1):
                if event.is_set():
                    return
                r = sr + (tr - sr) * i / steps
                g = sg + (tg - sg) * i / steps
                b = sb + (tb - sb) * i / steps
                self._apply_color(bulb, rgb_to_hex(r, g, b))
                if i < steps:
                    time.sleep(interval)

        with self._fade_lock:
            if self._fades.get(bulb.mac) is event:
                self._fades.pop(bulb.mac, None)
