"""Command-line entry point:  python -m qlab_wyze_bridge"""

import argparse
import logging
import sys
import time

from .config import load_config
from .osc_server import OscBridge
from .wyze_controller import WyzeController


def build_parser():
    p = argparse.ArgumentParser(
        prog="qlab_wyze_bridge",
        description="Bridge QLab OSC commands to Wyze color bulbs.")
    p.add_argument("-c", "--config", default="config.yaml",
                   help="Path to YAML config (default: config.yaml).")
    p.add_argument("--host", help="Override the OSC listen host.")
    p.add_argument("--port", type=int, help="Override the OSC listen port.")
    p.add_argument("--list-devices", action="store_true",
                   help="Log in, print discovered bulbs/MACs, and exit.")
    p.add_argument("--simulate", action="store_true",
                   help="Run without connecting to Wyze; log commands only "
                        "(useful for testing the OSC wiring with QLab).")
    p.add_argument("-v", "--verbose", action="store_true",
                   help="Enable debug logging.")
    return p


def _enable_system_trust_store(log):
    """Verify TLS against the OS trust store instead of only the certifi roots.

    Some Wyze API hosts (e.g. api.wyzecam.com) serve an incomplete certificate
    chain — they omit the intermediate CA. The bundled certifi roots then fail
    with "unable to get local issuer certificate". The macOS and Windows
    verifiers fetch the missing intermediate automatically (AIA), so routing
    verification through the system trust store fixes it. No-op if truststore
    isn't installed (e.g. Python < 3.10), falling back to certifi.
    """
    try:
        import truststore
        truststore.inject_into_ssl()
        log.debug("TLS verification using the system trust store (truststore).")
    except ImportError:
        log.debug("truststore unavailable; using certifi for TLS verification.")
    except Exception as err:  # noqa: BLE001 - never block startup on this
        log.debug("Could not enable system trust store: %s", err)


def _connect_with_retry(controller, log, max_attempts):
    """Connect to Wyze, retrying with exponential backoff on failure.

    Returns True once connected, or False after exhausting attempts (the
    launchd KeepAlive will then restart the process and we try again).
    """
    delay = 2
    for attempt in range(1, max_attempts + 1):
        try:
            controller.connect()
            return True
        except Exception as err:  # noqa: BLE001
            if attempt >= max_attempts:
                log.error("Failed to connect to Wyze after %d attempt(s): %s",
                          attempt, err)
                return False
            log.warning("Connect attempt %d/%d failed (%s); retrying in %ds.",
                        attempt, max_attempts, err, delay)
            time.sleep(delay)
            delay = min(delay * 2, 30)
    return False


def main(argv=None):
    args = build_parser().parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    log = logging.getLogger("qlab_wyze_bridge")

    _enable_system_trust_store(log)

    cfg = load_config(args.config)

    controller = WyzeController(
        credentials=cfg.credentials,
        bulbs_config=cfg.bulbs,
        groups=cfg.groups,
        fade_min_interval=cfg.fade_min_interval,
        simulate=args.simulate,
    )

    # Retry the connection with backoff. At boot (as a system service) the
    # network may not be ready yet, and Wyze's cloud can hiccup — neither
    # should bring the bridge down. Fail fast for the interactive --list-devices.
    max_attempts = 1 if args.list_devices else 8
    if not _connect_with_retry(controller, log, max_attempts):
        return 1

    if args.list_devices:
        print(controller.describe())
        return 0

    # Optional Art-Net listener so QLab can drive the bulbs as DMX fixtures.
    artnet_server = None
    if cfg.artnet.get("enabled"):
        from .artnet_server import ArtNetServer
        artnet_server = ArtNetServer(controller, cfg.artnet,
                                     fade_min_interval=cfg.fade_min_interval)
        artnet_server.start()

    bridge = OscBridge(
        controller,
        host=args.host or cfg.osc_host,
        port=args.port or cfg.osc_port,
        prefix=cfg.osc_prefix,
    )

    try:
        bridge.serve_forever()
    except KeyboardInterrupt:
        log.info("Shutting down.")
        bridge.shutdown()
        if artnet_server is not None:
            artnet_server.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
