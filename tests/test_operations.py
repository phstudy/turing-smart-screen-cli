"""Packet-level tests: what each operation writes to the device.

The vendor app builds a 500-byte header
[command, 0, 0x1A, 0x6D, timestamp u32 LE, payload...], DES-CBC encrypts it with
key = IV = b"slv3tuzx" and sends it in a 512-byte packet ending 0xA1 0x1A.
"""

import pytest

from turingscreencli import operations, transport

DES = pytest.importorskip("Crypto.Cipher.DES")
if not hasattr(DES, "new") or getattr(DES, "MODE_CBC", None) != 2:
    pytest.skip("real pycryptodome needed to decrypt packets", allow_module_level=True)

KEY = b"slv3tuzx"


def decode(packet: "bytes | bytearray") -> bytes:
    """Decrypt the 512-byte command packet back to its 500-byte header."""
    assert len(packet) >= 512
    assert packet[510:512] == b"\xa1\x1a"
    return DES.new(KEY, DES.MODE_CBC, KEY).decrypt(bytes(packet[:504]))[:500]


class Recorder:
    """Stands in for transport.write_to_device: records writes, returns canned replies."""

    def __init__(self, replies=None, default=b"\x00" * 512):
        self.writes = []
        self.replies = list(replies or [])
        self.default = default

    def __call__(self, dev, data, timeout=2000):
        self.writes.append(bytes(data))
        return self.replies.pop(0) if self.replies else self.default

    def headers(self):
        return [decode(w[:512]) for w in self.writes]

    def commands(self):
        return [h[0] for h in self.headers()]


@pytest.fixture
def rec(monkeypatch):
    r = Recorder()
    monkeypatch.setattr(operations, "write_to_device", r)
    monkeypatch.setattr(operations.time, "sleep", lambda s: None)
    monkeypatch.setattr(operations, "_FIRMWARE_CACHE", {})
    return r


def test_header_layout():
    header = transport.build_command_packet_header(14)
    assert len(header) == 500
    assert header[:4] == bytes([14, 0, 0x1A, 0x6D])
    assert 0 <= int.from_bytes(header[4:8], "little") < 86_400_000  # ms since local midnight
    assert header[8:] == bytes(492)
    packet = transport.encrypt_command_packet(header)
    assert len(packet) == 512
    assert decode(packet) == bytes(header)


def test_brightness_payload(rec):
    operations.send_brightness_command(None, 77)
    (h,) = rec.headers()
    assert h[0] == 14 and h[8] == 77


def test_frame_rate_payload(rec):
    operations.send_frame_rate_command(None, 25)
    (h,) = rec.headers()
    assert h[0] == 15 and h[8] == 25


def test_save_settings_payload(rec):
    operations.send_save_settings_command(None, brightness=90, startup=1, rotation=2, sleep=30, offline=1)
    (h,) = rec.headers()
    assert h[0] == 125
    assert list(h[8:14]) == [90, 1, 0, 2, 30, 1]


def test_delete_uses_42_with_big_endian_path_length(rec):
    # 40 is "save a small file" (no reply on firmware 0008); 42 is delete and answers [1] == 200.
    operations.delete_file(None, "a.png")
    (h,) = rec.headers()
    path = b"/tmp/sdcard/mmcblk0p1/img/a.png"
    assert h[0] == 42
    assert int.from_bytes(h[8:12], "big") == len(path)
    assert h[12:16] == b"\x00\x00\x00\x00"
    assert h[16 : 16 + len(path)] == path


@pytest.mark.parametrize("name", ["../x.png", "img/../../x.png", "/etc/x.png", "a/b.png", ""])
def test_device_paths_reject_anything_but_a_plain_name(rec, name):
    assert operations.delete_file(None, name) is False
    assert operations.play_file2(None, name) is False
    assert rec.writes == []


def test_play_stored_asset_keeps_brightness_unless_given(rec):
    operations.play_stored_asset(None, "a.png")
    assert 14 not in rec.commands()


def test_play_stored_asset_sets_brightness_when_given(rec):
    operations.play_stored_asset(None, "a.png", brightness=60)
    bright = [h for h in rec.headers() if h[0] == 14]
    assert len(bright) == 1 and bright[0][8] == 60


def test_upload_marks_chunk_length_and_last_chunk(rec, tmp_path, monkeypatch):
    # Firmware writes header[12..15] bytes from each chunk and closes the file when [16] == 1;
    # with only [8..11] set the file on the card stayed 0 bytes (seen on firmware 0008).
    monkeypatch.setattr(operations, "UPLOAD_CHUNK_BYTES", 10)
    f = tmp_path / "pic.png"
    f.write_bytes(b"p" * 25)
    assert operations.upload_file(None, str(f)) is True
    heads = rec.headers()
    assert heads[0][0] == 38 and heads[0][16:16 + 27] == b"/tmp/sdcard/mmcblk0p1/img/pic.png"[:27]
    chunks = [h for h in heads if h[0] == 39]
    assert [int.from_bytes(h[8:12], "big") for h in chunks] == [10, 10, 5]
    assert [int.from_bytes(h[12:16], "big") for h in chunks] == [10, 10, 5]
    assert [h[16] for h in chunks] == [0, 0, 1]
    assert [len(w) - 512 for w in rec.writes if decode(w[:512])[0] == 39] == [10, 10, 5]


def test_play_select_does_not_send_file_size_query(rec):
    # 98 is "get file size", not play; 110 plays a stored H.264 and 113 shows a stored image.
    operations.play_stored_asset(None, "a.h264")
    assert 98 not in rec.commands() and 110 in rec.commands()
    rec.writes.clear()
    operations.play_stored_asset(None, "a.png")
    assert 98 not in rec.commands() and 113 in rec.commands()


def test_stream_throttles_only_when_queue_is_full(rec, tmp_path, monkeypatch):
    # Vendor app: after each 121 chunk, wait (poll 122) only if reply[8] > 3.
    clip = tmp_path / "c.mp4.h264"
    clip.write_bytes(b"v" * 30)
    monkeypatch.setattr(operations, "extract_h264_from_mp4", lambda p: clip)
    monkeypatch.setattr(operations, "STREAM_CHUNK_BYTES", 10)
    full, low = bytes([0] * 8 + [5] + [0] * 503), bytes([0] * 8 + [1] + [0] * 503)
    rec.default = low
    rec.replies = [low] * 4 + [full, low, low]  # 4 setup writes (111 112 102 15), chunk 1 full, 122 drains, chunk 2 ...
    assert operations.send_video(None, "c.mp4") is True
    cmds = rec.commands()
    assert 41 not in cmds
    assert cmds.count(122) == 1 and cmds.index(122) == cmds.index(121) + 1


def test_send_video_keeps_brightness_unless_given(rec, tmp_path, monkeypatch):
    clip = tmp_path / "c.mp4.h264"
    clip.write_bytes(b"\x00\x00\x00\x01" + b"x" * 100)
    monkeypatch.setattr(operations, "extract_h264_from_mp4", lambda p: clip)
    assert operations.send_video(None, str(tmp_path / "c.mp4")) is True
    assert 14 not in rec.commands()
    chunks = [h for h in rec.headers() if h[0] == 121]
    assert len(chunks) == 1 and int.from_bytes(chunks[0][8:12], "big") == 104


def test_wait_for_stream_gives_up_instead_of_recursing(rec):
    rec.default = bytes([0] * 8 + [9] + [0] * 503)  # device always reports a full buffer
    assert operations.wait_for_stream(None, 2, timeout=0.5) is False
    assert set(rec.commands()) == {122}


def test_wait_for_stream_returns_when_buffer_drains(rec):
    rec.replies = [bytes([0] * 8 + [5] + [0] * 503), bytes([0] * 8 + [1] + [0] * 503)]
    assert operations.wait_for_stream(None, 2) is True
    assert rec.commands() == [122, 122]


def test_refresh_storage_reports_failure(rec):
    rec.default = None
    assert operations.send_refresh_storage_command(None) is None


def test_refresh_storage_parses_reply(rec):
    reply = bytearray(512)
    reply[8:12] = (2048 * 1024).to_bytes(4, "little")
    reply[12:16] = (100).to_bytes(4, "little")
    reply[16:20] = (2048 * 1024 - 100).to_bytes(4, "little")
    rec.replies = [bytes(reply)]
    assert operations.send_refresh_storage_command(None) == {
        "total_kb": 2048 * 1024, "used_kb": 100, "free_kb": 2048 * 1024 - 100}


def test_list_storage_reports_failure(rec):
    rec.default = None
    assert operations.send_list_storage_command(None, "/tmp/sdcard/mmcblk0p1/img/") is None


def test_list_storage_parses_names_and_drops_nul_padding(rec):
    # As firmware 0008 answers: one reply with the names, the rest NUL padding.
    first = b"result:dir:file:AMD.mp4.h264/nyan.mp4.h264/".ljust(512, b"\0")
    rec.replies = [bytes(512)] * 8 + [first] + [bytes(512)] * 11
    assert operations.send_list_storage_command(None, "/x/") == ["AMD.mp4.h264", "nyan.mp4.h264"]


def test_list_storage_empty_directory(rec):
    rec.replies = [b"result:dir:".ljust(512, b"\0")] + [bytes(512)] * 19
    assert operations.send_list_storage_command(None, "/x/") == []


def test_stop_play_reports_failure(rec):
    rec.default = None
    assert operations.stop_play(None) is False
    assert rec.commands() == [111, 114]


def _reply(code, payload=b""):
    r = bytearray(512)
    r[0], r[1] = code, 200
    r[8 : 8 + len(payload)] = payload
    return bytes(r)


def test_device_info_reads_version_storage_and_status(rec):
    storage = b"".join(v.to_bytes(4, "little") for v in (61053344, 2848, 61050496, 0, 0, 0))
    rec.replies = [_reply(10, b"turzx_0001_0008"), _reply(100, storage), _reply(112, b"\x00"),
                   _reply(122, b"\x02")]
    info = operations.read_device_info(None)
    assert rec.commands() == [10, 100, 112, 122]  # no 240 (chip id) before firmware 15
    assert info == {
        "firmware": "turzx_0001_0008",
        "firmware_version": 8,
        "storage_total_kb": 61053344,
        "storage_used_kb": 2848,
        "storage_free_kb": 61050496,
        "stored_video_playing": False,
        "stream_queue": 2,
    }


def test_device_info_includes_chip_id_when_reported(rec):
    rec.replies = [_reply(10, b"turzx_0001_0015"), _reply(100), _reply(112), _reply(122), _reply(240, bytes(range(1, 17)))]
    info = operations.read_device_info(None)
    assert info is not None
    assert info["firmware_version"] == 15
    assert info["chip_id"] == bytes(range(1, 17)).hex()


def test_device_info_none_when_panel_silent(rec):
    rec.default = None
    assert operations.read_device_info(None) is None


def _png(tmp_path, size=(480, 1920), noise=False):
    from PIL import Image
    import os
    img = Image.frombytes("RGB", size, os.urandom(size[0] * size[1] * 3)) if noise else Image.new("RGB", size, (10, 20, 30))
    p = tmp_path / "pic.png"
    img.save(p)
    return p


def test_send_image_jpeg_uses_101_and_stays_under_limit(rec, tmp_path):
    p = _png(tmp_path, noise=True)
    assert operations.send_image(None, str(p), fmt="jpeg", max_bytes=300_000) is True
    (w,) = rec.writes
    h = decode(w[:512])
    size = int.from_bytes(h[8:12], "big")
    assert h[0] == 101 and size == len(w) - 512 <= 300_000
    assert w[512:514] == b"\xff\xd8"  # JPEG data follows the header


def test_send_image_png_default_unchanged(rec, tmp_path):
    p = _png(tmp_path)
    assert operations.send_image(None, str(p)) is True
    assert rec.commands() == [102]


def test_send_image_auto_switches_to_jpeg_only_when_png_too_big(rec, tmp_path):
    small = _png(tmp_path)
    assert operations.send_image(None, str(small), fmt="auto") is True
    assert rec.commands() == [102]
    rec.writes.clear()
    big = _png(tmp_path, noise=True)
    assert operations.send_image(None, str(big), fmt="auto", max_bytes=300_000) is True
    assert rec.commands() == [101]


def test_send_video_rotation_only_when_given(rec, tmp_path, monkeypatch):
    clip = tmp_path / "c.mp4.h264"
    clip.write_bytes(b"v" * 10)
    monkeypatch.setattr(operations, "extract_h264_from_mp4", lambda p: clip)
    operations.send_video(None, "c.mp4")
    assert 13 not in rec.commands()
    rec.writes.clear()
    operations.send_video(None, "c.mp4", rotation=2)
    (h,) = [h for h in rec.headers() if h[0] == 13]
    assert h[8] == 2


def test_list_storage_waits_for_data_then_stops_at_padding(rec):
    # Firmware 0008: 8 empty replies while it reads the card, then the names, then padding.
    rec.replies = [bytes(512)] * 8 + [b"result:dir:file:a.png/".ljust(512, b"\0"),
                                      b"b.png/".ljust(512, b"\0")] + [bytes(512)] * 10
    assert operations.send_list_storage_command(None, "/x/") == ["a.png", "b.png"]
    assert len(rec.writes) == 11


# --- commands from the vendor app, layouts as the app builds them


def test_stream_marks_last_chunk(rec, tmp_path, monkeypatch):
    clip = tmp_path / "c.mp4.h264"
    clip.write_bytes(b"v" * 25)
    monkeypatch.setattr(operations, "extract_h264_from_mp4", lambda p: clip)
    monkeypatch.setattr(operations, "STREAM_CHUNK_BYTES", 10)
    operations.send_video(None, "c.mp4")
    assert [h[12] for h in rec.headers() if h[0] == 121] == [0, 0, 1]


def test_save_settings_clock_direction(rec):
    operations.send_save_settings_command(None, brightness=80, clock_direction=1)
    (h,) = rec.headers()
    assert list(h[8:15]) == [80, 0, 0, 0, 0, 0, 1]


def test_set_rotation(rec):
    operations.set_rotation(None, 2)
    (h,) = rec.headers()
    assert h[0] == 13 and h[8] == 2


def test_file_size(rec):
    rec.replies = [_reply(98, (6809).to_bytes(4, "little"))]
    assert operations.file_size(None, "a.png") == 6809
    (h,) = rec.headers()
    path = b"/tmp/sdcard/mmcblk0p1/img/a.png"
    assert h[0] == 98 and int.from_bytes(h[8:12], "big") == len(path) and h[16 : 16 + len(path)] == path


def test_file_size_rejects_paths(rec):
    assert operations.file_size(None, "../a.png") is None
    assert rec.writes == []


def test_read_voltage(rec):
    rec.replies = [_reply(50, (5012).to_bytes(4, "big"))]
    assert operations.read_voltage(None) == 5012


def test_read_orientation(rec):
    rec.replies = [_reply(60, bytes([1, 3]))]
    assert operations.read_orientation(None) == {"sensor": 1, "orientation": 3, "name": "landscape-270"}


def test_read_orientation_unsupported(rec):
    rec.replies = [bytes(512)]  # no reply data at all
    assert operations.read_orientation(None) is None
    rec.replies = [_reply(60)]  # firmware 0008: [0]=60, [1]=200, data zeros
    assert operations.read_orientation(None) is None


def test_read_block_size(rec):
    rec.replies = [_reply(17, (524288).to_bytes(4, "big"))]
    assert operations.read_block_size(None) == 524288
    rec.replies = [_reply(17)]
    assert operations.read_block_size(None) == 0


def test_set_clock(rec):
    import datetime
    operations.set_clock(None, datetime.datetime(2026, 11, 16, 2, 5, 9), mode=1)
    (h,) = rec.headers()
    assert h[0] == 51
    assert int.from_bytes(h[8:10], "big") == 2026 and list(h[10:16]) == [11, 16, 2, 5, 9, 1]


def test_set_run_state(rec):
    operations.set_run_state(None, False)
    (h,) = rec.headers()
    assert h[0] == 49 and h[8] == 0


def test_power_off(rec):
    operations.power_off(None)
    assert rec.commands() == [12]


# --- firmware differences (operations.FIRMWARE_FEATURES)


def test_every_feature_has_a_version_and_evidence():
    for name, feature in operations.FIRMWARE_FEATURES.items():
        assert isinstance(feature.min_version, int), name
        assert feature.evidence, name


def test_supports_reads_version_once(rec):
    dev = object()
    rec.replies = [_reply(10, b"turzx_0001_0008")]
    assert operations.supports(dev, "chip_id") is False
    assert operations.supports(dev, "voltage") is False
    assert rec.commands() == [10]


def test_supports_newer_firmware(rec):
    rec.replies = [_reply(10, b"turzx_0001_0015")]
    dev = object()
    assert operations.supports(dev, "chip_id") is True
    assert operations.supports(dev, "voltage") is False  # not in 0015 either


def test_unknown_version_string_assumes_supported(rec):
    rec.replies = [_reply(10, b"something-else")]
    assert operations.supports(object(), "voltage") is True


def test_info_skips_chip_id_on_old_firmware(rec):
    rec.replies = [_reply(10, b"turzx_0001_0008"), _reply(100), _reply(112), _reply(122)]
    info = operations.read_device_info(object())
    assert info is not None and "chip_id" not in info
    assert 240 not in rec.commands()


def test_play_stored_png_without_reply_is_ok_on_0008(rec):
    dev = object()
    rec.default = None
    operations._FIRMWARE_CACHE[id(dev)] = 8
    assert operations.play_file3(dev, "a.png") is True


def test_play_stored_png_without_reply_fails_on_newer_firmware(rec):
    dev = object()
    rec.default = None
    operations._FIRMWARE_CACHE[id(dev)] = operations.LATEST_KNOWN_FIRMWARE + 1
    assert operations.play_file3(dev, "a.png") is False
