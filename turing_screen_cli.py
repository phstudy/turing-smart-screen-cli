import argparse
import io
import logging
import math
import platform
import struct
import subprocess
import sys
import time
from pathlib import Path

import usb.core
import usb.util
from Crypto.Cipher import DES
from PIL import Image

VENDOR_ID = 0x1cbe
PRODUCT_ID = 0x0088

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger('turing_screen_cli')

def build_command_packet_header(a0: int) -> bytearray:
    """
    Build a command packet header with a given command ID.

    Args:
        a0 (int): Command ID.

    Returns:
        bytearray: The command packet header.
    """
    packet = bytearray(500)
    packet[0] = a0
    packet[2] = 0x1A
    packet[3] = 0x6D
    timestamp = int((time.time() - time.mktime(time.localtime()[:3] + (0, 0, 0, 0, 0, -1))) * 1000)
    packet[4:8] = struct.pack('<I', timestamp)
    return packet

def encrypt_with_des(key: bytes, data: bytes) -> bytes:
    """
    Encrypt data using DES encryption in CBC mode.

    Args:
        key (bytes): The encryption key.
        data (bytes): The data to encrypt.

    Returns:
        bytes: The encrypted data.
    """
    cipher = DES.new(key, DES.MODE_CBC, key)
    padded_len = (len(data) + 7) // 8 * 8
    padded_data = data.ljust(padded_len, b'\x00')
    return cipher.encrypt(padded_data)

def encrypt_command_packet(data: bytearray) -> bytearray:
    """
    Encrypt a command packet using DES encryption.

    Args:
        data (bytearray): The command packet to encrypt.

    Returns:
        bytearray: The encrypted command packet.
    """
    des_key = b'slv3tuzx'
    encrypted = encrypt_with_des(des_key, data)
    final_packet = bytearray(512)
    final_packet[:len(encrypted)] = encrypted
    final_packet[510] = 161
    final_packet[511] = 26
    return final_packet

def find_usb_device():
    """
    Find and configure the USB device.

    Returns:
        usb.core.Device: The USB device.

    Raises:
        ValueError: If the USB device is not found.
    """
    dev = usb.core.find(idVendor=VENDOR_ID, idProduct=PRODUCT_ID)
    if dev is None:
        raise ValueError('USB device not found')

    try:
        dev.set_configuration()
    except usb.core.USBError as e:
        logger.warning("set_configuration() failed: %s", e)

    if platform.system() == "Linux":
        try:
            if dev.is_kernel_driver_active(0):
                dev.detach_kernel_driver(0)
        except usb.core.USBError as e:
            logger.warning("detach_kernel_driver failed: %s", e)

    return dev

def read_flush(ep_in, max_attempts=5):
    """
    Flush the USB IN endpoint by reading available data until timeout or max attempts reached.

    Args:
        ep_in: The USB IN endpoint.
        max_attempts (int): Maximum number of attempts to flush.
    """
    for _ in range(max_attempts):
        try:
            ep_in.read(512, timeout=100)
        except usb.core.USBError as e:
            if e.errno == 110 or e.args[0] == 'Operation timed out':
                break
            else:
                break

def write_to_device(dev, data, timeout=2000):
    """
    Write data to the USB device and read the response.

    Args:
        dev: The USB device.
        data: The data to write.
        timeout (int): Timeout for the operation.

    Returns:
        bytes: The response from the device, or None if an error occurs.
    """
    cfg = dev.get_active_configuration()
    intf = usb.util.find_descriptor(cfg, bInterfaceNumber=0)
    if intf is None:
        raise RuntimeError("USB interface 0 not found")
    ep_out = usb.util.find_descriptor(intf, custom_match=lambda e: usb.util.endpoint_direction(e.bEndpointAddress) == usb.util.ENDPOINT_OUT)
    ep_in = usb.util.find_descriptor(intf, custom_match=lambda e: usb.util.endpoint_direction(e.bEndpointAddress) == usb.util.ENDPOINT_IN)
    assert ep_out is not None and ep_in is not None, "Could not find USB endpoints"
    
    try:
        ep_out.write(data, timeout)
    except usb.core.USBError as e:
        logger.error("USB write error: %s", e)
        return None
    
    try:
        response = ep_in.read(512, timeout)
        read_flush(ep_in)
        return bytes(response)
    except usb.core.USBError as e:
        logger.error("USB read error: %s", e)
        return None

def delay_sync(dev):
    """
    Send a sync command to the device and wait for a short delay.

    Args:
        dev: The USB device.
    """
    send_sync_command(dev)
    time.sleep(0.2)

def send_sync_command(dev):
    """
    Send a sync command (ID 10) to the device.

    Args:
        dev: The USB device.

    Returns:
        The response from the device.
    """
    logger.info("Sending Sync Command (ID 10)...")
    cmd_packet = build_command_packet_header(10)
    return write_to_device(dev, encrypt_command_packet(cmd_packet))

def send_restart_device_command(dev):
    """
    Send a restart command (ID 11) to the device.

    Args:
        dev: The USB device.

    Returns:
        The response from the device.
    """
    logger.info("Sending Restart Command (ID 11)...")
    return write_to_device(dev, encrypt_command_packet(build_command_packet_header(11)))

def send_brightness_command(dev, brightness: int):
    """
    Send a brightness command (ID 14) to the device.

    Args:
        dev: The USB device.
        brightness (int): The brightness value (0–102).

    Returns:
        The response from the device.
    """
    logger.info("Sending Brightness Command (ID 14)...")
    logger.info("  Brightness = %d", brightness)
    cmd_packet = build_command_packet_header(14)
    cmd_packet[8] = brightness
    return write_to_device(dev, encrypt_command_packet(cmd_packet))

def send_frame_rate_command(dev, frame_rate: int):
    """
    Send a frame rate command (ID 15) to the device.

    Args:
        dev: The USB device.
        frame_rate (int): The frame rate value.

    Returns:
        The response from the device.
    """
    logger.info("Sending Frame Rate Command (ID 15)...")
    logger.info("  Frame Rate = %d", frame_rate)
    cmd_packet = build_command_packet_header(15)
    cmd_packet[8] = frame_rate
    return write_to_device(dev, encrypt_command_packet(cmd_packet))

def format_bytes(val):
    """
    Format a byte value into a human-readable string.

    Args:
        val (int): The byte value.

    Returns:
        str: The formatted string.
    """
    if val > 1024 * 1024:
        return f"{val / (1024 * 1024):.2f} GB"
    else:
        return f"{val / 1024:.2f} MB"

def delete_file(dev, filename: str):
    """
    Delete a file from the device storage.

    Args:
        dev: The USB device.
        filename (str): The filename to delete.

    Returns:
        bool: True if the file was deleted successfully, False otherwise.
    """
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

def play_file(dev, filename: str, play_command_func=None):
    """
    Play a file from the device storage.

    Args:
        dev: The USB device.
        filename (str): The filename to play.
        play_command_func (function, optional): The function to use for playing. Defaults to _play_command.

    Returns:
        bool: True if the file was played successfully, False otherwise.
    """
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

def play_file2(dev, filename: str):
    """
    Play a file using the alternative play command (ID 110).

    Args:
        dev: The USB device.
        filename (str): The filename to play.

    Returns:
        bool: True if the file was played successfully, False otherwise.
    """
    return play_file(dev, filename, _play2_command)

def play_file3(dev, filename: str):
    """
    Play a file using the alternative play command (ID 113).

    Args:
        dev: The USB device.
        filename (str): The filename to play.

    Returns:
        bool: True if the file was played successfully, False otherwise.
    """
    return play_file(dev, filename, _play3_command)

def upload_file(dev, file_path: str):
    """
    Upload a file to the device storage.

    Args:
        dev: The USB device.
        file_path (str): The path of the file to upload.

    Returns:
        bool: True if the file was uploaded successfully, False otherwise.
    """
    path_obj = Path(file_path)
    if not path_obj.exists():
        logger.error("Error: File does not exist: %s", file_path)
        return False

    ext = path_obj.suffix.lower()
    if ext == ".png":
        device_path = f"/tmp/sdcard/mmcblk0p1/img/{path_obj.name}"
        logger.info("Uploading PNG: %s → %s", file_path, device_path)
    elif ext == ".mp4":
        h264_path = extract_h264_from_mp4(file_path)
        device_path = f"/tmp/sdcard/mmcblk0p1/video/{h264_path.name}"
        file_path = h264_path  # Update local path to .h264
        logger.info("Uploading MP4 as H264: %s → %s", file_path, device_path)
    else:
        logger.error("Error: Unsupported file type. Only .png and .mp4 are allowed.")
        return False

    if not _open_file_command(dev, device_path):
        logger.error("Failed to open remote file for writing.")
        return False

    if not _write_file_command(dev, str(file_path)):
        logger.error("Failed to write file data.")
        return False

    logger.info("Upload completed successfully.")
    return True

def send_list_storage_command(dev, path: str):
    """
    Send a List Storage Command (ID 99) to list files in a directory on the device.

    Args:
        dev: The USB device.
        path (str): The path of the directory to list.
    """
    logger.info("Sending List Storage Command (ID 99) for path: %s", path)

    path_bytes = path.encode('ascii')
    length = len(path_bytes)
    
    packet = build_command_packet_header(99)

    packet[8] = (length >> 24) & 0xFF
    packet[9] = (length >> 16) & 0xFF
    packet[10] = (length >> 8) & 0xFF
    packet[11] = length & 0xFF
    packet[12:16] = b'\x00\x00\x00\x00'
    packet[16:16+length] = path_bytes

    receive_buffer = bytearray(10240)
    receive_offset = 0

    max_tries = 20
    for i in range(max_tries):
        response = write_to_device(dev, encrypt_command_packet(packet))
        if response:
            chunk_size = len(response)
            if receive_offset + chunk_size <= len(receive_buffer):
                receive_buffer[receive_offset:receive_offset + chunk_size] = response
                receive_offset += chunk_size
            else:
                logger.warning("Buffer overflow prevented. Increase buffer size for larger directory listings.")
                break
        else:
            if i > 0:  # Only log warning if we've received some data
                logger.warning("No response in chunk %d", i)
            break

    if receive_offset == 0:
        logger.warning("No data received.")
        return

    try:
        decoded_string = receive_buffer[:receive_offset].decode('utf-8', errors='ignore')
        files = decoded_string.split('file:')
        
        if len(files) > 1:
            logger.info("Files found:")
            for filename in files[-1].rstrip('/').split('/'):
                if filename.strip():
                    logger.info("  %s", filename)
        else:
            logger.info("No files found or format unexpected")
    except Exception as e:
        logger.error("Failed to decode received data: %s", e)

def send_refresh_storage_command(dev):
    """
    Send a Refresh Storage Command (ID 100) to get storage information.

    Args:
        dev: The USB device.
    """
    logger.info("Sending Refresh Storage Command (ID 100)...")
    response = write_to_device(dev, encrypt_command_packet(build_command_packet_header(100)))
    
    if not response or len(response) < 20:
        logger.error("Invalid or incomplete response from device")
        return

    try:
        total = format_bytes(int.from_bytes(response[8:12], byteorder='little'))
        used = format_bytes(int.from_bytes(response[12:16], byteorder='little'))
        valid = format_bytes(int.from_bytes(response[16:20], byteorder='little'))

        logger.info("  Card Total: %s", total)
        logger.info("  Card Used:  %s", used)
        logger.info("  Card Valid: %s", valid)
    except Exception as e:
        logger.error("Error parsing storage information: %s", e)

def send_save_settings_command(dev, brightness=0, startup=0, reserved=0, rotation=0, sleep=0, offline=0):
    """
    Send a Save Settings Command (ID 125) to save device settings.

    Args:
        dev: The USB device.
        brightness (int): Brightness value (0–102, default 0).
        startup (int): Startup mode (0=default, 1=play image, 2=play video, default 0).
        reserved (int): Reserved value (default 0).
        rotation (int): Screen rotation (0=0°, 2=180°, default 0).
        sleep (int): Sleep timeout (0–255, default 0).
        offline (int): Offline mode (0=disabled, 1=enabled, default 0).

    Returns:
        The response from the device.
    """
    logger.info("Sending Save Settings Command (ID 125)...")
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

def _open_file_command(dev, path: str):
    """
    Send an Open File Command (ID 38) to open a file on the device for writing.

    Args:
        dev: The USB device.
        path (str): The path of the file on the device.

    Returns:
        The response from the device.
    """
    logger.info("Sending Open File Command (ID 38) for device path: %s", path)

    path_bytes = path.encode('ascii')  # convert to ASCII bytes
    length = len(path_bytes)
    
    packet = build_command_packet_header(38)

    packet[8] = (length >> 24) & 0xFF
    packet[9] = (length >> 16) & 0xFF
    packet[10] = (length >> 8) & 0xFF
    packet[11] = length & 0xFF
    packet[12:16] = b'\x00\x00\x00\x00'
    packet[16:16+length] = path_bytes

    return write_to_device(dev, encrypt_command_packet(packet))

def _write_file_command(dev, file_path: str) -> bool:
    """
    Send a Write File Command (ID 39) to write the contents of a local file to
    a file previously opened on the device.

    Args:
        dev: The USB device.
        file_path (str): The local path of the file to write.

    Returns:
        bool: True if the file was written successfully, False otherwise.
    """
    logger.info("Sending Write File Command (ID 39) for local path: %s", file_path)

    CHUNK_SIZE = 1048576  # 1MB chunks
    HEADER_SIZE = 512
    TOTAL_BUFFER_SIZE = HEADER_SIZE + CHUNK_SIZE
    total_sent = 0
    last_progress = -1

    try:
        with open(file_path, 'rb') as f:
            file_size = f.seek(0, io.SEEK_END)
            f.seek(0)

            while True:
                buffer = bytearray(TOTAL_BUFFER_SIZE)
                data = f.read(CHUNK_SIZE)
                bytes_read = len(data)
                if bytes_read == 0:
                    break

                header = build_command_packet_header(39)
                header[8] = (CHUNK_SIZE >> 24) & 0xFF
                header[9] = (CHUNK_SIZE >> 16) & 0xFF
                header[10] = (CHUNK_SIZE >> 8) & 0xFF
                header[11] = CHUNK_SIZE & 0xFF

                header[12] = (bytes_read >> 24) & 0xFF
                header[13] = (bytes_read >> 16) & 0xFF
                header[14] = (bytes_read >> 8) & 0xFF
                header[15] = bytes_read & 0xFF

                if f.tell() == file_size:
                    header[16] = 1  # Last chunk flag

                buffer[0:bytes_read] = data

                response = write_to_device(dev, encrypt_command_packet(header)+buffer)
                if not response:
                    logger.error("Failed to send chunk.")
                    return False

                total_sent += bytes_read
                progress = int(total_sent / file_size * 100)
                if progress != last_progress:
                    last_progress = progress
                    logger.info("Upload progress: %d%%", progress)

        return True

    except Exception as e:
        logger.error("Error during upload: %s", e)
        return False

def _delete_command(dev, file_path: str):
    """
    Send a Delete Command (ID 42) to delete a file on the device.

    Args:
        dev: The USB device.
        file_path (str): The path of the file on the device to delete.

    Returns:
        The response from the device.
    """
    logger.info("Sending Delete Command (ID 42) for path: %s", file_path)

    path_bytes = file_path.encode('ascii')
    length = len(path_bytes)
    
    packet = build_command_packet_header(42)

    packet[8] = (length >> 24) & 0xFF
    packet[9] = (length >> 16) & 0xFF
    packet[10] = (length >> 8) & 0xFF
    packet[11] = length & 0xFF
    packet[12:16] = b'\x00\x00\x00\x00'
    packet[16:16+length] = path_bytes

    return write_to_device(dev, encrypt_command_packet(packet))

def _play_command(dev, file_path: str):
    """
    Send a Play Command (ID 98) to play a file on the device.

    Args:
        dev: The USB device.
        file_path (str): The path of the file on the device to play.

    Returns:
        The response from the device.
    """
    logger.info("Sending Play Command (ID 98) for path: %s", file_path)

    path_bytes = file_path.encode('ascii')
    length = len(path_bytes)
    
    packet = build_command_packet_header(98)

    packet[8] = (length >> 24) & 0xFF
    packet[9] = (length >> 16) & 0xFF
    packet[10] = (length >> 8) & 0xFF
    packet[11] = length & 0xFF
    packet[12:16] = b'\x00\x00\x00\x00'
    packet[16:16+length] = path_bytes

    return write_to_device(dev, encrypt_command_packet(packet))

def _play2_command(dev, file_path: str):
    """
    Send an alternative Play Command (ID 110) to play a file on the device.

    Args:
        dev: The USB device.
        file_path (str): The path of the file on the device to play.

    Returns:
        The response from the device.
    """
    logger.info("Sending Play#2 Command (ID 110) for path: %s", file_path)

    path_bytes = file_path.encode('ascii')
    length = len(path_bytes)
    
    packet = build_command_packet_header(110)

    packet[8] = (length >> 24) & 0xFF
    packet[9] = (length >> 16) & 0xFF
    packet[10] = (length >> 8) & 0xFF
    packet[11] = length & 0xFF
    packet[12:16] = b'\x00\x00\x00\x00'
    packet[16:16+length] = path_bytes

    return write_to_device(dev, encrypt_command_packet(packet))

def _play3_command(dev, file_path: str):
    """
    Send an alternative Play Command (ID 113) to play a file on the device.

    Args:
        dev: The USB device.
        file_path (str): The path of the file on the device to play.

    Returns:
        The response from the device.
    """
    logger.info("Sending Play#3 Command (ID 113) for path: %s", file_path)

    path_bytes = file_path.encode('ascii')
    length = len(path_bytes)
    
    packet = build_command_packet_header(113)

    packet[8] = (length >> 24) & 0xFF
    packet[9] = (length >> 16) & 0xFF
    packet[10] = (length >> 8) & 0xFF
    packet[11] = length & 0xFF
    packet[12:16] = b'\x00\x00\x00\x00'
    packet[16:16+length] = path_bytes

    return write_to_device(dev, encrypt_command_packet(packet))

def stop_play(dev):
    """
    Send Stop Play Commands (ID 111 and ID 114) to stop playback on the device.

    Args:
        dev: The USB device.

    Returns:
        bool: True if the commands were sent successfully.
    """
    logger.info("Sending Stop Play#1 Command (ID 111)")
    cmd_packet = build_command_packet_header(111)
    write_to_device(dev, encrypt_command_packet(cmd_packet))

    logger.info("Sending Stop Play#2 Command (ID 114)")
    cmd_packet = build_command_packet_header(114)
    write_to_device(dev, encrypt_command_packet(cmd_packet))

    return True

def send_image(dev, image_path, max_chunk_bytes=524288):
    """
    Send an image to the device for display.

    Args:
        dev: The USB device.
        image_path (str): The path of the image to send.
        max_chunk_bytes (int): Maximum size of each chunk to send.

    Returns:
        bool: True if the image was sent successfully.
    """
    try:
        with Image.open(image_path) as img:
            img = img.convert("RGBA")
            width, height = img.size

            if (width, height) != (480, 1920):
                logger.warning("Image resolution is %dx%d, not 480x1920 (device screen resolution).", width, height)

            total_size = len(_encode_png(img))
            num_layers = math.ceil(total_size / max_chunk_bytes)
            logger.info("Image size: %d bytes → split into %d layers", total_size, num_layers)

            h = height // num_layers

            results = []

            for i in range(num_layers):
                y_start = height - (i + 1) * h
                y_start = max(0, y_start)

                visible_part = img.crop((0, y_start, width, height - h * i))

                canvas_height = height - i * h
                layer_img = Image.new("RGBA", (width, canvas_height), (0, 0, 0, 0))
                layer_img.paste(visible_part, (0, y_start))

                label = f"layer_{i+1} ({width}x{canvas_height}) shows Y={y_start}-{height - h * i}"
                logger.info("Sending %s...", label)

                encoded = _encode_png(layer_img)
                results.append(_send_png_bytes(dev, encoded, part=label))

            return all(results)
    except Exception as e:
        logger.error("Error sending image: %s", e)
        return False

def _encode_png(image: Image.Image) -> bytes:
    """
    Encode an image as a PNG.

    Args:
        image (Image.Image): The image to encode.

    Returns:
        bytes: The encoded PNG data.
    """
    buffer = io.BytesIO()
    image.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()

def _send_png_bytes(dev, img_data, part=None):
    """
    Send PNG image data to the device.

    Args:
        dev: The USB device.
        img_data (bytes): The PNG image data.
        part (str, optional): Description of the image part being sent.

    Returns:
        The response from the device.
    """
    img_size = len(img_data)
    cmd_packet = build_command_packet_header(102)
    cmd_packet[8] = (img_size >> 24) & 0xFF
    cmd_packet[9] = (img_size >> 16) & 0xFF
    cmd_packet[10] = (img_size >> 8) & 0xFF
    cmd_packet[11] = img_size & 0xFF
    logger.info("→ Transmitting [%s] - %d bytes", part or "image", img_size)
    full_payload = encrypt_command_packet(cmd_packet) + img_data
    return write_to_device(dev, full_payload)

def clear_image(dev):
    """
    Clear the current image on the device by sending a minimal PNG.

    Args:
        dev: The USB device.

    Returns:
        The response from the device.
    """
    # This is a minimal transparent PNG that clears the screen
    img_data = bytearray([
        0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a, 0x00, 0x00, 0x00, 0x0d, 0x49, 0x48, 0x44, 0x52,
        0x00, 0x00, 0x01, 0xe0, 0x00, 0x00, 0x07, 0x80, 0x08, 0x06, 0x00, 0x00, 0x00, 0x16, 0xf0, 0x84,
        0xf5, 0x00, 0x00, 0x00, 0x01, 0x73, 0x52, 0x47, 0x42, 0x00, 0xae, 0xce, 0x1c, 0xe9, 0x00, 0x00,
        0x00, 0x04, 0x67, 0x41, 0x4d, 0x41, 0x00, 0x00, 0xb1, 0x8f, 0x0b, 0xfc, 0x61, 0x05, 0x00, 0x00,
        0x00, 0x09, 0x70, 0x48, 0x59, 0x73, 0x00, 0x00, 0x0e, 0xc3, 0x00, 0x00, 0x0e, 0xc3, 0x01, 0xc7,
        0x6f, 0xa8, 0x64, 0x00, 0x00, 0x0e, 0x0c, 0x49, 0x44, 0x41, 0x54, 0x78, 0x5e, 0xed, 0xc1, 0x01,
        0x0d, 0x00, 0x00, 0x00, 0xc2, 0xa0, 0xf7, 0x4f, 0x6d, 0x0f, 0x07, 0x14, 0x00, 0x00, 0x00, 0x00,
    ] + [0x00] * 3568 + [
        0x00, 0xf0, 0x66, 0x4a, 0xc8, 0x00, 0x01, 0x11, 0x9d, 0x82, 0x0a, 0x00, 0x00, 0x00, 0x00, 0x49,
        0x45, 0x4e, 0x44, 0xae, 0x42, 0x60, 0x82
    ])
    img_size = len(img_data)
    logger.info("Sending Clear Image Command (ID 102) - %d bytes", img_size)

    cmd_packet = build_command_packet_header(102)
    cmd_packet[8] = (img_size >> 24) & 0xFF
    cmd_packet[9] = (img_size >> 16) & 0xFF
    cmd_packet[10] = (img_size >> 8) & 0xFF
    cmd_packet[11] = img_size & 0xFF

    full_payload = encrypt_command_packet(cmd_packet) + img_data
    return write_to_device(dev, full_payload)

def delay(dev, rst):
    """
    Wait for device to be ready.

    Args:
        dev: The USB device.
        rst: Reset threshold.
    """
    time.sleep(0.05)
    logger.info("Sending Delay Command (ID 122)...")
    cmd_packet = build_command_packet_header(122)
    response = write_to_device(dev, encrypt_command_packet(cmd_packet))
    if response and response[8] > rst:
        delay(dev, rst)

def extract_h264_from_mp4(mp4_path: str):
    """
    Extract H.264 stream from an MP4 file.

    Args:
        mp4_path (str): The path of the MP4 file.

    Returns:
        Path: The path of the extracted H.264 file.

    Raises:
        FileNotFoundError: If the input file does not exist.
        subprocess.CalledProcessError: If FFmpeg fails to extract the H.264 stream.
    """
    input_path = Path(mp4_path)
    if not input_path.exists():
        raise FileNotFoundError(f"Input file not found: {input_path}")

    output_path = input_path.with_name(input_path.name + ".h264")
    
    if output_path.exists():
        logger.info("%s already exists. Skipping extraction.", output_path.name)
        return output_path

    cmd = [
        "ffmpeg",
        "-y",  # overwrite without asking
        "-i", str(input_path),  # input file
        "-c:v", "copy",  # copy video stream
        "-bsf:v", "h264_mp4toannexb",  # convert to Annex-B
        "-an",  # remove audio
        "-f", "h264",  # set output format
        str(output_path)  # output file
    ]

    logger.info("Extracting H.264 from %s...", input_path.name)
    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True)
        logger.info("Done. Saved as %s", output_path.name)
        return output_path
    except subprocess.CalledProcessError as e:
        logger.error("FFmpeg error: %s\nOutput: %s", e, e.stderr)
        raise

def send_video(dev, video_path, loop=False):
    """
    Send a video to the device for playback.

    Args:
        dev: The USB device.
        video_path (str): The path of the video to send.
        loop (bool): Whether to loop the video playback.

    Returns:
        bool: True if the video was sent successfully.
    """
    try:
        output_path = extract_h264_from_mp4(video_path)
        
        # Reset commands sequence
        write_to_device(dev, encrypt_command_packet(build_command_packet_header(111)))
        write_to_device(dev, encrypt_command_packet(build_command_packet_header(112)))
        write_to_device(dev, encrypt_command_packet(build_command_packet_header(13)))
        
        # Configure screen
        send_brightness_command(dev, 32)
        write_to_device(dev, encrypt_command_packet(build_command_packet_header(41)))
        clear_image(dev)
        send_frame_rate_command(dev, 25)
        
        logger.info("Sending video data (ID 121)...")
        
        try:
            while True:
                with open(output_path, 'rb') as f:
                    chunk_count = 0
                    while True:
                        data = f.read(202752)  # Read in ~200KB chunks
                        chunk_size = len(data)
                        if not data:
                            break
                            
                        chunk_count += 1
                        if chunk_count % 10 == 0:  # Log less frequently
                            logger.info("Sending chunk #%d (%d bytes)", chunk_count, chunk_size)

                        cmd_packet = build_command_packet_header(121)
                        cmd_packet[8] = (chunk_size >> 24) & 0xFF
                        cmd_packet[9] = (chunk_size >> 16) & 0xFF
                        cmd_packet[10] = (chunk_size >> 8) & 0xFF
                        cmd_packet[11] = chunk_size & 0xFF

                        full_payload = encrypt_command_packet(cmd_packet) + data
                        response = write_to_device(dev, full_payload)
                        time.sleep(0.03)  # Small delay between chunks
                        
                        # Check response status and wait if needed
                        if response is None or len(response) < 9 or response[8] <= 3:
                            delay(dev, 2)
                            
                    logger.info("Video sent successfully (%d chunks)", chunk_count)
                
                if not loop:
                    break
                logger.info("Looping video...")
                
        except KeyboardInterrupt:
            logger.info("\nLoop interrupted by user. Sending reset...")
        
        # Reset after playback
        write_to_device(dev, encrypt_command_packet(build_command_packet_header(123)))
        return True
        
    except Exception as e:
        logger.error("Error sending video: %s", e)
        return False

def main():
    """
    Main function that parses command-line arguments and performs the requested action.
    """
    parser = argparse.ArgumentParser(
        description="Turing Smart Screen CLI Tool - Control your Turing Smart Screen device via USB",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Examples:\n"
               "  python turing_screen_cli.py send-image --path sample.png\n"
               "  python turing_screen_cli.py brightness --value 80\n"
               "  python turing_screen_cli.py save --brightness 100 --rotation 0\n"
               "  python turing_screen_cli.py list-storage --type image"
    )
    subparsers = parser.add_subparsers(dest="command", required=True, help="Command to execute")

    # Simple commands with no arguments
    subparsers.add_parser("sync", help="Send Sync Command (ID 10)")
    subparsers.add_parser("restart", help="Send Restart Device Command (ID 11)")
    subparsers.add_parser("refresh-storage", help="Get storage information (ID 100)")
    subparsers.add_parser("clear-image", help="Clear the current image (ID 102)")
    subparsers.add_parser('stop-play', help="Stop playback (ID 111 & ID 114)")

    # Commands with arguments
    brightness_parser = subparsers.add_parser("brightness", help="Set screen brightness (ID 14)")
    brightness_parser.add_argument("--value", type=int, required=True, choices=range(0, 103),
                                   metavar="[0-102]", help="Brightness value (0–102)")

    save_parser = subparsers.add_parser("save", help="Save device settings (ID 125)")
    save_parser.add_argument("--brightness", type=int, default=102, choices=range(0, 103), metavar="[0-102]",
                             help="Brightness value (0-102, default: 102)")
    save_parser.add_argument("--startup", type=int, default=0, choices=[0, 1, 2], metavar="[0|1|2]",
                             help="0 = default, 1 = play image, 2 = play video (default: 0)")
    save_parser.add_argument("--reserved", type=int, default=0, choices=[0], metavar="[0]",
                             help="Reserved value (default: 0)")
    save_parser.add_argument("--rotation", type=int, default=0, choices=[0, 2], metavar="[0|2]",
                             help="0 = 0°, 2 = 180° (default: 0)")
    save_parser.add_argument("--sleep", type=int, default=0, choices=range(0, 256), metavar="[0-255]",
                             help="Sleep timeout (default: 0)")
    save_parser.add_argument("--offline", type=int, default=0, choices=[0, 1], metavar="[0|1]",
                             help="0 = Disabled, 1 = Enabled (default: 0)")
    
    list_parser = subparsers.add_parser("list-storage", help="List files stored on the device (ID 99)")
    list_parser.add_argument('--type', type=str, choices=['image', 'video'], required=True,
                            help="Type of files to list: image or video")

    image_parser = subparsers.add_parser('send-image', help="Display an image on the screen (ID 102)")
    image_parser.add_argument('--path', type=str, required=True, help='Path to PNG image (ideally 480x1920)')

    parser_video = subparsers.add_parser('send-video', help="Play a video on the screen (ID 121)")
    parser_video.add_argument('--path', type=str, required=True, help='Path to MP4 video file')
    parser_video.add_argument('--loop', action='store_true', help='Loop the video playback until interrupted')

    upload_parser = subparsers.add_parser('upload', help="Upload PNG or MP4 file to device storage")
    upload_parser.add_argument('--path', type=str, required=True, help='Path to .png or .mp4 file')

    delete_parser = subparsers.add_parser('delete', help="Delete a file from device storage")
    delete_parser.add_argument('--filename', type=str, required=True, help='.png or .h264 filename to delete')

    play_parser = subparsers.add_parser('play-select', help="Play a stored file from device storage")
    play_parser.add_argument('--filename', type=str, required=True, help='.png or .h264 filename to play')

    args = parser.parse_args()

    try:
        # Find and configure the USB device
        dev = find_usb_device()
        
        # Dispatch to the appropriate command
        if args.command == "sync":
            send_sync_command(dev)
        elif args.command == "restart":
            delay_sync(dev)
            send_restart_device_command(dev)
        elif args.command == "refresh-storage":
            delay_sync(dev)
            send_refresh_storage_command(dev)
        elif args.command == "brightness":
            delay_sync(dev)
            send_brightness_command(dev, args.value)
        elif args.command == "save":
            delay_sync(dev)
            send_save_settings_command(
                dev,
                brightness=args.brightness,
                startup=args.startup,
                reserved=args.reserved,
                rotation=args.rotation,
                sleep=args.sleep,
                offline=args.offline
            )
        elif args.command == "list-storage":
            delay_sync(dev)
            path = "/tmp/sdcard/mmcblk0p1/img/" if args.type == "image" else "/tmp/sdcard/mmcblk0p1/video/"
            send_list_storage_command(dev, path)
        elif args.command == 'clear-image':
            delay_sync(dev)
            clear_image(dev)
        elif args.command == 'send-image':
            delay_sync(dev)
            send_image(dev, args.path)
        elif args.command == 'send-video':
            delay_sync(dev)
            send_video(dev, args.path, loop=args.loop)
        elif args.command == 'upload':
            delay_sync(dev)
            send_refresh_storage_command(dev)
            upload_file(dev, args.path)
        elif args.command == 'delete':
            delay_sync(dev)
            delete_file(dev, args.filename)
        elif args.command == 'stop-play':
            delay_sync(dev)
            stop_play(dev)
        elif args.command == 'play-select':
            # Execute sequence of commands to properly play stored files
            path_obj = Path(args.filename)
            ext = path_obj.suffix.lower()
            delay_sync(dev)
            stop_play(dev) # 111 114
            send_brightness_command(dev, 32) # 14

            if ext == ".h264":
                play_file(dev, args.filename) # 98
            
            cmd_packet = build_command_packet_header(111)
            write_to_device(dev, encrypt_command_packet(cmd_packet))
            cmd_packet = build_command_packet_header(112)
            write_to_device(dev, encrypt_command_packet(cmd_packet))
            clear_image(dev)

            if ext == ".h264":
                play_file2(dev, args.filename) # 110
            elif ext == ".png":
                play_file3(dev, args.filename) # 113
            else:
                logger.error("Error: Unsupported file type. Only .png and .h264 are allowed.")

            logger.info("File playback complete.")
    except ValueError as e:
        logger.error("Error: %s", e)
        sys.exit(1)
    except KeyboardInterrupt:
        logger.info("Operation cancelled by user")
        sys.exit(0)
    except Exception as e:
        logger.error("Unexpected error: %s", e)
        sys.exit(1)

if __name__ == "__main__":
    main()