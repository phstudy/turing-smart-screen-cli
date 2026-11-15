"""Low-level USB transport helpers for the Turing Smart Screen."""

from __future__ import annotations

import logging
import platform
import struct
import time
from functools import partial
from typing import Any, cast

import usb.core
import usb.util
from Crypto.Cipher import DES

logger = logging.getLogger(__name__)

VENDOR_ID = 0x1CBE
PRODUCT_ID = 0x0088
_DES_KEY = b"slv3tuzx"


def _endpoint_matches_direction(endpoint, *, direction):
    return usb.util.endpoint_direction(endpoint.bEndpointAddress) == direction


def build_command_packet_header(command_id: int) -> bytearray:
    """Build a command packet header for the provided command id."""
    packet = bytearray(500)
    packet[0] = command_id
    packet[2] = 0x1A
    packet[3] = 0x6D
    timestamp = int((time.time() - time.mktime(time.localtime()[:3] + (0, 0, 0, 0, 0, -1))) * 1000)
    packet[4:8] = struct.pack("<I", timestamp)
    return packet


def encrypt_with_des(key: bytes, data: bytes | bytearray) -> bytes:
    """DES-CBC with IV = key and PKCS7 padding, as .NET's DESCryptoServiceProvider in the vendor app
    (a 500-byte header becomes 504 bytes ending 04 04 04 04)."""
    cipher = DES.new(key, DES.MODE_CBC, key)
    pad = 8 - len(data) % 8
    return cipher.encrypt(bytes(data) + bytes([pad]) * pad)


def encrypt_command_packet(data: bytearray) -> bytearray:
    encrypted = encrypt_with_des(_DES_KEY, data)
    final_packet = bytearray(512)
    final_packet[: len(encrypted)] = encrypted
    final_packet[510] = 161
    final_packet[511] = 26
    return final_packet


def find_usb_device():
    # find() without find_all returns one Device or None (its stubs also allow a generator).
    dev = cast(Any, usb.core.find(idVendor=VENDOR_ID, idProduct=PRODUCT_ID))
    if dev is None:
        raise ValueError("USB device not found")

    try:
        dev.set_configuration()
    except usb.core.USBError as exc:
        logger.warning("set_configuration() failed: %s", exc)

    if platform.system() == "Linux":
        try:
            if dev.is_kernel_driver_active(0):
                dev.detach_kernel_driver(0)
        except usb.core.USBError as exc:
            logger.warning("detach_kernel_driver failed: %s", exc)

    return dev


def read_flush(ep_in, max_attempts: int = 5) -> None:
    for _ in range(max_attempts):
        try:
            ep_in.read(512, timeout=100)
        except usb.core.USBError as exc:
            if exc.errno == 110 or exc.args[0] == "Operation timed out":
                break
            break


class TransportError(RuntimeError):
    """A command could not be written to the panel."""


def write_to_device(dev, data, timeout: int = 2000):
    """Write one command (packet plus any data) and return the 512-byte reply.

    Returns None when the panel does not answer within `timeout` ms; raises TransportError when the
    write itself fails.
    """
    cfg = dev.get_active_configuration()
    intf = usb.util.find_descriptor(cfg, bInterfaceNumber=0)
    if intf is None:
        raise RuntimeError("USB interface 0 not found")

    ep_out: Any = usb.util.find_descriptor(
        intf,
        custom_match=partial(
            _endpoint_matches_direction,
            direction=usb.util.ENDPOINT_OUT,
        ),
    )
    ep_in: Any = usb.util.find_descriptor(
        intf,
        custom_match=partial(
            _endpoint_matches_direction,
            direction=usb.util.ENDPOINT_IN,
        ),
    )
    if ep_out is None or ep_in is None:
        raise RuntimeError("Unable to locate USB endpoints")

    try:
        ep_out.write(data, timeout)
    except usb.core.USBError as exc:
        # The command never reached the panel: an error, not "no reply" (callers may accept no reply).
        raise TransportError(f"USB write failed: {exc}") from exc

    try:
        response = ep_in.read(512, timeout)
        read_flush(ep_in)
        return bytes(response)
    except usb.core.USBError as exc:
        logger.debug("No reply: %s", exc)
        return None
