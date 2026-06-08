"""Command-line entry point:  python -m qlab_wyze_bridge"""

import argparse
import logging
import sys

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


def main(argv=None):
    args = build_parser().parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    log = logging.getLogger("qlab_wyze_bridge")

    cfg = load_config(args.config)

    controller = WyzeController(
        credentials=cfg.credentials,
        bulbs_config=cfg.bulbs,
        groups=cfg.groups,
        fade_min_interval=cfg.fade_min_interval,
        simulate=args.simulate,
    )

    try:
        controller.connect()
    except Exception as err:  # noqa: BLE001
        log.error("Failed to connect to Wyze: %s", err)
        return 1

    if args.list_devices:
        print(controller.describe())
        return 0

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
    return 0


if __name__ == "__main__":
    sys.exit(main())
