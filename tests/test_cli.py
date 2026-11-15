import datetime
import json

import pytest

import turingscreencli.cli as cli
from turingscreencli import transport


def _noop(*_args, **_kwargs):
    return None


def _run(monkeypatch, argv, factory=None, **ops):
    """Run the CLI with operations replaced by stubs; the device is a plain object."""
    monkeypatch.setattr(cli, "configure_logging", _noop)
    monkeypatch.setattr(cli.operations, "delay_sync", _noop)
    monkeypatch.setattr(cli.operations, "supports", lambda dev, feature: True)
    for name, fn in ops.items():
        monkeypatch.setattr(cli.operations, name, fn)
    return cli.run(argv, device_factory=factory or (lambda: object()))


# Every subcommand with a minimal argv, the operation it must call, and the arguments it must pass.
CASES = {
    "info": (["info"], "read_device_info", {"firmware": "x"}, lambda c: True),
    "sync": (["sync"], "send_sync_command", b"ok", lambda c: True),
    "restart": (["restart"], "send_restart_device_command", b"ok", lambda c: True),
    "refresh-storage": (["refresh-storage"], "send_refresh_storage_command", {"total_kb": 1}, lambda c: True),
    "clear-image": (["clear-image"], "clear_image", b"ok", lambda c: True),
    "stop-play": (["stop-play"], "stop_play", True, lambda c: True),
    "brightness": (["brightness", "--value", "60"], "send_brightness_command", b"ok", lambda c: c[0][1] == 60),
    "frame-rate": (["frame-rate", "--value", "30"], "send_frame_rate_command", b"ok", lambda c: c[0][1] == 30),
    "rotate": (["rotate", "--value", "2"], "set_rotation", b"ok", lambda c: c[0][1] == 2),
    "save": (
        ["save", "--brightness", "50", "--rotation", "2", "--clock-direction", "1"],
        "send_save_settings_command",
        b"ok",
        lambda c: c[1]["brightness"] == 50 and c[1]["rotation"] == 2 and c[1]["clock_direction"] == 1,
    ),
    "list-storage": (["list-storage", "--type", "video"], "send_list_storage_command", ["a.h264"],
                     lambda c: c[0][1] == cli.operations.VIDEO_DIR),
    "file-size": (["file-size", "--filename", "a.png"], "file_size", 12, lambda c: c[0][1] == "a.png"),
    "send-image": (["send-image", "--path", "a.png", "--format", "jpeg", "--quality", "70"], "send_image", True,
                   lambda c: c[1]["fmt"] == "jpeg" and c[1]["quality"] == 70),
    "send-video": (["send-video", "--path", "a.mp4", "--frame-rate", "24", "--rotation", "2"], "send_video", True,
                   lambda c: c[1]["frame_rate"] == 24 and c[1]["rotation"] == 2 and c[1]["brightness"] is None),
    "upload": (["upload", "--path", "a.png"], "upload_file", True, lambda c: c[0][1] == "a.png"),
    "delete": (["delete", "--filename", "a.png"], "delete_file", True, lambda c: c[0][1] == "a.png"),
    "play-select": (["play-select", "--filename", "a.png", "--brightness", "40"], "play_stored_asset", True,
                    lambda c: c[1]["brightness"] == 40),
    "voltage": (["voltage"], "read_voltage", 5000, lambda c: True),
    "orientation": (["orientation"], "read_orientation", {"orientation": 1}, lambda c: True),
    "block-size": (["block-size"], "read_block_size", 0, lambda c: True),
    "set-clock": (["set-clock", "--time", "2026-11-16T02:05:09", "--mode", "1"], "set_clock", b"ok",
                  lambda c: c[0][1] == datetime.datetime(2026, 11, 16, 2, 5, 9) and c[1]["mode"] == 1),
    "run-state": (["run-state", "off"], "set_run_state", b"ok", lambda c: c[0][1] is False),
    "power-off": (["power-off", "--yes"], "power_off", b"ok", lambda c: True),
}


def test_cases_cover_every_command():
    assert set(CASES) == set(cli.COMMANDS)


@pytest.mark.parametrize("name", sorted(CASES))
def test_every_command_calls_its_operation(monkeypatch, name):
    argv, op, result, check = CASES[name]
    calls = []

    def stub(*args, **kwargs):
        calls.append((args, kwargs))
        return result

    extra = {"send_refresh_storage_command": stub} if name == "upload" else {}
    assert _run(monkeypatch, argv, **{op: stub}, **extra) == 0
    mine = [c for c in calls if c[0] and c[0][0] is not None]
    assert mine and check(mine[-1]), mine


@pytest.mark.parametrize("name", sorted(CASES))
def test_every_command_fails_when_the_operation_fails(monkeypatch, name):
    argv, op, _result, _check = CASES[name]
    assert _run(monkeypatch, argv, **{op: lambda *a, **k: None}) == 1


def test_every_subcommand_has_a_handler():
    parser = cli.create_parser()
    sub = next(a for a in parser._actions if a.dest == "command")
    assert sub.choices is not None
    assert set(sub.choices) == set(cli.COMMANDS)


def test_every_gated_command_names_a_known_feature():
    for name, (_handler, _sync, feature) in cli.COMMANDS.items():
        assert feature is None or feature in cli.operations.FIRMWARE_FEATURES, name


def test_device_missing(monkeypatch):
    def factory():
        raise ValueError("missing device")

    assert _run(monkeypatch, ["sync"], factory=factory) == 1


def test_transport_error_exits_1(monkeypatch):
    def broken(dev):
        raise transport.TransportError("USB write failed")

    assert _run(monkeypatch, ["sync"], send_sync_command=broken) == 1


def test_ctrl_c_exits_130(monkeypatch):
    def interrupted(*a, **k):
        raise KeyboardInterrupt

    assert _run(monkeypatch, ["upload", "--path", "a.png"], send_refresh_storage_command=lambda d: {"x": 1},
                upload_file=interrupted) == 130


def test_power_off_without_yes_does_not_open_the_device(monkeypatch):
    opened = []
    with pytest.raises(SystemExit) as e:
        _run(monkeypatch, ["power-off"], factory=lambda: opened.append(1))
    assert e.value.code == 2 and opened == []


def test_bad_clock_time_is_rejected_before_the_device(monkeypatch):
    opened = []
    with pytest.raises(SystemExit) as e:
        _run(monkeypatch, ["set-clock", "--time", "garbage"], factory=lambda: opened.append(1))
    assert e.value.code == 2 and opened == []


def test_brightness_range_is_0_to_100():
    parser = cli.create_parser()
    parser.parse_args(["brightness", "--value", "100"])
    with pytest.raises(SystemExit):
        parser.parse_args(["brightness", "--value", "101"])


def test_unsupported_feature_is_not_sent(monkeypatch, caplog):
    sent = []
    rc = _run(monkeypatch, ["voltage"], read_voltage=lambda dev: sent.append(1) or 0,
              supports=lambda dev, f: False, unsupported_reason=lambda dev, f: "not supported by firmware 8 (x)")
    assert rc == 1 and sent == []
    assert "not supported by firmware 8" in caplog.text and "--force" in caplog.text


@pytest.mark.parametrize("argv", [["--force", "voltage"], ["voltage", "--force"]])
def test_force_before_or_after_the_command(monkeypatch, argv):
    sent = []
    rc = _run(monkeypatch, argv, read_voltage=lambda dev: sent.append(1) or 0, supports=lambda dev, f: False)
    assert rc == 0 and sent == [1]


def test_results_on_stdout_diagnostics_on_stderr(monkeypatch, capsys):
    import logging

    monkeypatch.setattr(logging.root, "handlers", [])  # as when run from a shell (pytest adds its own)
    monkeypatch.setattr(cli.operations, "delay_sync", _noop)

    def listing(dev, path):
        cli.operations.logger.warning("a diagnostic")
        return ["a.h264", "b.h264"]

    monkeypatch.setattr(cli.operations, "send_list_storage_command", listing)
    assert cli.run(["list-storage", "--type", "video"], device_factory=lambda: object()) == 0
    out, err = capsys.readouterr()
    assert out == "a.h264\nb.h264\n"
    assert "a diagnostic" in err


def test_info_json(monkeypatch, capsys):
    info = {"firmware": "turzx_0001_0015", "firmware_version": 15}
    assert _run(monkeypatch, ["info", "--json"], read_device_info=lambda dev: info) == 0
    assert json.loads(capsys.readouterr().out) == info


def test_orientation_none_exits_1(monkeypatch):
    assert _run(monkeypatch, ["orientation"], read_orientation=lambda dev: None) == 1
