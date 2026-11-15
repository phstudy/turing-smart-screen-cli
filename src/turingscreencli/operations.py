"""High-level device operations for the Turing Smart Screen 8.8" (USB 1cbe:0088).

Packet layouts follow the vendor app; reply checks follow what the
panel actually answers (measured on firmware 0008 and 0015).
"""

from __future__ import annotations

import base64
import io
import json
import logging
import math
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from PIL import Image

from . import transport

logger = logging.getLogger(__name__)

build_command_packet_header = transport.build_command_packet_header
encrypt_command_packet = transport.encrypt_command_packet
write_to_device = transport.write_to_device

OK = 200  # status byte the panel puts in its replies

# Chunk sizes for 39 (file write) and 121 (H.264 stream); the vendor app uses 202752 when the
# panel does not report a block size (17 is not handled by the 8.8" firmware).
UPLOAD_CHUNK_BYTES = 202752
STREAM_CHUNK_BYTES = 202752

# One image push (101 / 102) must stay under about 1 MiB or the panel times out.
MAX_IMAGE_BYTES = 1_000_000

IMAGE_DIR = "/tmp/sdcard/mmcblk0p1/img/"
VIDEO_DIR = "/tmp/sdcard/mmcblk0p1/video/"

# A transparent 480x1920 PNG: what the vendor app pushes (102) to clear the panel.
_BLANK_PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAeAAAAeACAYAAAAW8IT1AAAAAXNSR0IArs4c6QAAAARnQU1BAACxjwv8YQUAAAAJcEhZcwAADsMAAA7DAcdvqGQAAA4MSURBVHhe7cEBDQAAAMKg909tDwcUAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA8GZKyAABEZ2CCgAAAABJRU5ErkJggg==")


# ---------------------------------------------------------------- firmware differences


@dataclass(frozen=True)
class Feature:
    """A behaviour that differs between firmware versions.

    min_version: first firmware that has it, or None if no firmware checked so far has it.
    checked_up_to: highest firmware version this entry was checked against; anything newer is
        assumed to have the feature until someone checks it and updates this entry.
    """

    min_version: Optional[int]
    checked_up_to: int
    evidence: str


# The single list of firmware-dependent behaviour. Commands check it with supports().
# Measured on firmware 0008 and 0015.
FIRMWARE_FEATURES = {
    "stored_image_reply": Feature(None, 15, "113 answers only the first time after a restart or upload"),
    "chip_id": Feature(15, 15, "240 returns a 16-byte chip id on 0015, zeros on 0008"),
    "voltage": Feature(None, 15, "50 answers zeros"),
    "orientation": Feature(None, 15, "60 answers zeros"),
    "run_state": Feature(None, 15, "49 is a plain ack"),
    "block_size": Feature(None, 15, "17 answers zeros"),
    "set_clock": Feature(None, 15, "51 is a plain ack"),
}

# Firmware version per device object, filled from any reply to command 10. A failed or
# unparseable read is not cached, so the next call asks again.
_FIRMWARE_CACHE: dict = {}


def _version_from_reply(response) -> tuple:
    """(firmware string, version number or None) from a reply to command 10, or (None, None)."""
    if response is None or len(response) < 40 or response[0] != 10:
        return None, None
    text = bytes(response[8:40]).split(b"\0", 1)[0].decode("utf-8", errors="replace")
    last = text.rsplit("_", 1)[-1]
    return text, (int(last) if last.isdigit() else None)


def _remember_version(dev, response) -> None:
    _text, version = _version_from_reply(response)
    if version is not None:
        _FIRMWARE_CACHE[id(dev)] = version


def firmware_version(dev) -> Optional[int]:
    """Firmware version number from command 10 (e.g. 8 for turzx_0001_0008); None if unknown."""
    if id(dev) not in _FIRMWARE_CACHE:
        _remember_version(dev, write_to_device(dev, encrypt_command_packet(build_command_packet_header(10))))
    return _FIRMWARE_CACHE.get(id(dev))


def supports(dev, feature: str) -> bool:
    """Whether this panel's firmware has `feature`. Unknown or not-yet-checked versions are assumed to."""
    entry = FIRMWARE_FEATURES[feature]
    version = firmware_version(dev)
    if version is None or version > entry.checked_up_to:
        return True
    return entry.min_version is not None and version >= entry.min_version


def unsupported_reason(dev, feature: str) -> str:
    entry = FIRMWARE_FEATURES[feature]
    needs = (
        f"needs firmware {entry.min_version} or later"
        if entry.min_version is not None
        else f"no firmware checked so far (up to {entry.checked_up_to}) has it"
    )
    return f"not supported by firmware {firmware_version(dev)} ({needs}; {entry.evidence})"


# ---------------------------------------------------------------- packets and replies


def _command(dev, command_id: int, timeout: int = 2000):
    return write_to_device(dev, encrypt_command_packet(build_command_packet_header(command_id)), timeout)


def _path_packet(command_id: int, device_path: str) -> bytearray:
    """[8..11] path length (big-endian), path from [16]: 38, 40, 42, 98, 99, 110, 113."""
    path_bytes = device_path.encode("ascii")
    packet = build_command_packet_header(command_id)
    packet[8:12] = len(path_bytes).to_bytes(4, "big")
    packet[16 : 16 + len(path_bytes)] = path_bytes
    return packet


def _ok(response: Optional[bytes], command_id: int, status_at: int = 1) -> bool:
    """A reply to command_id with OK at response[status_at]."""
    return _accepted(response, command_id, status_at) is not None


def _accepted(response: Optional[bytes], command_id: int, status_at: int = 1, min_len: int = 0) -> Optional[bytes]:
    """The reply if it answers command_id with OK at response[status_at] (and is at least min_len long), else None."""
    if (
        response is None
        or len(response) <= max(status_at, min_len - 1)
        or response[0] != command_id
        or response[status_at] != OK
    ):
        return None
    return response


def _query(dev, command_id: int) -> Optional[bytes]:
    response = _command(dev, command_id)
    if response is None or len(response) < 24 or response[0] != command_id:
        return None
    return bytes(response)


def delay_sync(dev) -> None:
    """Send a sync (10, which also reports the firmware version) and wait briefly."""
    _remember_version(dev, send_sync_command(dev))
    time.sleep(0.2)


def send_sync_command(dev):
    logger.info("Sending sync command...")
    return _command(dev, 10)


def send_restart_device_command(dev):
    logger.info("Sending restart command...")
    return _command(dev, 11)


def send_brightness_command(dev, brightness: int):
    logger.info("Brightness = %d", brightness)
    packet = build_command_packet_header(14)
    packet[8] = brightness
    return write_to_device(dev, encrypt_command_packet(packet))


def send_frame_rate_command(dev, frame_rate: int):
    logger.info("Frame rate = %d", frame_rate)
    packet = build_command_packet_header(15)
    packet[8] = frame_rate
    return write_to_device(dev, encrypt_command_packet(packet))


def set_rotation(dev, rotation: int):
    """13: set the panel rotation; the panel saves it. 0 or 2 (180°) on the 8.8"."""
    packet = build_command_packet_header(13)
    packet[8] = rotation
    return write_to_device(dev, encrypt_command_packet(packet))


def format_bytes(val: int) -> str:
    if val > 1024 * 1024:
        return f"{val / (1024 * 1024):.2f} GB"
    return f"{val / 1024:.2f} MB"


# ---------------------------------------------------------------- files on the SD card


def device_path_for(filename: str) -> Optional[str]:
    """Path on the panel's SD card for a stored .png / .h264, or None if the name is not acceptable.

    Only a plain file name is accepted: no directories, no "..".
    """
    if not filename or filename != Path(filename).name or filename in (".", ".."):
        logger.error("Give a plain file name (no directories): %r", filename)
        return None
    ext = Path(filename).suffix.lower()
    if ext == ".png":
        return IMAGE_DIR + filename
    if ext == ".h264":
        return VIDEO_DIR + filename
    logger.error("Unsupported file type: only .png and .h264 are stored on the panel.")
    return None


def file_size(dev, filename: str) -> Optional[int]:
    """98: size in bytes of a stored .png / .h264 (0 if it does not exist)."""
    device_path = device_path_for(filename)
    if device_path is None:
        return None
    return _remote_size(dev, device_path)


def _remote_size(dev, device_path: str) -> Optional[int]:
    response = _accepted(write_to_device(dev, encrypt_command_packet(_path_packet(98, device_path))), 98, min_len=12)
    if response is None:
        logger.error("Panel did not answer the file size query (98)")
        return None
    return int.from_bytes(response[8:12], "little")


def delete_file(dev, filename: str) -> bool:
    """42: delete a stored file. The panel also answers OK when the file does not exist."""
    device_path = device_path_for(filename)
    if device_path is None:
        return False
    logger.info("Delete: %s", device_path)
    if not _ok(write_to_device(dev, encrypt_command_packet(_path_packet(42, device_path))), 42):
        logger.error("Panel did not confirm the delete (42).")
        return False
    return True


def upload_file(dev, file_path: str) -> bool:
    """Store a .png (as is) or .mp4 (as .h264) on the SD card, then check its size there."""
    local_path = Path(file_path)
    if not local_path.is_file():
        logger.error("File does not exist: %s", file_path)
        return False
    ext = local_path.suffix.lower()
    if ext == ".mp4":
        local_path = extract_h264_from_mp4(file_path)
        device_path = VIDEO_DIR + local_path.name
    elif ext == ".png":
        device_path = IMAGE_DIR + local_path.name
    else:
        logger.error("Unsupported file type: only .png and .mp4 can be uploaded.")
        return False
    size = local_path.stat().st_size
    if size == 0:
        logger.error("Refusing to upload an empty file: %s", local_path)
        return False

    logger.info("Uploading %s → %s (%d bytes)", local_path, device_path, size)
    # 38 opens the file; the panel answers OK at [8] (even for a missing folder, so the size check below matters).
    if not _ok(write_to_device(dev, encrypt_command_packet(_path_packet(38, device_path))), 38, status_at=8):
        logger.error("Panel did not open %s for writing (38).", device_path)
        return False
    if not _write_file_chunks(dev, local_path, size):
        return False
    stored = _remote_size(dev, device_path)
    if stored != size:
        logger.error("Upload check failed: %s is %s bytes on the panel, %d locally.", device_path, stored, size)
        return False
    return True


def _write_file_chunks(dev, local_path: Path, size: int) -> bool:
    """39: [8..11] bytes to receive, [12..15] bytes to write, [16] = 1 on the last chunk (closes the file)."""
    sent = 0
    with open(local_path, "rb") as fh:
        while True:
            chunk = fh.read(UPLOAD_CHUNK_BYTES)
            if not chunk:
                return True
            sent += len(chunk)
            packet = build_command_packet_header(39)
            packet[8:12] = len(chunk).to_bytes(4, "big")
            packet[12:16] = len(chunk).to_bytes(4, "big")
            packet[16] = 1 if sent >= size else 0
            if not _ok(write_to_device(dev, encrypt_command_packet(packet) + chunk), 39):
                logger.error("Panel did not confirm the chunk ending at byte %d (39).", sent)
                return False


def send_list_storage_command(dev, path: str, timeout: float = 10.0) -> Optional[list]:
    """File names in a directory on the SD card (99), or None if the panel did not answer.

    The same request is repeated: the panel answers empty (all-NUL) replies until the listing is
    ready (the 9th reply on firmware 0008), then "result:dir:file:<name>/<name>/..." (NUL padded).
    """
    logger.info("Listing %s", path)
    packet = encrypt_command_packet(_path_packet(99, path))
    received = bytearray()
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        response = write_to_device(dev, packet)
        if not response:
            break
        if not any(response):
            if received:
                break  # done; the rest is NUL padding
            continue
        received += response
    else:
        if received:
            logger.warning("Listing may be truncated (still receiving data after %.0f s)", timeout)

    if not received:
        logger.error("No listing received from the panel.")
        return None
    text = received.replace(b"\0", b"").decode("utf-8", errors="ignore")
    if "file:" not in text:
        return []
    return [n.strip() for n in text.split("file:", 1)[1].split("/") if n.strip()]


def send_refresh_storage_command(dev) -> Optional[dict]:
    """SD card size in KB (100): total, used, free; None if the panel did not answer."""
    response = _query(dev, 100)
    if response is None:
        logger.error("Panel did not answer the storage query (100)")
        return None
    total, used, free = (int.from_bytes(response[o : o + 4], "little") for o in (8, 12, 16))
    logger.info("Card total %s, used %s, free %s", format_bytes(total), format_bytes(used), format_bytes(free))
    return {"total_kb": total, "used_kb": used, "free_kb": free}


# ---------------------------------------------------------------- status queries


ORIENTATIONS = ("portrait-0", "landscape-90", "portrait-180", "landscape-270")


def read_device_info(dev) -> Optional[dict]:
    """Read-only status: firmware (10), storage (100), stored-video flag (112), stream queue (122), chip id (240).

    Returns None if the panel does not answer the version query.
    """
    reply = _query(dev, 10)
    firmware, version = _version_from_reply(reply)
    if firmware is None:
        logger.error("Panel did not answer the version query (10)")
        return None
    _remember_version(dev, reply)
    info: dict = {"firmware": firmware}
    if version is not None:
        info["firmware_version"] = version
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


def read_voltage(dev) -> Optional[int]:
    """50: supply voltage in mV."""
    response = _query(dev, 50)
    return None if response is None else int.from_bytes(response[8:12], "big")


def read_orientation(dev) -> Optional[dict]:
    """60: orientation sensor; None when the firmware has no sensor (it answers zeros).

    Like the vendor app's first probe, a reading of 0 (portrait-0) counts as "no sensor".
    """
    response = _accepted(_command(dev, 60, timeout=250), 60, min_len=10)
    if response is None:
        return None
    if response[8] > 3 or response[9] > 3 or response[9] == 0:
        return None
    return {"sensor": response[8], "orientation": response[9], "name": ORIENTATIONS[response[9]]}


def read_block_size(dev) -> Optional[int]:
    """17: stream block size the panel wants; 0 means use the default (202752)."""
    response = _query(dev, 17)
    return None if response is None else int.from_bytes(response[8:12], "big")


def set_clock(dev, when, mode: int = 0):
    """51: set the panel clock: [8..9] year (big-endian), month, day, hour, minute, second, [15] mode.

    The vendor app sends it only to the 4.6" model, with mode 0, 1 or 2 (meaning not decoded).
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
    return _command(dev, 12)


def send_save_settings_command(
    dev,
    brightness: int = 100,
    startup: int = 0,
    reserved: int = 0,
    rotation: int = 0,
    sleep: int = 0,
    offline: int = 0,
    clock_direction: int = 0,
):
    """125: save all settings at once (every field is written; there is no read-back).

    [8] brightness, [9] startup mode, [10] 0, [11] rotation, [12] sleep, [13] offline mode,
    [14] offline clock direction (sent by the vendor app; firmware 0015 stores only [8..13]).
    """
    logger.info(
        "Saving settings: brightness=%d startup=%d rotation=%d sleep=%d offline=%d clock_direction=%d",
        brightness, startup, rotation, sleep, offline, clock_direction,
    )
    packet = build_command_packet_header(125)
    packet[8:15] = bytes([brightness, startup, reserved, rotation, sleep, offline, clock_direction])
    return write_to_device(dev, encrypt_command_packet(packet))


# ---------------------------------------------------------------- playback


def stop_play(dev) -> bool:
    """Stop stored playback like the vendor app: 111, then poll 112 until it reports stopped."""
    logger.info("Stopping playback")
    if not _ok(_command(dev, 111), 111):
        return False
    for _ in range(10):
        response = _command(dev, 112)
        if response is not None and len(response) > 8 and response[8] == 0:
            return True
        time.sleep(0.1)
    logger.error("Panel still reports playback after stop (112)")
    return False


def play_stored_asset(dev, filename: str, brightness: Optional[int] = None) -> bool:
    """Play a stored .h264 (110) or show a stored .png (113) with the vendor app's sequence:
    video: 98 (size > 0), 111, 112 until stopped, 102 (blank), 110;
    image: 111, 112 until stopped, 102 (blank), 113.
    brightness (14, before the blank) is the CLI's own option. Firmware may not confirm 113
    (FIRMWARE_FEATURES); only then is the firmware version asked for (10).
    """
    device_path = device_path_for(filename)
    if device_path is None:
        return False
    if device_path.endswith(".h264"):
        size = _remote_size(dev, device_path)
        if not size:
            logger.error("%s is not on the panel (or is empty)", device_path)
            return False
    if not stop_play(dev):
        return False
    if brightness is not None:
        send_brightness_command(dev, brightness)
    clear_image(dev)
    logger.info("Play: %s", device_path)
    if device_path.endswith(".h264"):
        # 110: the panel answers [1] = OK ([8] stays 0, unlike what the vendor app checks).
        if not _ok(write_to_device(dev, encrypt_command_packet(_path_packet(110, device_path))), 110):
            logger.error("Panel did not confirm playing %s (110)", device_path)
            return False
        return True
    response = write_to_device(dev, encrypt_command_packet(_path_packet(113, device_path)))
    if _ok(response, 113):
        return True
    if response is None and not supports(dev, "stored_image_reply"):
        logger.warning("Firmware %s does not always confirm showing a stored image; check the screen.", firmware_version(dev))
        return True
    logger.error("Panel did not confirm showing %s (113)", device_path)
    return False


# ---------------------------------------------------------------- images


def _encode_png(image: Image.Image) -> bytes:
    buffer = io.BytesIO()
    image.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()


def _send_png_bytes(dev, png: bytes, part: Optional[str] = None):
    """102: show a PNG now; [8..11] length (big-endian), data after the packet."""
    packet = build_command_packet_header(102)
    packet[8:12] = len(png).to_bytes(4, "big")
    logger.info("→ Transmitting [%s] - %d bytes", part or "image", len(png))
    return write_to_device(dev, encrypt_command_packet(packet) + png)


def send_jpeg_bytes(dev, jpeg: bytes):
    """101: show a JPEG now; same layout as 102."""
    packet = build_command_packet_header(101)
    packet[8:12] = len(jpeg).to_bytes(4, "big")
    logger.info("→ Transmitting JPEG - %d bytes", len(jpeg))
    return write_to_device(dev, encrypt_command_packet(packet) + jpeg)


def clear_image(dev):
    """Push a transparent full-screen PNG (102), as the vendor app does before playing."""
    return _send_png_bytes(dev, _BLANK_PNG, part="blank")


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


def send_image(
    dev,
    image_path,
    max_chunk_bytes: int = 524288,
    fmt: str = "png",
    quality: int = 90,
    max_bytes: int = MAX_IMAGE_BYTES,
) -> bool:
    """Show an image now.

    fmt "png": one PNG (102), split into layers if it is over max_chunk_bytes (a workaround for the
        panel's size limit, not something the vendor app does).
    fmt "jpeg": one JPEG (101), quality lowered until it fits max_bytes.
    fmt "auto": PNG if it fits one push, otherwise JPEG.
    """
    with Image.open(image_path) as img:
        img.load()
        if img.size != (480, 1920):
            logger.warning("Image is %dx%d, not 480x1920 (the screen).", *img.size)
        if fmt == "png":
            return _send_png_layers(dev, img.convert("RGBA"), max_chunk_bytes)
        if fmt == "auto":
            png = _encode_png(img.convert("RGBA"))
            if len(png) <= min(max_chunk_bytes, max_bytes):
                return _send_png_bytes(dev, png) is not None
        if fmt in ("auto", "jpeg"):
            return send_jpeg_bytes(dev, encode_jpeg_under(img, max_bytes, quality)) is not None
        raise ValueError(f"Unknown image format: {fmt}")


def _send_png_layers(dev, img, max_chunk_bytes: int) -> bool:
    """Split a large PNG into horizontal bands sent bottom-up, each on a canvas from the top of the
    screen to the band's bottom edge, so later (higher) bands do not hide earlier ones."""
    width, height = img.size
    layers = max(1, math.ceil(len(_encode_png(img)) / max_chunk_bytes))
    logger.info("Sending the image in %d layer(s)", layers)
    edges = [height * i // layers for i in range(layers + 1)]  # band k covers edges[k]..edges[k+1]
    results = []
    for k in reversed(range(layers)):
        top, bottom = edges[k], edges[k + 1]
        canvas = Image.new("RGBA", (width, bottom), (0, 0, 0, 0))
        canvas.paste(img.crop((0, top, width, bottom)), (0, top))
        results.append(_send_png_bytes(dev, _encode_png(canvas), part=f"rows {top}-{bottom}"))
    return all(r is not None for r in results)


# ---------------------------------------------------------------- video


def extract_h264_from_mp4(mp4_path: str) -> Path:
    """Copy the H.264 stream of an MP4 into <name>.mp4.h264 (Annex B), reusing an existing one."""
    input_path = Path(mp4_path)
    if not input_path.exists():
        raise FileNotFoundError(f"Input file not found: {input_path}")
    output_path = input_path.with_name(input_path.name + ".h264")
    if output_path.exists():
        logger.info("%s already exists; using it.", output_path.name)
        return output_path
    cmd = ["ffmpeg", "-y", "-i", str(input_path), "-c:v", "copy", "-bsf:v", "h264_mp4toannexb", "-an", "-f", "h264", str(output_path)]
    logger.info("Extracting H.264 from %s...", input_path.name)
    subprocess.run(cmd, check=True, capture_output=True, text=True)
    return output_path


def video_frame_rate(mp4_path: str) -> Optional[int]:
    """Frame rate of the first video stream (ffprobe), rounded; None if it cannot be read."""
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=avg_frame_rate,r_frame_rate",
             "-of", "json", str(mp4_path)],
            check=True, capture_output=True, text=True,
        ).stdout
        stream = json.loads(out)["streams"][0]
        for key in ("avg_frame_rate", "r_frame_rate"):
            num, _, den = stream.get(key, "0/0").partition("/")
            if den and float(den) and float(num):
                return round(float(num) / float(den))
    except (OSError, subprocess.CalledProcessError, ValueError, KeyError, IndexError):
        pass
    return None


def wait_for_stream(dev, max_queued: int, timeout: float = 10.0) -> bool:
    """Poll the stream status (122) until the panel reports at most max_queued buffered chunks.

    Returns False if it is still above that after `timeout` seconds (or stops answering).
    """
    deadline = time.monotonic() + timeout
    while True:
        time.sleep(0.05)
        response = _command(dev, 122)
        if response is None or len(response) < 9:
            logger.error("Panel did not report its stream status (122)")
            return False
        if response[8] <= max_queued:
            return True
        if time.monotonic() >= deadline:
            logger.error("Panel still has %d chunks queued after %.1f s", response[8], timeout)
            return False


def send_video(
    dev,
    video_path,
    loop: bool = False,
    brightness: Optional[int] = None,
    rotation: Optional[int] = None,
    frame_rate: Optional[int] = None,
) -> bool:
    """Stream an MP4's H.264 to the panel (121 chunks, flow control with 122, 123 to stop).

    The frame rate (15) is the video's own, like the vendor app, unless frame_rate is given.
    With loop=True, Ctrl-C ends the stream normally; otherwise it is re-raised after stopping.
    """
    h264 = extract_h264_from_mp4(video_path)
    if frame_rate is None:
        frame_rate = video_frame_rate(video_path)
        if frame_rate is None:
            logger.warning("Could not read the frame rate of %s; using 25", video_path)
            frame_rate = 25
    frame_rate = max(1, min(60, frame_rate))

    _command(dev, 111)
    _command(dev, 112)
    if rotation is not None:
        set_rotation(dev, rotation)  # the panel saves it, so only when asked
    if brightness is not None:
        send_brightness_command(dev, brightness)
    clear_image(dev)
    send_frame_rate_command(dev, frame_rate)

    size = h264.stat().st_size
    ok = True
    try:
        while ok:
            with open(h264, "rb") as fh:
                chunks = 0
                while True:
                    data = fh.read(STREAM_CHUNK_BYTES)
                    if not data:
                        break
                    chunks += 1
                    packet = build_command_packet_header(121)
                    packet[8:12] = len(data).to_bytes(4, "big")
                    packet[12] = 1 if fh.tell() >= size else 0  # last chunk, as the vendor app
                    response = write_to_device(dev, encrypt_command_packet(packet) + data)
                    time.sleep(0.03)
                    # reply[8] = chunks queued on the panel; like the vendor app, wait while it is over 3
                    if response is None or len(response) < 9 or response[8] > 3:
                        if not wait_for_stream(dev, 2):
                            ok = False
                            break
                logger.info("Sent %d chunks", chunks)
            if not loop:
                break
    except KeyboardInterrupt:
        _stop_stream(dev)
        if loop:
            return True
        raise
    _stop_stream(dev)
    return ok


def _stop_stream(dev) -> None:
    """123 ends the stream; the vendor app follows it with 111."""
    _command(dev, 123)
    _command(dev, 111)
