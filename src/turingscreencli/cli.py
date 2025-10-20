import argparse
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

    # Commands with arguments
    brightness_parser = subparsers.add_parser("brightness", help="Set screen brightness")
    brightness_parser.add_argument(
        "--value",
        type=int,
        required=True,
        choices=range(0, 103),
        metavar="[0-102]",
        help="Brightness value (0–102)",
    )

    save_parser = subparsers.add_parser("save", help="Persist device settings")
    save_parser.add_argument(
        "--brightness",
        type=int,
        default=102,
        choices=range(0, 103),
        metavar="[0-102]",
        help="Brightness value (0-102, default: 102)",
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
        "--offline",
        type=int,
        default=0,
        choices=[0, 1],
        metavar="[0|1]",
        help="0 = Disabled, 1 = Enabled (default: 0)",
    )

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
        help="Path to PNG image (ideally 480x1920)",
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

    return parser


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


def _dispatch_command(dev, args) -> bool:
    command = args.command

    if command == "sync":
        return operations.send_sync_command(dev) is not None
    if command == "restart":
        operations.delay_sync(dev)
        return operations.send_restart_device_command(dev) is not None
    if command == "refresh-storage":
        operations.delay_sync(dev)
        operations.send_refresh_storage_command(dev)
        return True
    if command == "brightness":
        operations.delay_sync(dev)
        return operations.send_brightness_command(dev, args.value) is not None
    if command == "save":
        operations.delay_sync(dev)
        response = operations.send_save_settings_command(
            dev,
            brightness=args.brightness,
            startup=args.startup,
            reserved=args.reserved,
            rotation=args.rotation,
            sleep=args.sleep,
            offline=args.offline,
        )
        return response is not None
    if command == "list-storage":
        operations.delay_sync(dev)
        path = "/tmp/sdcard/mmcblk0p1/img/" if args.type == "image" else "/tmp/sdcard/mmcblk0p1/video/"
        operations.send_list_storage_command(dev, path)
        return True
    if command == "clear-image":
        operations.delay_sync(dev)
        return operations.clear_image(dev) is not None
    if command == "send-image":
        operations.delay_sync(dev)
        return operations.send_image(dev, args.path)
    if command == "send-video":
        operations.delay_sync(dev)
        return operations.send_video(dev, args.path, loop=args.loop)
    if command == "upload":
        operations.delay_sync(dev)
        operations.send_refresh_storage_command(dev)
        return operations.upload_file(dev, args.path)
    if command == "delete":
        operations.delay_sync(dev)
        return operations.delete_file(dev, args.filename)
    if command == "stop-play":
        operations.delay_sync(dev)
        return operations.stop_play(dev)
    if command == "play-select":
        return operations.play_stored_asset(dev, args.filename)

    raise ValueError(f"Unsupported command: {command}")


def main(argv=None):
    """CLI entry point."""
    sys.exit(run(argv))


if __name__ == "__main__":
    main()
