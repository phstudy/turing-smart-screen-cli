"""High-level device operations for the Turing Smart Screen."""

from __future__ import annotations

import io
import logging
import math
import subprocess
import time
from pathlib import Path
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


def delete_file(dev, filename: str) -> bool:
    path_obj = Path(filename)
    ext = path_obj.suffix.lower()

    if ext == ".png":
        device_path = f"/tmp/sdcard/mmcblk0p1/img/{filename}"
        logger.info("Delete PNG: %s", device_path)
    elif ext == ".h264":
        device_path = f"/tmp/sdcard/mmcblk0p1/video/{filename}"
        logger.info("Delete H264: %s", device_path)
    else:
        logger.error("Error: Unsupported file type. Only .png and .h264 are allowed.")
        return False

    if not _delete_command(dev, device_path):
        logger.error("Failed to delete remote file.")
        return False

    logger.info("Delete completed successfully.")
    return True


def play_file(dev, filename: str, play_command_func: Optional[Callable] = None) -> bool:
    if play_command_func is None:
        play_command_func = _play_command

    path_obj = Path(filename)
    ext = path_obj.suffix.lower()

    if ext == ".png":
        device_path = f"/tmp/sdcard/mmcblk0p1/img/{filename}"
        logger.info("Play PNG: %s", device_path)
    elif ext == ".h264":
        device_path = f"/tmp/sdcard/mmcblk0p1/video/{filename}"
        logger.info("Play H264: %s", device_path)
    else:
        logger.error("Error: Unsupported file type. Only .png and .h264 are allowed.")
        return False

    if not play_command_func(dev, device_path):
        logger.error("Failed to play %s", device_path)
        return False

    logger.info("Play command sent successfully.")
    return True


def play_file2(dev, filename: str) -> bool:
    return play_file(dev, filename, _play2_command)


def play_file3(dev, filename: str) -> bool:
    return play_file(dev, filename, _play3_command)


def upload_file(dev, file_path: str) -> bool:
    local_path = Path(file_path)
    if not local_path.exists():
        logger.error("Error: File does not exist: %s", file_path)
        return False

    ext = local_path.suffix.lower()
    if ext == ".png":
        device_path = f"/tmp/sdcard/mmcblk0p1/img/{local_path.name}"
        logger.info("Uploading PNG: %s → %s", file_path, device_path)
    elif ext == ".mp4":
        h264_path = extract_h264_from_mp4(file_path)
        device_path = f"/tmp/sdcard/mmcblk0p1/video/{h264_path.name}"
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


def send_list_storage_command(dev, path: str) -> None:
    logger.info("Listing storage for path: %s", path)

    path_bytes = path.encode("ascii")
    length = len(path_bytes)

    packet = build_command_packet_header(99)

    packet[8] = (length >> 24) & 0xFF
    packet[9] = (length >> 16) & 0xFF
    packet[10] = (length >> 8) & 0xFF
    packet[11] = length & 0xFF
    packet[12:16] = b"\x00\x00\x00\x00"
    packet[16 : 16 + length] = path_bytes

    receive_buffer = bytearray(10240)
    receive_offset = 0

    max_tries = 20
    for i in range(max_tries):
        response = write_to_device(dev, encrypt_command_packet(packet))
        if response:
            chunk_size = len(response)
            if receive_offset + chunk_size <= len(receive_buffer):
                receive_buffer[receive_offset : receive_offset + chunk_size] = response
                receive_offset += chunk_size
            else:
                logger.warning("Buffer overflow prevented. Increase buffer size for larger directory listings.")
                break
        else:
            if i > 0:
                logger.warning("No response in chunk %d", i)
            break

    if receive_offset == 0:
        logger.warning("No data received.")
        return

    try:
        decoded_string = receive_buffer[:receive_offset].decode("utf-8", errors="ignore")
        files = decoded_string.split("file:")

        if len(files) > 1:
            logger.info("Files found:")
            for filename in files[-1].rstrip("/").split("/"):
                if filename.strip():
                    logger.info("  %s", filename)
        else:
            logger.info("No files found or format unexpected")
    except Exception as exc:
        logger.error("Failed to decode received data: %s", exc)


def send_refresh_storage_command(dev) -> None:
    logger.info("Refreshing storage information...")
    response = write_to_device(dev, encrypt_command_packet(build_command_packet_header(100)))

    if not response or len(response) < 20:
        logger.error("Invalid or incomplete response from device")
        return

    try:
        total = format_bytes(int.from_bytes(response[8:12], byteorder="little"))
        used = format_bytes(int.from_bytes(response[12:16], byteorder="little"))
        valid = format_bytes(int.from_bytes(response[16:20], byteorder="little"))

        logger.info("  Card Total: %s", total)
        logger.info("  Card Used:  %s", used)
        logger.info("  Card Valid: %s", valid)
    except Exception as exc:
        logger.error("Error parsing storage information: %s", exc)


def send_save_settings_command(
    dev,
    brightness: int = 0,
    startup: int = 0,
    reserved: int = 0,
    rotation: int = 0,
    sleep: int = 0,
    offline: int = 0,
):
    logger.info("Saving device settings...")
    logger.info("  Brightness:     %d", brightness)
    logger.info("  Startup Mode:   %d", startup)
    logger.info("  Reserved:       %d", reserved)
    logger.info("  Rotation:       %d", rotation)
    logger.info("  Sleep Timeout:  %d", sleep)
    logger.info("  Offline Mode:   %d", offline)

    cmd_packet = build_command_packet_header(125)
    cmd_packet[8] = brightness
    cmd_packet[9] = startup
    cmd_packet[10] = reserved
    cmd_packet[11] = rotation
    cmd_packet[12] = sleep
    cmd_packet[13] = offline

    return write_to_device(dev, encrypt_command_packet(cmd_packet))


def stop_play(dev) -> bool:
    logger.info("Stopping playback (phase 1)")
    cmd_packet = build_command_packet_header(111)
    write_to_device(dev, encrypt_command_packet(cmd_packet))

    logger.info("Stopping playback (phase 2)")
    cmd_packet = build_command_packet_header(114)
    write_to_device(dev, encrypt_command_packet(cmd_packet))

    return True


def send_image(dev, image_path, max_chunk_bytes: int = 524288) -> bool:
    try:
        with Image.open(image_path) as img:
            img = img.convert("RGBA")
            width, height = img.size

            if (width, height) != (480, 1920):
                logger.warning(
                    "Image resolution is %dx%d, not 480x1920 (device screen resolution).",
                    width,
                    height,
                )

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

                encoded = _encode_png(layer_img)
                results.append(_send_png_bytes(dev, encoded, part=label))

            return all(results)
    except Exception as exc:
        logger.error("Error sending image: %s", exc)
        return False


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


def delay(dev, rst):
    time.sleep(0.05)
    logger.info("Waiting for device readiness...")
    cmd_packet = build_command_packet_header(122)
    response = write_to_device(dev, encrypt_command_packet(cmd_packet))
    if response and response[8] > rst:
        delay(dev, rst)


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


def send_video(dev, video_path, loop: bool = False) -> bool:
    try:
        output_path = extract_h264_from_mp4(video_path)

        write_to_device(dev, encrypt_command_packet(build_command_packet_header(111)))
        write_to_device(dev, encrypt_command_packet(build_command_packet_header(112)))
        write_to_device(dev, encrypt_command_packet(build_command_packet_header(13)))

        send_brightness_command(dev, 32)
        write_to_device(dev, encrypt_command_packet(build_command_packet_header(41)))
        clear_image(dev)
        send_frame_rate_command(dev, 25)

        logger.info("Streaming video data...")

        try:
            while True:
                with open(output_path, "rb") as fh:
                    chunk_count = 0
                    while True:
                        data = fh.read(202752)
                        chunk_size = len(data)
                        if not data:
                            break

                        chunk_count += 1
                        if chunk_count % 10 == 0:
                            logger.info("Sending chunk #%d (%d bytes)", chunk_count, chunk_size)

                        cmd_packet = build_command_packet_header(121)
                        cmd_packet[8] = (chunk_size >> 24) & 0xFF
                        cmd_packet[9] = (chunk_size >> 16) & 0xFF
                        cmd_packet[10] = (chunk_size >> 8) & 0xFF
                        cmd_packet[11] = chunk_size & 0xFF

                        full_payload = encrypt_command_packet(cmd_packet) + data
                        response = write_to_device(dev, full_payload)
                        time.sleep(0.03)

                        if response is None or len(response) < 9 or response[8] <= 3:
                            delay(dev, 2)

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


def play_stored_asset(dev, filename: str) -> bool:
    path_obj = Path(filename)
    ext = path_obj.suffix.lower()

    delay_sync(dev)
    stop_play(dev)
    send_brightness_command(dev, 32)

    if ext == ".h264":
        play_file(dev, filename)

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
        with open(file_path, "rb") as fh:
            chunk_index = 0
            while True:
                data_chunk = fh.read(202752)
                if not data_chunk:
                    break

                chunk_size = len(data_chunk)
                chunk_index += 1
                logger.debug("Chunk %d size: %d bytes", chunk_index, chunk_size)

                cmd_packet = build_command_packet_header(39)
                cmd_packet[8] = (chunk_size >> 24) & 0xFF
                cmd_packet[9] = (chunk_size >> 16) & 0xFF
                cmd_packet[10] = (chunk_size >> 8) & 0xFF
                cmd_packet[11] = chunk_size & 0xFF

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

    packet = build_command_packet_header(40)
    packet[8] = (length >> 24) & 0xFF
    packet[9] = (length >> 16) & 0xFF
    packet[10] = (length >> 8) & 0xFF
    packet[11] = length & 0xFF
    packet[12:16] = b"\x00\x00\x00\x00"
    packet[16 : 16 + length] = path_bytes

    return write_to_device(dev, encrypt_command_packet(packet))


def _play_command(dev, file_path: str):
    logger.info("Requesting playback for: %s", file_path)

    path_bytes = file_path.encode("ascii")
    length = len(path_bytes)

    packet = build_command_packet_header(98)

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
