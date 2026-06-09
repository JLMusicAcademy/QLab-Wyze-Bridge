# QLab → Wyze Bridge

A lightweight Python server that listens for **QLab OSC** messages and
translates them into commands for **Wyze color LED bulbs**, using the
[`wyze-sdk`](https://github.com/shauntarves/wyze-sdk) Python client (the same
Wyze cloud API that powers [ha-wyzeapi](https://github.com/SecKatie/ha-wyzeapi)).

Send an OSC message from a QLab *Network* cue and the bridge turns it into the
right Wyze API call:

```
/wyze/stage_left/color  ff0000        ->  set bulb "stage left" to red
/wyze/stage/fade        0 5.0         ->  fade the "stage" group to 0% over 5 s
/wyze/all/off                         ->  turn every bulb off
```

## Features

- **On / Off / Toggle**
- **Snap color** — instant color change (hex or R G B)
- **Color wheel** — `hsv` control (hue + saturation set the color, value sets brightness)
- **Color fade** — smooth RGB crossfade over a duration
- **Brightness** — snap to a level (0–100%)
- **Intensity fade** — smooth brightness fade over a duration
- **Color temperature** — white-balance in Kelvin (1800–6500 K)
- **Sun Match** and **Away (vacation) mode**
- **Targets:** individual bulbs, named **groups**, or **`all`** at once
- Auto-discovers your bulbs and names them after their Wyze app nickname
- Software fades (the Wyze cloud API has no native fade — the bridge interpolates)
- Auto re-login if the Wyze access token expires
- `--simulate` mode to test the QLab wiring without touching real bulbs

## Why `wyze-sdk` and not ha-wyzeapi directly?

`ha-wyzeapi` is a *Home Assistant* integration — it only runs inside Home
Assistant. It's built on top of the standalone `wyze-sdk` library, which is
what this bridge uses so it can run on its own as a small service next to QLab.

---

## 1. Install

Requires Python 3.8+.

```bash
git clone https://github.com/JLMusicAcademy/QLab-Wyze-Bridge.git
cd QLab-Wyze-Bridge
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

> **If install fails building `blackboxprotobuf`** (a Wyze SDK dependency) with
> an error like `AttributeError: install_layout`, your `setuptools` is too new
> for that legacy package. Pin an older build toolchain first, then retry:
>
> ```bash
> pip install "setuptools<66" wheel
> pip install -r requirements.txt
> ```

## 2. Get your Wyze API key

Wyze now requires an API key for SDK/API access:

1. Go to <https://developer-api-console.wyze.com/>.
2. Sign in with your Wyze account and create an API key.
3. Note the **Key ID** and **API Key**.

> If your account uses an authenticator app for 2FA, you'll also need your
> TOTP secret (`totp_key`). SMS 2FA is not supported by the SDK — switch the
> account to an authenticator app or use API-key auth.

## 3. Configure

```bash
cp config.example.yaml config.yaml
```

You have three ways to supply credentials (pick one):

1. **A `.env` file (recommended)** — copy `.env.example` to `.env` and fill it
   in. The bridge loads it automatically at startup, and `.env` is git-ignored.
   Leave the `${WYZE_*}` placeholders as-is in `config.yaml`; they read from
   the environment that `.env` populates.

   ```bash
   cp .env.example .env
   # then edit .env with your email, password, key_id, api_key
   ```

2. **Directly in `config.yaml`** — replace the `${WYZE_*}` placeholders with
   your actual values.

3. **Real shell environment variables** — `export WYZE_EMAIL=...` etc.

Precedence: a literal value in `config.yaml` wins; otherwise a real shell
variable wins; otherwise the `.env` value is used. Both `config.yaml` and
`.env` are git-ignored so your credentials stay out of the repo.

Confirm the bridge can see your bulbs:

```bash
python -m qlab_wyze_bridge --list-devices
```

This prints each bulb's auto-generated **name** (use this as the OSC target),
along with its MAC and model. If a bulb isn't found, add it manually under
`bulbs:` in the config using the MAC + model.

## 4. Run

```bash
python -m qlab_wyze_bridge
# or, if pip-installed:  qlab-wyze-bridge
```

The bridge listens on UDP `0.0.0.0:9000` by default. Leave it running on a
machine reachable from your QLab Mac (it can be the same Mac).

---

## 5. Point QLab at the bridge

In QLab, add a **Network** patch (Settings → Network) pointing at the bridge:

- **Type:** OSC Message
- **Destination:** the IP of the machine running the bridge (e.g. `127.0.0.1`
  if it's the same Mac, otherwise the bridge machine's LAN IP)
- **Port:** `9000`

Then add **Network cues** whose OSC message uses the addresses below.

---

## OSC command reference

All addresses follow the pattern **`/wyze/<target>/<command> [arguments]`**.

`<target>` is a bulb name (from `--list-devices`), a group name, or `all`.

| Command | Address | Arguments | Example |
|---|---|---|---|
| On | `/wyze/<target>/on` | — | `/wyze/stage_left/on` |
| Off | `/wyze/<target>/off` | — | `/wyze/all/off` |
| Toggle | `/wyze/<target>/toggle` | — | `/wyze/stage_left/toggle` |
| Brightness (snap) | `/wyze/<target>/brightness` | `level` 0–100 | `/wyze/stage_left/brightness 75` |
| Brightness fade | `/wyze/<target>/fade` | `level` 0–100, `seconds` | `/wyze/stage/fade 0 5.0` |
| Color (snap) | `/wyze/<target>/color` | `hex` **or** `r g b` (0–255) | `/wyze/stage_left/color ff8800` |
| Color fade | `/wyze/<target>/colorfade` | `hex seconds` **or** `r g b seconds` | `/wyze/stage/colorfade 0000ff 3.0` |
| Color wheel (HSV) | `/wyze/<target>/hsv` | `hue` 0–360, `sat` 0–100, `val` 0–100 | `/wyze/stage_left/hsv 120 100 80` |
| Color temperature | `/wyze/<target>/colortemp` | `kelvin` 1800–6500 | `/wyze/stage_left/colortemp 2700` |
| Sun Match | `/wyze/<target>/sunmatch` | `0` or `1` | `/wyze/stage_left/sunmatch 1` |
| Away mode | `/wyze/<target>/away` | `0` or `1` | `/wyze/all/away 1` |

Notes:

- **Color (hex)** can be sent with or without a leading `#`, e.g. `ff0000` or
  `#ff0000`. You can also send three integers for R, G, B.
- **HSV** is the easiest mapping for a QLab "color wheel": hue and saturation
  pick the color, and the value (0–100) is applied as brightness.
- **Fades** are interpolated by the bridge. Step rate is controlled by
  `fade.min_interval` in the config (default 0.25 s = 4 updates/sec) to stay
  friendly with Wyze's cloud rate limits. A new command on the same bulb
  cancels any fade already in progress on it.
- A fade with `0` seconds behaves like a snap.

---

## Testing without bulbs

Use simulate mode to verify your QLab cues without sending anything to Wyze:

```bash
python -m qlab_wyze_bridge --simulate -v
```

Every received OSC message is logged with the command it *would* run. Add a
`bulbs:` section in the config so simulate mode has names to resolve, e.g.:

```yaml
bulbs:
  stage_left:  { mac: "AA", model: "WLPA19C" }
  stage_right: { mac: "BB", model: "WLPA19C" }
groups:
  stage: [stage_left, stage_right]
```

---

## Running as a background service (optional)

A minimal `systemd` unit on Linux:

```ini
[Unit]
Description=QLab Wyze Bridge
After=network-online.target

[Service]
WorkingDirectory=/opt/QLab-Wyze-Bridge
ExecStart=/opt/QLab-Wyze-Bridge/.venv/bin/python -m qlab_wyze_bridge -c /opt/QLab-Wyze-Bridge/config.yaml
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

## Troubleshooting

- **No bulbs found:** run `--list-devices`. If empty, double-check credentials
  and that your API key is active. Add bulbs manually under `bulbs:` if needed.
- **Login fails / rate limited:** Wyze rate-limits frequent logins. Wait a few
  minutes between attempts; the bridge logs in once at startup and reuses the
  session.
- **`sunmatch` errors:** Sun Match is only available on certain models (Bulb
  White v1/v2, Bulb Color, Bulb Color BR30, Light Strip / Pro).
- **`SSL: CERTIFICATE_VERIFY_FAILED ... unable to get local issuer
  certificate`:** some Wyze hosts serve an incomplete certificate chain. The
  bridge fixes this by verifying against your OS trust store via the
  `truststore` package (installed from `requirements.txt`), which fetches the
  missing intermediate automatically. If you see this error, make sure
  `truststore` is installed (`pip install truststore`) and you're on Python
  3.10+.
- **QLab sends but nothing happens:** confirm QLab's destination IP/port match
  the bridge, that both are on the same network, and that the OSC address
  starts with `/wyze/`. Run the bridge with `-v` to see incoming messages.

## License

MIT
