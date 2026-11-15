"""High-level device operations for the Turing Smart Screen."""

from __future__ import annotations

import io
import logging
import math
import subprocess
import time
from pathlib import Path
from dataclasses import dataclass
from typing import Callable, Optional

from PIL import Image

from . import transport

logger = logging.getLogger(__name__)

build_command_packet_header = transport.build_command_packet_header
encrypt_command_packet = transport.encrypt_command_packet
write_to_device = transport.write_to_device


def delay_sync(dev) -> None:
    """Send a sync command and wait briefly."""
    send_sync_command(dev)
    time.sleep(0.2)


def send_sync_command(dev):
    logger.info("Sending sync command...")
    cmd_packet = build_command_packet_header(10)
    return write_to_device(dev, encrypt_command_packet(cmd_packet))


def send_restart_device_command(dev):
    logger.info("Sending restart command...")
    return write_to_device(dev, encrypt_command_packet(build_command_packet_header(11)))


def send_brightness_command(dev, brightness: int):
    logger.info("Updating brightness...")
    logger.info("  Brightness = %d", brightness)
    cmd_packet = build_command_packet_header(14)
    cmd_packet[8] = brightness
    return write_to_device(dev, encrypt_command_packet(cmd_packet))


def send_frame_rate_command(dev, frame_rate: int):
    logger.info("Updating frame rate...")
    logger.info("  Frame Rate = %d", frame_rate)
    cmd_packet = build_command_packet_header(15)
    cmd_packet[8] = frame_rate
    return write_to_device(dev, encrypt_command_packet(cmd_packet))


def format_bytes(val: int) -> str:
    if val > 1024 * 1024:
        return f"{val / (1024 * 1024):.2f} GB"
    return f"{val / 1024:.2f} MB"


# Chunk sizes for 39 (file write) and 121 (H.264 stream); the vendor app uses 202752 when the
# device does not report a block size (17 is not handled by the 8.8" firmware).
UPLOAD_CHUNK_BYTES = 202752
STREAM_CHUNK_BYTES = 202752

@dataclass(frozen=True)
class Feature:
    min_version: int  # first firmware version (the number after the last "_" of command 10's reply) that has it
    evidence: str


# Behaviour that differs between panel firmware versions: the single list. Commands check it with
# supports(); a feature no known firmware has gets LATEST_KNOWN_FIRMWARE + 1, so it is only tried on newer ones.
LATEST_KNOWN_FIRMWARE = 15
FIRMWARE_FEATURES = {
    # Measured on firmware 0008 and 0015.
    "stored_image_reply": Feature(
        LATEST_KNOWN_FIRMWARE + 1,
        "113 answers only the first time after a restart or upload on 0008 and on 0015 (measured)",
    ),
    "chip_id": Feature(15, "240 returns a 16-byte chip id on 0015, zeros on 0008 (measured)"),
    "voltage": Feature(LATEST_KNOWN_FIRMWARE + 1, "50 answers zeros on 0008 and 0015"),
    "orientation": Feature(LATEST_KNOWN_FIRMWARE + 1, "60 answers zeros on 0008 and 0015"),
    "run_state": Feature(LATEST_KNOWN_FIRMWARE + 1, "49 is a plain ack on 0008 and 0015"),
    "block_size": Feature(LATEST_KNOWN_FIRMWARE + 1, "17 answers zeros on 0008 and 0015"),
    "set_clock": Feature(LATEST_KNOWN_FIRMWARE + 1, "51 is a plain ack on 0008 and 0015"),
}

_FIRMWARE_CACHE: dict = {}


def firmware_version(dev) -> Optional[int]:
    """Firmware version number from command 10 (e.g. 8 for turzx_0001_0008), cached per device.

    None if the panel does not answer or the version string has no number.
    """
    key = id(dev)
    if key not in _FIRMWARE_CACHE:
        response = write_to_device(dev, encrypt_command_packet(build_command_packet_header(10)))
        version = None
        if response is not None and len(response) >= 40 and response[0] == 10:
            text = bytes(response[8:40]).split(b"\0", 1)[0].decode("utf-8", errors="replace")
            last = text.rsplit("_", 1)[-1]
            version = int(last) if last.isdigit() else None
        _FIRMWARE_CACHE[key] = version
    return _FIRMWARE_CACHE[key]


def supports(dev, feature: str) -> bool:
    """Whether this panel's firmware has `feature` (FIRMWARE_FEATURES). Unknown versions are assumed to."""
    version = firmware_version(dev)
    return version is None or version >= FIRMWARE_FEATURES[feature].min_version


# One image push (101 / 102) must stay under about 1 MiB or the panel times out.
MAX_IMAGE_BYTES = 1_000_000

IMAGE_DIR = "/tmp/sdcard/mmcblk0p1/img/"
VIDEO_DIR = "/tmp/sdcard/mmcblk0p1/video/"


def device_path_for(filename: str) -> Optional[str]:
    """Path on the device's SD card for a stored .png / .h264, or None if the name is not acceptable.

    Only a plain file name is accepted: no directories, no "..".
    """
    if not filename or filename != Path(filename).name or filename in (".", ".."):
        logger.error("Error: give a plain file name (no directories): %r", filename)
        return None
    ext = Path(filename).suffix.lower()
    if ext == ".png":
        return IMAGE_DIR + filename
    if ext == ".h264":
        return VIDEO_DIR + filename
    logger.error("Error: Unsupported file type. Only .png and .h264 are allowed.")
    return None


def delete_file(dev, filename: str) -> bool:
    device_path = device_path_for(filename)
    if device_path is None:
        return False
    logger.info("Delete: %s", device_path)

    if not _delete_command(dev, device_path):
        logger.error("Failed to delete remote file.")
        return False

    logger.info("Delete completed successfully.")
    return True


def play_file(dev, filename: str, play_command_func: Callable) -> bool:
    device_path = device_path_for(filename)
    if device_path is None:
        return False
    logger.info("Play: %s", device_path)

    if not play_command_func(dev, device_path):
        logger.error("Failed to play %s", device_path)
        return False

    logger.info("Play command sent successfully.")
    return True


def play_file2(dev, filename: str) -> bool:
    """Play a stored H.264 file (110)."""
    return play_file(dev, filename, _play2_command)


def play_file3(dev, filename: str) -> bool:
    """Show a stored PNG/JPG (113).

    Firmware without "stored_image_reply" often does not answer; that is not counted as a failure.
    """
    if not supports(dev, "stored_image_reply"):
        device_path = device_path_for(filename)
        if device_path is None:
            return False
        logger.info("Play: %s", device_path)
        response = write_to_device(dev, encrypt_command_packet(_path_packet(113, device_path)))
        if response is None:
            logger.warning("Firmware %s does not confirm showing a stored image; check the screen.", firmware_version(dev))
        return True
    return play_file(dev, filename, _play3_command)


def upload_file(dev, file_path: str) -> bool:
    local_path = Path(file_path)
    if not local_path.exists():
        logger.error("Error: File does not exist: %s", file_path)
        return False

    ext = local_path.suffix.lower()
    if ext == ".png":
        device_path = IMAGE_DIR + local_path.name
        logger.info("Uploading PNG: %s → %s", file_path, device_path)
    elif ext == ".mp4":
        h264_path = extract_h264_from_mp4(file_path)
        device_path = VIDEO_DIR + h264_path.name
        local_path = h264_path  # Update local path to .h264
        logger.info("Uploading MP4 as H264: %s → %s", local_path, device_path)
    else:
        logger.error("Error: Unsupported file type. Only .png and .mp4 are allowed.")
        return False

    if not _open_file_command(dev, device_path):
        logger.error("Failed to open remote file for writing.")
        return False

    if not _write_file_command(dev, str(local_path)):
        logger.error("Failed to write file data.")
        return False

    logger.info("Upload completed successfully.")
    return True


def send_list_storage_command(dev, path: str) -> Optional[list]:
    """File names in a directory on the SD card (99), or None if the panel did not answer.

    The same request is repeated up to 20 times: the panel answers with empty (all-NUL) replies
    until the listing is ready, then "result:dir:file:<name>/<name>/..." (NUL padded).
    """
    logger.info("Listing storage for path: %s", path)

    path_bytes = path.encode("ascii")
    packet = build_command_packet_header(99)
    packet[8:12] = len(path_bytes).to_bytes(4, "big")
    packet[16 : 16 + len(path_bytes)] = path_bytes

    received = bytearray()
    for i in range(20):
        response = write_to_device(dev, encrypt_command_packet(packet))
        if not response:
            if i > 0:
                logger.warning("No response in chunk %d", i)
            break
        if not any(response):
            if received:
                break  # data done; the rest is NUL padding
            continue  # the panel answers empty until the listing is ready (9th reply on firmware 0008)
        received += response
    else:
        logger.warning("Listing may be truncated (20 replies, all with data)")

    if not received:
        logger.error("No data received.")
        return None

    text = received.replace(b"\0", b"").decode("utf-8", errors="ignore")
    if "file:" not in text:
        logger.info("No files found or format unexpected: %r", text[:80])
        return []
    names = text.split("file:", 1)[1].split("/")
    return [n.strip() for n in names if n.strip()]


def send_refresh_storage_command(dev) -> Optional[dict]:
    """SD card size in KB (100): total, used, free; None if the panel did not answer."""
    logger.info("Refreshing storage information...")
    response = write_to_device(dev, encrypt_command_packet(build_command_packet_header(100)))

    if not response or len(response) < 20:
        logger.error("Invalid or incomplete response from device")
        return None

    total, used, free = (int.from_bytes(response[o : o + 4], byteorder="little") for o in (8, 12, 16))
    logger.info("  Card Total: %s  Used: %s  Free: %s", format_bytes(total), format_bytes(used), format_bytes(free))
    return {"total_kb": total, "used_kb": used, "free_kb": free}


def _query(dev, command_id: int) -> Optional[bytes]:
    response = write_to_device(dev, encrypt_command_packet(build_command_packet_header(command_id)))
    if response is None or len(response) < 24 or response[0] != command_id:
        return None
    return bytes(response)


def read_device_info(dev) -> Optional[dict]:
    """Read-only status: firmware (10), storage (100), stored-video flag (112), stream queue (122), chip id (240).

    Returns None if the panel does not answer the version query.
    """
    version = _query(dev, 10)
    if version is None:
        logger.error("Panel did not answer the version query (10)")
        return None
    firmware = version[8:40].split(b"\0", 1)[0].decode("utf-8", errors="replace")
    info: dict = {"firmware": firmware}
    last = firmware.rsplit("_", 1)[-1]
    if last.isdigit():
        info["firmware_version"] = int(last)
    _FIRMWARE_CACHE[id(dev)] = info.get("firmware_version")

    storage = _query(dev, 100)
    if storage is not None:
        total, used, free = (int.from_bytes(storage[o : o + 4], "little") for o in (8, 12, 16))
        info.update(storage_total_kb=total, storage_used_kb=used, storage_free_kb=free)
    playing = _query(dev, 112)
    if playing is not None:
        info["stored_video_playing"] = playing[8] != 0
    queue = _query(dev, 122)
    if queue is not None:
        info["stream_queue"] = queue[8]
    if supports(dev, "chip_id"):
        chip = _query(dev, 240)
        if chip is not None and any(chip[8:24]):
            info["chip_id"] = chip[8:24].hex()
    return info


ORIENTATIONS = ("portrait-0", "landscape-90", "portrait-180", "landscape-270")


def set_rotation(dev, rotation: int):
    """13: set the panel rotation; the panel saves it. 0 or 2 (180°) on the 8.8"."""
    packet = build_command_packet_header(13)
    packet[8] = rotation
    return write_to_device(dev, encrypt_command_packet(packet))


def _path_packet(command_id: int, device_path: str) -> bytearray:
    path_bytes = device_path.encode("ascii")
    packet = build_command_packet_header(command_id)
    packet[8:12] = len(path_bytes).to_bytes(4, "big")
    packet[16 : 16 + len(path_bytes)] = path_bytes
    return packet


def file_size(dev, filename: str) -> Optional[int]:
    """98: size in bytes of a stored .png / .h264 (0 if it does not exist)."""
    device_path = device_path_for(filename)
    if device_path is None:
        return None
    response = write_to_device(dev, encrypt_command_packet(_path_packet(98, device_path)))
    if response is None or len(response) < 12:
        logger.error("Panel did not answer the file size query (98)")
        return None
    return int.from_bytes(response[8:12], "little")


def read_voltage(dev) -> Optional[int]:
    """50: supply voltage in mV (0 on firmware that does not report it)."""
    response = _query(dev, 50)
    return None if response is None else int.from_bytes(response[8:12], "big")


def read_orientation(dev) -> Optional[dict]:
    """60: orientation sensor; None when the firmware has no sensor support (it answers zeros).

    Like the vendor app's first probe, a reading of 0 (portrait-0) is treated as "no sensor".
    """
    response = write_to_device(dev, encrypt_command_packet(build_command_packet_header(60)), 250)
    if response is None or len(response) < 10 or response[0] != 60 or response[1] != 200:
        return None
    # Vendor app (first probe): [9] must be non-zero; firmware without a sensor answers zeros.
    if response[8] > 3 or response[9] > 3 or response[9] == 0:
        return None
    return {"sensor": response[8], "orientation": response[9], "name": ORIENTATIONS[response[9]]}


def read_block_size(dev) -> Optional[int]:
    """17: stream block size the panel wants; 0 means use the default (202752)."""
    response = _query(dev, 17)
    return None if response is None else int.from_bytes(response[8:12], "big")


def set_clock(dev, when, mode: int = 0):
    """51: set the panel clock: [8..9] year (big-endian), month, day, hour, minute, second, [15] mode 0/1/2.

    The vendor app sends it only to the 4.6" model; the 8.8" firmware acknowledges it.
    """
    packet = build_command_packet_header(51)
    packet[8:10] = when.year.to_bytes(2, "big")
    packet[10:15] = bytes([when.month, when.day, when.hour, when.minute, when.second])
    packet[15] = mode
    return write_to_device(dev, encrypt_command_packet(packet))


def set_run_state(dev, running: bool):
    """49: the vendor app's "Run all" / "Stop all" (multi-device page); [8] = 1 or 0."""
    packet = build_command_packet_header(49)
    packet[8] = 1 if running else 0
    return write_to_device(dev, encrypt_command_packet(packet))


def power_off(dev):
    """12: halt the panel (the vendor app sends it on exit). It stays off until power is cycled."""
    return write_to_device(dev, encrypt_command_packet(build_command_packet_header(12)))


def send_save_settings_command(
    dev,
    brightness: int = 0,
    startup: int = 0,
    reserved: int = 0,
    rotation: int = 0,
    sleep: int = 0,
    offline: int = 0,
    clock_direction: int = 0,
):
    logger.info("Saving device settings...")
    logger.info("  Brightness:     %d", brightness)
    logger.info("  Startup Mode:   %d", startup)
    logger.info("  Reserved:       %d", reserved)
    logger.info("  Rotation:       %d", rotation)
    logger.info("  Sleep Timeout:  %d", sleep)
    logger.info("  Offline Mode:   %d", offline)
    logger.info("  Clock Direction:%d", clock_direction)

    cmd_packet = build_command_packet_header(125)
    cmd_packet[8] = brightness
    cmd_packet[9] = startup
    cmd_packet[10] = reserved
    cmd_packet[11] = rotation
    cmd_packet[12] = sleep
    cmd_packet[13] = offline
    cmd_packet[14] = clock_direction  # the vendor app adds this; firmware 0015 stores only [8..13]

    return write_to_device(dev, encrypt_command_packet(cmd_packet))


def stop_play(dev) -> bool:
    logger.info("Stopping playback (phase 1)")
    first = write_to_device(dev, encrypt_command_packet(build_command_packet_header(111)))

    logger.info("Stopping playback (phase 2)")
    second = write_to_device(dev, encrypt_command_packet(build_command_packet_header(114)))

    return first is not None and second is not None


def send_image(
    dev,
    image_path,
    max_chunk_bytes: int = 524288,
    fmt: str = "png",
    quality: int = 90,
    max_bytes: int = MAX_IMAGE_BYTES,
) -> bool:
    """Show an image now.

    fmt "png": one PNG (102), split into layers if it is over max_chunk_bytes.
    fmt "jpeg": one JPEG (101), quality lowered until it fits max_bytes.
    fmt "auto": PNG if it fits one push, otherwise JPEG.
    """
    try:
        with Image.open(image_path) as img:
            img.load()
            if img.size != (480, 1920):
                logger.warning("Image resolution is %dx%d, not 480x1920 (device screen resolution).", *img.size)
            if fmt == "png":
                return _send_png_layers(dev, img.convert("RGBA"), max_chunk_bytes)
            if fmt == "auto":
                png = _encode_png(img.convert("RGBA"))
                if len(png) <= min(max_chunk_bytes, max_bytes):
                    return _send_png_bytes(dev, png) is not None
            if fmt in ("auto", "jpeg"):
                jpeg = encode_jpeg_under(img, max_bytes, quality)
                return send_jpeg_bytes(dev, jpeg) is not None
            logger.error("Unknown image format: %s", fmt)
            return False
    except Exception as exc:
        logger.error("Error sending image: %s", exc)
        return False


def encode_jpeg_under(image: Image.Image, max_bytes: int, quality: int = 90) -> bytes:
    """Baseline JPEG of `image` (alpha dropped) under max_bytes, lowering quality as needed."""
    rgb = image.convert("RGB")
    for q in range(quality, 9, -5):
        buf = io.BytesIO()
        rgb.save(buf, format="JPEG", quality=q, optimize=False, progressive=False)
        if buf.tell() <= max_bytes:
            if q != quality:
                logger.info("JPEG quality lowered to %d to fit %d bytes", q, max_bytes)
            return buf.getvalue()
    raise RuntimeError(f"Cannot fit the image as a JPEG under {max_bytes} bytes")


def send_jpeg_bytes(dev, jpeg_data: bytes):
    """Show a JPEG now (101): [8..11] length, data after the header."""
    cmd_packet = build_command_packet_header(101)
    cmd_packet[8:12] = len(jpeg_data).to_bytes(4, "big")
    logger.info("→ Transmitting JPEG - %d bytes", len(jpeg_data))
    return write_to_device(dev, encrypt_command_packet(cmd_packet) + jpeg_data)


def _send_png_layers(dev, img, max_chunk_bytes: int) -> bool:
    width, height = img.size
    total_size = len(_encode_png(img))
    num_layers = math.ceil(total_size / max_chunk_bytes)
    logger.info("Image size: %d bytes → split into %d layers", total_size, num_layers)

    h = height // num_layers
    results = []
    for i in range(num_layers):
        y_start = max(0, height - (i + 1) * h)
        visible_part = img.crop((0, y_start, width, height - h * i))
        canvas_height = height - i * h
        layer_img = Image.new("RGBA", (width, canvas_height), (0, 0, 0, 0))
        layer_img.paste(visible_part, (0, y_start))
        label = f"layer_{i + 1} ({width}x{canvas_height}) shows Y={y_start}-{height - h * i}"
        logger.info("Sending %s...", label)
        results.append(_send_png_bytes(dev, _encode_png(layer_img), part=label))
    return all(r is not None for r in results)


def clear_image(dev):
    img_data = bytearray(
        [
            0x89,
            0x50,
            0x4E,
            0x47,
            0x0D,
            0x0A,
            0x1A,
            0x0A,
            0x00,
            0x00,
            0x00,
            0x0D,
            0x49,
            0x48,
            0x44,
            0x52,
            0x00,
            0x00,
            0x01,
            0xE0,
            0x00,
            0x00,
            0x07,
            0x80,
            0x08,
            0x06,
            0x00,
            0x00,
            0x00,
            0x16,
            0xF0,
            0x84,
            0xF5,
            0x00,
            0x00,
            0x00,
            0x01,
            0x73,
            0x52,
            0x47,
            0x42,
            0x00,
            0xAE,
            0xCE,
            0x1C,
            0xE9,
            0x00,
            0x00,
            0x00,
            0x04,
            0x67,
            0x41,
            0x4D,
            0x41,
            0x00,
            0x00,
            0xB1,
            0x8F,
            0x0B,
            0xFC,
            0x61,
            0x05,
            0x00,
            0x00,
            0x00,
            0x09,
            0x70,
            0x48,
            0x59,
            0x73,
            0x00,
            0x00,
            0x0E,
            0xC3,
            0x00,
            0x00,
            0x0E,
            0xC3,
            0x01,
            0xC7,
            0x6F,
            0xA8,
            0x64,
            0x00,
            0x00,
            0x0E,
            0x0C,
            0x49,
            0x44,
            0x41,
            0x54,
            0x78,
            0x5E,
            0xED,
            0xC1,
            0x01,
            0x0D,
            0x00,
            0x00,
            0x00,
            0xC2,
            0xA0,
            0xF7,
            0x4F,
            0x6D,
            0x0F,
            0x07,
            0x14,
            0x00,
            0x00,
            0x00,
            0x00,
        ]
        + [0x00] * 3568
        + [
            0x00,
            0xF0,
            0x66,
            0x4A,
            0xC8,
            0x00,
            0x01,
            0x11,
            0x9D,
            0x82,
            0x0A,
            0x00,
            0x00,
            0x00,
            0x00,
            0x49,
            0x45,
            0x4E,
            0x44,
            0xAE,
            0x42,
            0x60,
            0x82,
        ]
    )
    img_size = len(img_data)
    logger.info("Clearing panel image - %d bytes", img_size)

    cmd_packet = build_command_packet_header(102)
    cmd_packet[8] = (img_size >> 24) & 0xFF
    cmd_packet[9] = (img_size >> 16) & 0xFF
    cmd_packet[10] = (img_size >> 8) & 0xFF
    cmd_packet[11] = img_size & 0xFF

    full_payload = encrypt_command_packet(cmd_packet) + img_data
    return write_to_device(dev, full_payload)


def wait_for_stream(dev, max_queued: int, timeout: float = 10.0) -> bool:
    """Poll the stream status (122) until the device reports at most max_queued buffered chunks.

    Returns False if it is still above that after `timeout` seconds (or stops answering).
    """
    deadline = time.monotonic() + timeout
    while True:
        time.sleep(0.05)
        response = write_to_device(dev, encrypt_command_packet(build_command_packet_header(122)))
        if response is None or len(response) < 9:
            logger.error("Device did not report its stream status")
            return False
        if response[8] <= max_queued:
            return True
        if time.monotonic() >= deadline:
            logger.error("Device still has %d chunks queued after %.1f s", response[8], timeout)
            return False


def extract_h264_from_mp4(mp4_path: str) -> Path:
    input_path = Path(mp4_path)
    if not input_path.exists():
        raise FileNotFoundError(f"Input file not found: {input_path}")

    output_path = input_path.with_name(input_path.name + ".h264")

    if output_path.exists():
        logger.info("%s already exists. Skipping extraction.", output_path.name)
        return output_path

    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(input_path),
        "-c:v",
        "copy",
        "-bsf:v",
        "h264_mp4toannexb",
        "-an",
        "-f",
        "h264",
        str(output_path),
    ]

    logger.info("Extracting H.264 from %s...", input_path.name)
    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True)
        logger.info("Done. Saved as %s", output_path.name)
        return output_path
    except subprocess.CalledProcessError as exc:
        logger.error("FFmpeg error: %s\nOutput: %s", exc, exc.stderr)
        raise


def send_video(
    dev, video_path, loop: bool = False, brightness: Optional[int] = None, rotation: Optional[int] = None
) -> bool:
    try:
        output_path = extract_h264_from_mp4(video_path)

        write_to_device(dev, encrypt_command_packet(build_command_packet_header(111)))
        write_to_device(dev, encrypt_command_packet(build_command_packet_header(112)))
        if rotation is not None:
            # 13 sets the rotation and the panel saves it, so only send it when asked.
            rotate = build_command_packet_header(13)
            rotate[8] = rotation
            write_to_device(dev, encrypt_command_packet(rotate))

        if brightness is not None:
            send_brightness_command(dev, brightness)
        clear_image(dev)
        send_frame_rate_command(dev, 25)

        logger.info("Streaming video data...")

        try:
            while True:
                file_size_bytes = Path(output_path).stat().st_size
                with open(output_path, "rb") as fh:
                    chunk_count = 0
                    while True:
                        data = fh.read(STREAM_CHUNK_BYTES)
                        chunk_size = len(data)
                        if not data:
                            break

                        chunk_count += 1
                        if chunk_count % 10 == 0:
                            logger.info("Sending chunk #%d (%d bytes)", chunk_count, chunk_size)

                        cmd_packet = build_command_packet_header(121)
                        cmd_packet[8:12] = chunk_size.to_bytes(4, "big")
                        cmd_packet[12] = 1 if fh.tell() >= file_size_bytes else 0  # last chunk, as the vendor app

                        full_payload = encrypt_command_packet(cmd_packet) + data
                        response = write_to_device(dev, full_payload)
                        time.sleep(0.03)

                        # reply[8] = chunks queued on the device; like the vendor app, wait while it is over 3
                        if response is None or len(response) < 9 or response[8] > 3:
                            if not wait_for_stream(dev, 2):
                                return False

                    logger.info("Video sent successfully (%d chunks)", chunk_count)

                if not loop:
                    break
                logger.info("Looping video...")

        except KeyboardInterrupt:
            logger.info("\nLoop interrupted by user. Sending reset...")

        write_to_device(dev, encrypt_command_packet(build_command_packet_header(123)))
        return True

    except Exception as exc:
        logger.error("Error sending video: %s", exc)
        return False


def play_stored_asset(dev, filename: str, brightness: Optional[int] = None) -> bool:
    if device_path_for(filename) is None:
        return False
    ext = Path(filename).suffix.lower()

    delay_sync(dev)
    stop_play(dev)
    if brightness is not None:
        send_brightness_command(dev, brightness)

    cmd_packet = build_command_packet_header(111)
    write_to_device(dev, encrypt_command_packet(cmd_packet))
    cmd_packet = build_command_packet_header(112)
    write_to_device(dev, encrypt_command_packet(cmd_packet))
    clear_image(dev)

    if ext == ".h264":
        return play_file2(dev, filename)
    if ext == ".png":
        return play_file3(dev, filename)

    logger.error("Error: Unsupported file type. Only .png and .h264 are allowed.")
    return False


def _encode_png(image: Image.Image) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()


def _send_png_bytes(dev, img_data, part: Optional[str] = None):
    img_size = len(img_data)
    cmd_packet = build_command_packet_header(102)
    cmd_packet[8] = (img_size >> 24) & 0xFF
    cmd_packet[9] = (img_size >> 16) & 0xFF
    cmd_packet[10] = (img_size >> 8) & 0xFF
    cmd_packet[11] = img_size & 0xFF
    logger.info("→ Transmitting [%s] - %d bytes", part or "image", img_size)
    full_payload = encrypt_command_packet(cmd_packet) + img_data
    return write_to_device(dev, full_payload)


def _open_file_command(dev, path: str):
    logger.info("Opening remote file: %s", path)

    path_bytes = path.encode("ascii")
    length = len(path_bytes)

    packet = build_command_packet_header(38)

    packet[8] = (length >> 24) & 0xFF
    packet[9] = (length >> 16) & 0xFF
    packet[10] = (length >> 8) & 0xFF
    packet[11] = length & 0xFF
    packet[12:16] = b"\x00\x00\x00\x00"
    packet[16 : 16 + length] = path_bytes

    return write_to_device(dev, encrypt_command_packet(packet))


def _write_file_command(dev, file_path: str) -> bool:
    logger.info("Writing remote file from: %s", file_path)

    try:
        total = Path(file_path).stat().st_size
        with open(file_path, "rb") as fh:
            chunk_index = 0
            sent = 0
            while True:
                data_chunk = fh.read(UPLOAD_CHUNK_BYTES)
                if not data_chunk:
                    break

                chunk_size = len(data_chunk)
                chunk_index += 1
                sent += chunk_size
                logger.debug("Chunk %d size: %d bytes", chunk_index, chunk_size)

                # [8..11] bytes to receive, [12..15] bytes to write to the file, [16] = 1 closes it.
                cmd_packet = build_command_packet_header(39)
                cmd_packet[8:12] = chunk_size.to_bytes(4, "big")
                cmd_packet[12:16] = chunk_size.to_bytes(4, "big")
                cmd_packet[16] = 1 if sent >= total else 0

                response = write_to_device(dev, encrypt_command_packet(cmd_packet) + data_chunk)
                if response is None:
                    logger.error("Write command failed at chunk %d", chunk_index)
                    return False

        logger.info("File write completed successfully (%d chunks).", chunk_index)
        return True
    except FileNotFoundError:
        logger.error("File not found: %s", file_path)
        return False
    except Exception as exc:
        logger.error("Error writing file: %s", exc)
        return False


def _delete_command(dev, file_path: str):
    logger.info("Deleting remote file: %s", file_path)

    path_bytes = file_path.encode("ascii")
    length = len(path_bytes)

    packet = build_command_packet_header(42)
    packet[8] = (length >> 24) & 0xFF
    packet[9] = (length >> 16) & 0xFF
    packet[10] = (length >> 8) & 0xFF
    packet[11] = length & 0xFF
    packet[12:16] = b"\x00\x00\x00\x00"
    packet[16 : 16 + length] = path_bytes

    return write_to_device(dev, encrypt_command_packet(packet))


def _play2_command(dev, file_path: str):
    logger.info("Requesting alternate playback for: %s", file_path)

    path_bytes = file_path.encode("ascii")
    length = len(path_bytes)

    packet = build_command_packet_header(110)

    packet[8] = (length >> 24) & 0xFF
    packet[9] = (length >> 16) & 0xFF
    packet[10] = (length >> 8) & 0xFF
    packet[11] = length & 0xFF
    packet[12:16] = b"\x00\x00\x00\x00"
    packet[16 : 16 + length] = path_bytes

    return write_to_device(dev, encrypt_command_packet(packet))


def _play3_command(dev, file_path: str):
    logger.info("Requesting image playback for: %s", file_path)

    path_bytes = file_path.encode("ascii")
    length = len(path_bytes)

    packet = build_command_packet_header(113)

    packet[8] = (length >> 24) & 0xFF
    packet[9] = (length >> 16) & 0xFF
    packet[10] = (length >> 8) & 0xFF
    packet[11] = length & 0xFF
    packet[12:16] = b"\x00\x00\x00\x00"
    packet[16 : 16 + length] = path_bytes

    return write_to_device(dev, encrypt_command_packet(packet))
