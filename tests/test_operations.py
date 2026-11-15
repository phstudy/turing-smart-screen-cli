"""Packet-level tests: what each operation writes to the panel and how it reads the replies.

The vendor app builds a 500-byte header [command, 0, 0x1A, 0x6D,
timestamp u32 LE, payload...], DES-CBC encrypts it with key = IV = b"slv3tuzx" and sends it in a
512-byte packet ending 0xA1 0x1A. The fake panel below answers like the real one (firmware 0008/0015):
[0] = command, [1] = 200, and 200 at [8] for 38 (open file).
"""

import datetime
import io

import pytest

from turingscreencli import operations, transport

DES = pytest.importorskip("Crypto.Cipher.DES")
if getattr(DES, "MODE_CBC", None) != 2:  # conftest's stand-in when pycryptodome is missing
    pytest.skip("real pycryptodome needed to decrypt packets", allow_module_level=True)

KEY = b"slv3tuzx"
SILENT = object()


def decode(packet: "bytes | bytearray") -> bytes:
    """Decrypt the 512-byte command packet back to its 500-byte header."""
    assert len(packet) >= 512
    assert packet[510:512] == b"\xa1\x1a"
    return DES.new(KEY, DES.MODE_CBC, KEY).decrypt(bytes(packet[:504]))[:500]


def reply(code, payload=b"", status=200):
    r = bytearray(512)
    r[0], r[1] = code, status
    r[8 : 8 + len(payload)] = payload
    return bytes(r)


class Panel:
    """Stands in for transport.write_to_device.

    Scripted replies (in order) win; otherwise it answers like the real panel. `silent` makes it
    not answer (None); `version` is what command 10 reports; `sizes` maps device paths to sizes (98).
    """

    def __init__(self):
        self.writes = []
        self.replies = []
        self.silent = False
        self.version = b"turzx_0001_0015"
        self.sizes = {}
        self.playing = 0

    def __call__(self, dev, data, timeout=2000):
        data = bytes(data)
        self.writes.append(data)
        if self.replies:
            r = self.replies.pop(0)
            return None if r is SILENT else r
        if self.silent:
            return None
        h = decode(data[:512])
        code = h[0]
        if code == 10:
            return reply(10, self.version)
        if code == 38:
            return reply(38, bytes([200]))
        if code == 98:
            path = h[16 : 16 + int.from_bytes(h[8:12], "big")].decode()
            return reply(98, self.sizes.get(path, 0).to_bytes(4, "little"))
        if code == 112:
            return reply(112, bytes([self.playing]))
        return reply(code)

    def headers(self):
        return [decode(w[:512]) for w in self.writes]

    def commands(self):
        return [h[0] for h in self.headers()]

    def payload_after(self, code):
        return [w[512:] for w in self.writes if decode(w[:512])[0] == code]


@pytest.fixture
def panel(monkeypatch):
    p = Panel()
    monkeypatch.setattr(operations, "write_to_device", p)
    monkeypatch.setattr(operations.time, "sleep", lambda s: None)
    monkeypatch.setattr(operations, "_FIRMWARE_CACHE", {})
    return p


def path_of(header):
    return header[16 : 16 + int.from_bytes(header[8:12], "big")]


# ---------------------------------------------------------------- framing and simple commands


def test_header_layout():
    header = transport.build_command_packet_header(14)
    assert len(header) == 500
    assert header[:4] == bytes([14, 0, 0x1A, 0x6D])
    assert 0 <= int.from_bytes(header[4:8], "little") < 86_400_000  # ms since local midnight
    assert header[8:] == bytes(492)
    packet = transport.encrypt_command_packet(header)
    assert len(packet) == 512 and decode(packet) == bytes(header)


def test_encryption_matches_dotnet_pkcs7():
    # .NET's DESCryptoServiceProvider (the vendor app) pads with PKCS7: 500 bytes + 04 04 04 04.
    header = transport.build_command_packet_header(10)
    packet = transport.encrypt_command_packet(header)
    plain = DES.new(KEY, DES.MODE_CBC, KEY).decrypt(bytes(packet[:504]))
    assert plain[500:504] == b"\x04\x04\x04\x04"
    from Crypto.Util.Padding import pad

    assert bytes(packet[:504]) == DES.new(KEY, DES.MODE_CBC, KEY).encrypt(pad(bytes(header), 8))
    assert packet[504:510] == bytes(6)


@pytest.mark.parametrize(
    "call, code, byte8",
    [
        (lambda: operations.send_brightness_command(None, 77), 14, 77),
        (lambda: operations.send_frame_rate_command(None, 25), 15, 25),
        (lambda: operations.set_rotation(None, 2), 13, 2),
        (lambda: operations.set_run_state(None, False), 49, 0),
        (lambda: operations.set_run_state(None, True), 49, 1),
    ],
)
def test_one_byte_commands(panel, call, code, byte8):
    call()
    (h,) = panel.headers()
    assert h[0] == code and h[8] == byte8


def test_save_settings_layout(panel):
    operations.send_save_settings_command(
        None, brightness=90, startup=1, rotation=2, sleep=30, offline=1, clock_direction=3
    )
    (h,) = panel.headers()
    assert h[0] == 125 and list(h[8:15]) == [90, 1, 0, 2, 30, 1, 3]


def test_set_clock(panel):
    operations.set_clock(None, datetime.datetime(2026, 11, 16, 2, 5, 9), mode=1)
    (h,) = panel.headers()
    assert h[0] == 51
    assert int.from_bytes(h[8:10], "big") == 2026 and list(h[10:16]) == [11, 16, 2, 5, 9, 1]


def test_power_off(panel):
    operations.power_off(None)
    assert panel.commands() == [12]


def test_delay_sync_learns_the_firmware_version(panel):
    dev = object()
    operations.delay_sync(dev)
    assert operations.firmware_version(dev) == 15
    assert panel.commands() == [10]  # no second version query


# ---------------------------------------------------------------- firmware features


def test_every_feature_is_well_formed():
    for name, f in operations.FIRMWARE_FEATURES.items():
        assert f.min_version is None or isinstance(f.min_version, int), name
        assert isinstance(f.checked_up_to, int) and f.evidence, name


def test_supports_by_version(panel, monkeypatch):
    monkeypatch.setitem(operations.FIRMWARE_FEATURES, "x_from_15", operations.Feature(15, 15, "test"))
    monkeypatch.setitem(operations.FIRMWARE_FEATURES, "x_none", operations.Feature(None, 15, "test"))
    for version, from15, none in [(8, False, False), (15, True, False), (16, True, True)]:
        operations._FIRMWARE_CACHE.clear()
        panel.version = f"turzx_0001_{version:04d}".encode()
        dev = object()
        assert operations.supports(dev, "x_from_15") is from15, version
        assert operations.supports(dev, "x_none") is none, version  # 16 is newer than checked: try it


def test_unknown_version_string_assumes_supported(panel):
    panel.version = b"something-else"
    assert operations.supports(object(), "voltage") is True


def test_failed_version_query_is_not_cached(panel):
    dev = object()
    panel.replies = [SILENT]
    assert operations.firmware_version(dev) is None
    assert operations.firmware_version(dev) == 15  # asked again
    assert panel.commands() == [10, 10]


def test_unsupported_reason_wording(panel):
    panel.version = b"turzx_0001_0008"
    dev = object()
    assert "no firmware checked so far (up to 15)" in operations.unsupported_reason(dev, "voltage")
    assert "needs firmware 15 or later" in operations.unsupported_reason(dev, "chip_id")


# ---------------------------------------------------------------- status queries


def test_device_info(panel):
    storage = b"".join(v.to_bytes(4, "little") for v in (61053344, 2848, 61050496, 0, 0, 0))
    panel.version = b"turzx_0001_0008"
    panel.replies = [reply(10, b"turzx_0001_0008"), reply(100, storage), reply(112, b"\x00"), reply(122, b"\x02")]
    info = operations.read_device_info(object())
    assert panel.commands() == [10, 100, 112, 122]  # no 240 before firmware 15
    assert info == {
        "firmware": "turzx_0001_0008",
        "firmware_version": 8,
        "storage_total_kb": 61053344,
        "storage_used_kb": 2848,
        "storage_free_kb": 61050496,
        "stored_video_playing": False,
        "stream_queue": 2,
    }


def test_device_info_chip_id_on_15(panel):
    panel.replies = [reply(10, b"turzx_0001_0015"), reply(100), reply(112), reply(122), reply(240, bytes(range(1, 17)))]
    info = operations.read_device_info(object())
    assert info is not None and info["chip_id"] == bytes(range(1, 17)).hex()


def test_device_info_none_when_panel_silent(panel):
    panel.silent = True
    assert operations.read_device_info(object()) is None


def test_refresh_storage(panel):
    panel.replies = [reply(100, b"".join(v.to_bytes(4, "little") for v in (2048, 100, 1948)))]
    assert operations.send_refresh_storage_command(None) == {"total_kb": 2048, "used_kb": 100, "free_kb": 1948}
    panel.silent = True
    assert operations.send_refresh_storage_command(None) is None


def test_read_voltage_and_block_size(panel):
    panel.replies = [reply(50, (5012).to_bytes(4, "big")), reply(17, (524288).to_bytes(4, "big"))]
    assert operations.read_voltage(None) == 5012
    assert operations.read_block_size(None) == 524288


def test_read_orientation(panel):
    panel.replies = [reply(60, bytes([1, 3]))]
    assert operations.read_orientation(None) == {"sensor": 1, "orientation": 3, "name": "landscape-270"}
    panel.replies = [reply(60)]  # firmware without a sensor: zeros
    assert operations.read_orientation(None) is None
    panel.replies = [SILENT]
    assert operations.read_orientation(None) is None


# ---------------------------------------------------------------- files


def test_file_size_and_path_layout(panel):
    path = "/tmp/sdcard/mmcblk0p1/img/a.png"
    panel.sizes[path] = 6809
    assert operations.file_size(None, "a.png") == 6809
    (h,) = panel.headers()
    assert h[0] == 98 and path_of(h) == path.encode() and h[12:16] == bytes(4)


@pytest.mark.parametrize("name", ["../x.png", "img/../../x.png", "/etc/x.png", "a/b.png", "", "x.txt"])
def test_bad_names_send_nothing(panel, name):
    assert operations.delete_file(None, name) is False
    assert operations.file_size(None, name) is None
    assert operations.play_stored_asset(None, name) is False
    assert panel.writes == []


def test_delete_uses_42_and_checks_the_reply(panel):
    assert operations.delete_file(None, "a.png") is True
    (h,) = panel.headers()
    assert h[0] == 42 and path_of(h) == b"/tmp/sdcard/mmcblk0p1/img/a.png"
    panel.replies = [reply(42, status=0)]
    assert operations.delete_file(None, "a.png") is False


def _upload(panel, monkeypatch, tmp_path, size, chunk=10):
    monkeypatch.setattr(operations, "UPLOAD_CHUNK_BYTES", chunk)
    f = tmp_path / "pic.png"
    f.write_bytes(bytes(range(256)) * (size // 256) + b"p" * (size % 256))
    panel.sizes["/tmp/sdcard/mmcblk0p1/img/pic.png"] = size
    return f


@pytest.mark.parametrize("size, chunks", [(25, [10, 10, 5]), (20, [10, 10]), (7, [7])])
def test_upload_chunks_and_last_flag(panel, monkeypatch, tmp_path, size, chunks):
    f = _upload(panel, monkeypatch, tmp_path, size)
    assert operations.upload_file(None, str(f)) is True
    heads = panel.headers()
    assert heads[0][0] == 38 and path_of(heads[0]) == b"/tmp/sdcard/mmcblk0p1/img/pic.png"
    writes = [h for h in heads if h[0] == 39]
    assert [int.from_bytes(h[8:12], "big") for h in writes] == chunks
    assert [int.from_bytes(h[12:16], "big") for h in writes] == chunks
    assert [h[16] for h in writes] == [0] * (len(chunks) - 1) + [1]
    assert b"".join(panel.payload_after(39)) == f.read_bytes()
    assert heads[-1][0] == 98  # size check after the upload


def test_upload_refuses_empty_file(panel, tmp_path):
    f = tmp_path / "empty.png"
    f.write_bytes(b"")
    assert operations.upload_file(None, str(f)) is False
    assert panel.writes == []


def test_upload_fails_when_size_on_panel_differs(panel, monkeypatch, tmp_path):
    f = _upload(panel, monkeypatch, tmp_path, 25)
    panel.sizes["/tmp/sdcard/mmcblk0p1/img/pic.png"] = 0  # what firmware 0008 showed for the old, broken upload
    assert operations.upload_file(None, str(f)) is False


def test_upload_fails_when_open_not_confirmed(panel, monkeypatch, tmp_path):
    f = _upload(panel, monkeypatch, tmp_path, 25)
    panel.replies = [reply(38, bytes([0]))]
    assert operations.upload_file(None, str(f)) is False
    assert 39 not in panel.commands()


# ---------------------------------------------------------------- listing


def _listing(*parts):
    return [p.ljust(512, b"\0") for p in parts]


def test_list_waits_for_data_then_stops_at_padding(panel):
    panel.replies = [bytes(512)] * 8 + _listing(b"result:dir:file:a.png/", b"b.png/") + [bytes(512)]
    assert operations.send_list_storage_command(None, "/x/") == ["a.png", "b.png"]
    assert len(panel.writes) == 11
    assert path_of(panel.headers()[0]) == b"/x/"


def test_list_empty_directory(panel):
    panel.replies = _listing(b"result:dir:") + [bytes(512)]
    assert operations.send_list_storage_command(None, "/x/") == []


def test_list_no_data_is_an_error_not_truncation(panel, monkeypatch, caplog):
    clock = iter(range(1000))
    monkeypatch.setattr(operations.time, "monotonic", lambda: next(clock))
    panel.replies = [bytes(512)] * 100
    assert operations.send_list_storage_command(None, "/x/", timeout=5) is None
    assert "truncated" not in caplog.text and "No listing" in caplog.text


def test_list_panel_silent(panel):
    panel.silent = True
    assert operations.send_list_storage_command(None, "/x/") is None


# ---------------------------------------------------------------- playback


def test_stop_play_polls_112(panel):
    panel.replies = [reply(111), reply(112, b"\x01"), reply(112, b"\x00")]
    assert operations.stop_play(None) is True
    assert panel.commands() == [111, 112, 112]


def test_stop_play_fails_without_reply(panel):
    panel.silent = True
    assert operations.stop_play(None) is False


def test_play_stored_video_sequence(panel):
    panel.sizes["/tmp/sdcard/mmcblk0p1/video/a.h264"] = 1000
    assert operations.play_stored_asset(object(), "a.h264") is True
    assert panel.commands() == [98, 111, 112, 102, 110]  # the vendor app's sequence
    assert path_of(panel.headers()[-1]) == b"/tmp/sdcard/mmcblk0p1/video/a.h264"
    assert 14 not in panel.commands()


def test_play_stored_video_missing(panel):
    assert operations.play_stored_asset(object(), "missing.h264") is False
    assert 110 not in panel.commands()


def test_play_stored_video_brightness_when_given(panel):
    panel.sizes["/tmp/sdcard/mmcblk0p1/video/a.h264"] = 1000
    operations.play_stored_asset(object(), "a.h264", brightness=60)
    (h,) = [h for h in panel.headers() if h[0] == 14]
    assert h[8] == 60


def test_play_stored_image_sends_113(panel):
    assert operations.play_stored_asset(object(), "a.png") is True
    assert panel.commands() == [111, 112, 102, 113]  # the vendor app's sequence
    h = panel.headers()[-1]
    assert h[0] == 113 and path_of(h) == b"/tmp/sdcard/mmcblk0p1/img/a.png"


def test_play_stored_image_no_reply_is_ok_on_known_firmware(panel, monkeypatch):
    panel.version = b"turzx_0001_0008"

    def silent_on_113(dev, data, timeout=2000):
        if decode(bytes(data)[:512])[0] == 113:
            panel.writes.append(bytes(data))
            return None
        return panel(dev, data, timeout)

    monkeypatch.setattr(operations, "write_to_device", silent_on_113)
    assert operations.play_stored_asset(object(), "a.png") is True
    assert panel.commands() == [111, 112, 102, 113, 10]  # version asked only because 113 was not answered


def test_play_stored_image_no_reply_fails_on_unchecked_firmware(panel, monkeypatch):
    panel.version = b"turzx_0001_0099"

    def silent_on_113(dev, data, timeout=2000):
        if decode(bytes(data)[:512])[0] == 113:
            return None
        return panel(dev, data, timeout)

    monkeypatch.setattr(operations, "write_to_device", silent_on_113)
    assert operations.play_stored_asset(object(), "a.png") is False


def test_play_stored_image_write_failure_is_an_error(panel, monkeypatch):
    def broken(dev, data, timeout=2000):
        raise transport.TransportError("USB write failed")

    monkeypatch.setattr(operations, "write_to_device", broken)
    with pytest.raises(transport.TransportError):
        operations.play_stored_asset(object(), "a.png")


# ---------------------------------------------------------------- images


def _png(tmp_path, size=(480, 1920), noise=False, name="pic.png"):
    import os

    from PIL import Image

    img = (
        Image.frombytes("RGB", size, os.urandom(size[0] * size[1] * 3))
        if noise
        else Image.new("RGB", size, (10, 20, 30))
    )
    p = tmp_path / name
    img.save(p)
    return p


def test_send_image_png_default(panel, tmp_path):
    assert operations.send_image(None, str(_png(tmp_path))) is True
    assert panel.commands() == [102]
    assert panel.payload_after(102)[0][:8] == b"\x89PNG\r\n\x1a\n"


def test_png_layers_cover_every_row(panel, tmp_path):
    from PIL import Image

    p = _png(tmp_path, noise=True)
    assert operations.send_image(None, str(p), max_chunk_bytes=400_000) is True
    layers = [Image.open(io.BytesIO(d)) for d in panel.payload_after(102)]
    assert len(layers) > 1
    covered = set()
    for layer in layers:
        alpha = layer.getchannel("A")
        covered |= {y for y in range(layer.height) if alpha.getpixel((0, y)) == 255}
    assert covered == set(range(1920))


def test_send_image_jpeg(panel, tmp_path):
    p = _png(tmp_path, noise=True)
    assert operations.send_image(None, str(p), fmt="jpeg", max_bytes=1_000_000) is True
    (h,) = panel.headers()
    data = panel.payload_after(101)[0]
    assert h[0] == 101 and int.from_bytes(h[8:12], "big") == len(data) <= 1_000_000
    assert data[:2] == b"\xff\xd8"


def test_send_image_auto(panel, tmp_path):
    assert operations.send_image(None, str(_png(tmp_path)), fmt="auto") is True
    assert panel.commands() == [102]
    panel.writes.clear()
    assert operations.send_image(None, str(_png(tmp_path, noise=True)), fmt="auto", max_bytes=1_000_000) is True
    assert panel.commands() == [101]


def test_clear_image_is_a_blank_png(panel):
    from PIL import Image

    operations.clear_image(None)
    (data,) = panel.payload_after(102)
    img = Image.open(io.BytesIO(data))
    assert img.size == (480, 1920) and img.getchannel("A").getextrema() == (0, 0)


# ---------------------------------------------------------------- video


@pytest.fixture
def clip(tmp_path, monkeypatch):
    h264 = tmp_path / "c.mp4.h264"
    h264.write_bytes(b"v" * 25)
    monkeypatch.setattr(operations, "extract_h264_from_mp4", lambda p: h264)
    monkeypatch.setattr(operations, "video_frame_rate", lambda p: 30)
    monkeypatch.setattr(operations, "STREAM_CHUNK_BYTES", 10)
    return h264


def test_stream_sequence(panel, clip):
    assert operations.send_video(None, "c.mp4") is True
    cmds = panel.commands()
    assert cmds[:4] == [111, 112, 102, 15] and cmds[-2:] == [123, 111]
    assert 13 not in cmds and 14 not in cmds and 41 not in cmds
    chunks = [h for h in panel.headers() if h[0] == 121]
    assert [int.from_bytes(h[8:12], "big") for h in chunks] == [10, 10, 5]
    assert [h[12] for h in chunks] == [0, 0, 1]
    assert b"".join(panel.payload_after(121)) == clip.read_bytes()


def test_stream_uses_the_video_frame_rate(panel, clip):
    operations.send_video(None, "c.mp4")
    (h,) = [h for h in panel.headers() if h[0] == 15]
    assert h[8] == 30
    panel.writes.clear()
    operations.send_video(None, "c.mp4", frame_rate=24)
    (h,) = [h for h in panel.headers() if h[0] == 15]
    assert h[8] == 24


def test_stream_rotation_only_when_given(panel, clip):
    operations.send_video(None, "c.mp4", rotation=2)
    (h,) = [h for h in panel.headers() if h[0] == 13]
    assert h[8] == 2


def test_stream_throttles_only_when_queue_is_full(panel, clip):
    full, low = reply(121, b"\x05"), reply(121, b"\x01")
    panel.replies = [reply(111), reply(112), reply(102), reply(15), full, reply(122, b"\x01"), low, low]
    assert operations.send_video(None, "c.mp4") is True
    cmds = panel.commands()
    assert cmds.count(122) == 1 and cmds.index(122) == cmds.index(121) + 1


def test_stream_gives_up_and_still_stops(panel, clip, monkeypatch):
    monkeypatch.setattr(operations, "wait_for_stream", lambda dev, n: False)
    panel.replies = [reply(111), reply(112), reply(102), reply(15), reply(121, b"\x05")]
    assert operations.send_video(None, "c.mp4") is False
    assert panel.commands()[-2:] == [123, 111]


def test_wait_for_stream(panel, monkeypatch):
    panel.replies = [reply(122, b"\x05"), reply(122, b"\x01")]
    assert operations.wait_for_stream(None, 2) is True
    clock = iter([0, 0.2, 0.4, 20])
    monkeypatch.setattr(operations.time, "monotonic", lambda: next(clock))
    panel.replies = [reply(122, b"\x09")] * 5
    assert operations.wait_for_stream(None, 2, timeout=10) is False


def test_video_frame_rate_from_ffprobe(monkeypatch):
    class R:
        stdout = '{"streams": [{"avg_frame_rate": "30000/1001", "r_frame_rate": "30000/1001"}]}'

    monkeypatch.setattr(operations.subprocess, "run", lambda *a, **k: R())
    assert operations.video_frame_rate("x.mp4") == 30

    def missing(*a, **k):
        raise FileNotFoundError("ffprobe")

    monkeypatch.setattr(operations.subprocess, "run", missing)
    assert operations.video_frame_rate("x.mp4") is None
