import argparse
import datetime
import json
import logging
import sys

from . import operations, transport

_LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"
_DATE_FORMAT = "%H:%M:%S"
logger = logging.getLogger(__name__)


def configure_logging(verbosity: int) -> None:
    """Configure root logging based on requested verbosity."""
    root = logging.getLogger()
    level = _verbosity_to_level(verbosity)

    if root.handlers:
        root.setLevel(level)
        for handler in root.handlers:
            handler.setLevel(level)
        return

    logging.basicConfig(
        level=level,
        format=_LOG_FORMAT,
        datefmt=_DATE_FORMAT,
        handlers=[logging.StreamHandler(sys.stdout)],
    )
    logging.captureWarnings(True)


def _verbosity_to_level(verbosity: int) -> int:
    if verbosity >= 2:
        return logging.DEBUG
    if verbosity == 1:
        return logging.INFO
    return logging.WARNING


def create_parser() -> argparse.ArgumentParser:
    """Build the command-line parser."""
    parser = argparse.ArgumentParser(
        description="Turing Smart Screen CLI Tool - Control your Turing Smart Screen device via USB",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  turing-screen send-image --path sample.png\n"
            "  turing-screen brightness --value 80\n"
            "  turing-screen save --brightness 100 --rotation 0\n"
            "  turing-screen list-storage --type image"
        ),
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Send a command even if this panel's firmware is known not to support it",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="count",
        default=0,
        help="Increase logging verbosity (-vv for debug logging).",
    )

    subparsers = parser.add_subparsers(dest="command", help="Command to execute")
    subparsers.required = True

    # Simple commands with no arguments
    subparsers.add_parser("sync", help="Send a sync command to the device")
    subparsers.add_parser("restart", help="Restart the device")
    subparsers.add_parser("refresh-storage", help="Show SD storage information")
    subparsers.add_parser("clear-image", help="Clear the current image")
    subparsers.add_parser("stop-play", help="Stop active playback")
    info_parser = subparsers.add_parser("info", help="Show firmware version, storage and playback status")
    info_parser.add_argument("--json", action="store_true", help="Print JSON instead of key: value lines")

    # Commands with arguments
    brightness_parser = subparsers.add_parser("brightness", help="Set screen brightness")
    brightness_parser.add_argument(
        "--value",
        type=int,
        required=True,
        choices=range(0, 101),
        metavar="[0-100]",
        help="Brightness value (0–100)",
    )

    frame_rate_parser = subparsers.add_parser("frame-rate", help="Set the playback frame rate")
    frame_rate_parser.add_argument(
        "--value",
        type=int,
        required=True,
        choices=range(1, 61),
        metavar="[1-60]",
        help="Frames per second (send-video uses 25)",
    )

    save_parser = subparsers.add_parser("save", help="Persist device settings")
    save_parser.add_argument(
        "--brightness",
        type=int,
        default=100,
        choices=range(0, 101),
        metavar="[0-100]",
        help="Brightness value (0-100, default: 100)",
    )
    save_parser.add_argument(
        "--startup",
        type=int,
        default=0,
        choices=[0, 1, 2],
        metavar="[0|1|2]",
        help="0 = default, 1 = play image, 2 = play video (default: 0)",
    )
    save_parser.add_argument(
        "--reserved",
        type=int,
        default=0,
        choices=[0],
        metavar="[0]",
        help="Reserved value (default: 0)",
    )
    save_parser.add_argument(
        "--rotation",
        type=int,
        default=0,
        choices=[0, 2],
        metavar="[0|2]",
        help="0 = 0°, 2 = 180° (default: 0)",
    )
    save_parser.add_argument(
        "--sleep",
        type=int,
        default=0,
        choices=range(0, 256),
        metavar="[0-255]",
        help="Sleep timeout (default: 0)",
    )
    save_parser.add_argument(
        "--clock-direction",
        type=int,
        default=0,
        choices=range(0, 4),
        metavar="[0-3]",
        help="Offline clock direction (the vendor app sends it; default: 0)",
    )
    save_parser.add_argument(
        "--offline",
        type=int,
        default=0,
        choices=[0, 1],
        metavar="[0|1]",
        help="0 = Disabled, 1 = Enabled (default: 0)",
    )

    rotate_parser = subparsers.add_parser("rotate", help="Set the panel rotation (13); the panel saves it")
    rotate_parser.add_argument("--value", type=int, required=True, choices=[0, 2], metavar="[0|2]", help="0 or 2 (180°)")

    size_parser = subparsers.add_parser("file-size", help="Size in bytes of a stored file (0 if missing)")
    size_parser.add_argument("--filename", required=True, help=".png or .h264 filename")

    subparsers.add_parser("voltage", help="Supply voltage in mV (0 on firmware that does not report it)")
    subparsers.add_parser("orientation", help="Orientation sensor reading (newer firmware only)")
    subparsers.add_parser("block-size", help="Stream block size the panel asks for (0 = default 202752)")

    clock_parser = subparsers.add_parser("set-clock", help="Set the panel clock (the vendor app does this for the 4.6\")")
    clock_parser.add_argument("--time", help="ISO time, e.g. 2026-11-16T02:05:00 (default: now)")
    clock_parser.add_argument("--mode", type=int, default=0, choices=[0, 1, 2], help="Clock mode byte (default 0)")

    run_parser = subparsers.add_parser("run-state", help="The vendor app's Run all / Stop all flag (49)")
    run_parser.add_argument("state", choices=["on", "off"])

    off_parser = subparsers.add_parser("power-off", help="Halt the panel until its power is cycled (12)")
    off_parser.add_argument("--yes", action="store_true", help="Really do it")

    list_parser = subparsers.add_parser("list-storage", help="List files stored on the device")
    list_parser.add_argument(
        "--type",
        type=str,
        choices=["image", "video"],
        required=True,
        help="Type of files to list: image or video",
    )

    image_parser = subparsers.add_parser("send-image", help="Display an image on the screen")
    image_parser.add_argument(
        "--path",
        type=str,
        required=True,
        help="Path to an image (ideally 480x1920)",
    )
    image_parser.add_argument(
        "--format",
        choices=["png", "jpeg", "auto"],
        default="png",
        help="png (default; split into layers if large), jpeg (one push, quality lowered to fit), "
        "auto (PNG if it fits one push, else JPEG)",
    )
    image_parser.add_argument(
        "--quality", type=int, default=90, choices=range(10, 101), metavar="[10-100]", help="JPEG quality (default 90)"
    )

    parser_video = subparsers.add_parser("send-video", help="Play a video on the screen")
    parser_video.add_argument(
        "--path",
        type=str,
        required=True,
        help="Path to MP4 video file",
    )
    parser_video.add_argument(
        "--loop",
        action="store_true",
        help="Loop the video playback until interrupted",
    )
    _add_brightness_option(parser_video)
    parser_video.add_argument(
        "--rotation",
        type=int,
        default=None,
        choices=[0, 2],
        metavar="[0|2]",
        help="Also set the panel rotation (0 or 2 = 180°); the panel saves it (default: leave it)",
    )

    upload_parser = subparsers.add_parser("upload", help="Upload PNG or MP4 file to device storage")
    upload_parser.add_argument(
        "--path",
        type=str,
        required=True,
        help="Path to .png or .mp4 file",
    )

    delete_parser = subparsers.add_parser("delete", help="Delete a file from device storage")
    delete_parser.add_argument(
        "--filename",
        type=str,
        required=True,
        help=".png or .h264 filename to delete",
    )

    play_parser = subparsers.add_parser("play-select", help="Play a stored file from device storage")
    play_parser.add_argument(
        "--filename",
        type=str,
        required=True,
        help=".png or .h264 filename to play",
    )
    _add_brightness_option(play_parser)

    return parser


def _add_brightness_option(subparser) -> None:
    subparser.add_argument(
        "--brightness",
        type=int,
        default=None,
        choices=range(0, 101),
        metavar="[0-100]",
        help="Also set the brightness (default: leave it as it is)",
    )


def run(argv=None, *, device_factory=transport.find_usb_device) -> int:
    """Run the CLI with the provided arguments."""
    parser = create_parser()
    args = parser.parse_args(argv)
    configure_logging(args.verbose)

    try:
        dev = device_factory()
    except ValueError as exc:
        logger.error("Error: %s", exc)
        return 1
    except KeyboardInterrupt:
        logger.info("Operation cancelled by user")
        return 0
    except Exception as exc:
        logger.error("Unexpected error: %s", exc)
        return 1

    try:
        success = _dispatch_command(dev, args)
    except KeyboardInterrupt:
        logger.info("Operation cancelled by user")
        return 0
    except Exception as exc:
        logger.error("Unexpected error: %s", exc)
        return 1

    return 0 if success else 1


def _print_mapping(mapping: dict, as_json: bool = False) -> None:
    if as_json:
        print(json.dumps(mapping, indent=2))
    else:
        for key, value in mapping.items():
            print(f"{key}: {value}")


def _info(dev, args) -> bool:
    info = operations.read_device_info(dev)
    if info is None:
        return False
    _print_mapping(info, args.json)
    return True


def _refresh_storage(dev, args) -> bool:
    storage = operations.send_refresh_storage_command(dev)
    if storage is None:
        return False
    _print_mapping(storage)
    return True


def _list_storage(dev, args) -> bool:
    path = operations.IMAGE_DIR if args.type == "image" else operations.VIDEO_DIR
    names = operations.send_list_storage_command(dev, path)
    if names is None:
        return False
    for name in names:
        print(name)
    return True


def _upload(dev, args) -> bool:
    if not operations.send_refresh_storage_command(dev):
        return False
    return operations.upload_file(dev, args.path)


def _print_value(value, unit: str = "") -> bool:
    if value is None:
        return False
    print(f"{value}{unit}")
    return True


def _orientation(dev, args) -> bool:
    orientation = operations.read_orientation(dev)
    if orientation is None:
        print("not supported by this firmware")
        return False
    _print_mapping(orientation)
    return True


def _set_clock(dev, args) -> bool:
    when = datetime.datetime.fromisoformat(args.time) if args.time else datetime.datetime.now()
    return operations.set_clock(dev, when, mode=args.mode) is not None


def _power_off(dev, args) -> bool:
    if not args.yes:
        logger.error("power-off halts the panel until its power is cycled; add --yes to do it")
        return False
    return operations.power_off(dev) is not None


# Every subcommand: (handler, send a sync first, firmware feature it needs or None).
# Features and their firmware versions are in operations.FIRMWARE_FEATURES. A test checks this matches the parser.
COMMANDS = {
    "info": (_info, True, None),
    "sync": (lambda dev, a: operations.send_sync_command(dev) is not None, False, None),
    "restart": (lambda dev, a: operations.send_restart_device_command(dev) is not None, True, None),
    "refresh-storage": (_refresh_storage, True, None),
    "clear-image": (lambda dev, a: operations.clear_image(dev) is not None, True, None),
    "stop-play": (lambda dev, a: operations.stop_play(dev), True, None),
    "brightness": (lambda dev, a: operations.send_brightness_command(dev, a.value) is not None, True, None),
    "frame-rate": (lambda dev, a: operations.send_frame_rate_command(dev, a.value) is not None, True, None),
    "rotate": (lambda dev, a: operations.set_rotation(dev, a.value) is not None, True, None),
    "save": (
        lambda dev, a: operations.send_save_settings_command(
            dev,
            brightness=a.brightness,
            startup=a.startup,
            reserved=a.reserved,
            rotation=a.rotation,
            sleep=a.sleep,
            offline=a.offline,
            clock_direction=a.clock_direction,
        )
        is not None,
        True,
        None,
    ),
    "list-storage": (_list_storage, True, None),
    "file-size": (lambda dev, a: _print_value(operations.file_size(dev, a.filename)), True, None),
    "send-image": (lambda dev, a: operations.send_image(dev, a.path, fmt=a.format, quality=a.quality), True, None),
    "send-video": (
        lambda dev, a: operations.send_video(dev, a.path, loop=a.loop, brightness=a.brightness, rotation=a.rotation),
        True,
        None,
    ),
    "upload": (_upload, True, None),
    "delete": (lambda dev, a: operations.delete_file(dev, a.filename), True, None),
    "play-select": (lambda dev, a: operations.play_stored_asset(dev, a.filename, brightness=a.brightness), False, None),
    "voltage": (lambda dev, a: _print_value(operations.read_voltage(dev), " mV"), True, "voltage"),
    "orientation": (_orientation, True, "orientation"),
    "block-size": (lambda dev, a: _print_value(operations.read_block_size(dev)), True, "block_size"),
    "set-clock": (_set_clock, True, "set_clock"),
    "run-state": (lambda dev, a: operations.set_run_state(dev, a.state == "on") is not None, True, "run_state"),
    "power-off": (_power_off, True, None),
}


def _dispatch_command(dev, args) -> bool:
    try:
        handler, sync_first, feature = COMMANDS[args.command]
    except KeyError:
        raise ValueError(f"Unsupported command: {args.command}") from None
    if sync_first:
        operations.delay_sync(dev)
    if feature is not None and not getattr(args, "force", False) and not operations.supports(dev, feature):
        needed = operations.FIRMWARE_FEATURES[feature]
        print(
            f"{args.command}: not supported by firmware {operations.firmware_version(dev)} "
            f"(needs {needed.min_version} or later; {needed.evidence}). Use --force to send it anyway."
        )
        return False
    return bool(handler(dev, args))


def main(argv=None):
    """CLI entry point."""
    sys.exit(run(argv))


if __name__ == "__main__":
    main()
