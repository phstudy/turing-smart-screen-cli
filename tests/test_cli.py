import turingscreencli.cli as cli


def _noop(*_args, **_kwargs):
    return None


def test_create_parser_includes_commands():
    parser = cli.create_parser()

    args = parser.parse_args(["sync"])
    assert args.command == "sync"

    args = parser.parse_args(["send-image", "--path", "img.png"])
    assert args.command == "send-image"
    assert args.path == "img.png"


def test_run_sync_success(monkeypatch):
    monkeypatch.setattr(cli, "configure_logging", _noop)

    captured = {}

    def fake_send_sync(dev):
        captured["dev"] = dev
        return b"ok"

    monkeypatch.setattr(cli.operations, "send_sync_command", fake_send_sync)

    rc = cli.run(["sync"], device_factory=lambda: object())

    assert rc == 0
    assert "dev" in captured


def test_run_device_missing(monkeypatch):
    monkeypatch.setattr(cli, "configure_logging", _noop)

    def factory():
        raise ValueError("missing device")

    rc = cli.run(["sync"], device_factory=factory)
    assert rc == 1


def test_run_upload_failure(monkeypatch):
    monkeypatch.setattr(cli, "configure_logging", _noop)
    monkeypatch.setattr(cli.operations, "delay_sync", lambda dev: None)
    monkeypatch.setattr(cli.operations, "send_refresh_storage_command", lambda dev: None)
    monkeypatch.setattr(cli.operations, "upload_file", lambda dev, path: False)

    rc = cli.run(["upload", "--path", "demo.png"], device_factory=lambda: object())

    assert rc == 1


def test_run_play_select_invalid_extension(monkeypatch):
    monkeypatch.setattr(cli, "configure_logging", _noop)
    monkeypatch.setattr(cli.operations, "play_stored_asset", lambda dev, name, brightness=None: False)

    rc = cli.run(["play-select", "--filename", "invalid.txt"], device_factory=lambda: object())

    assert rc == 1


def _run(monkeypatch, argv, **ops):
    monkeypatch.setattr(cli, "configure_logging", _noop)
    monkeypatch.setattr(cli.operations, "delay_sync", lambda dev: None)
    monkeypatch.setattr(cli.operations, "supports", lambda dev, feature: True)
    for name, fn in ops.items():
        monkeypatch.setattr(cli.operations, name, fn)
    return cli.run(argv, device_factory=lambda: object())


def test_refresh_storage_failure_exits_1(monkeypatch):
    assert _run(monkeypatch, ["refresh-storage"], send_refresh_storage_command=lambda dev: None) == 1


def test_list_storage_failure_exits_1(monkeypatch):
    assert _run(monkeypatch, ["list-storage", "--type", "image"], send_list_storage_command=lambda dev, p: None) == 1


def test_stop_play_failure_exits_1(monkeypatch):
    assert _run(monkeypatch, ["stop-play"], stop_play=lambda dev: False) == 1


def test_frame_rate_command(monkeypatch):
    sent = []
    rc = _run(monkeypatch, ["frame-rate", "--value", "30"],
              send_frame_rate_command=lambda dev, v: sent.append(v) or b"ok")
    assert rc == 0 and sent == [30]


def test_play_select_brightness_is_optional(monkeypatch):
    seen = []
    play = lambda dev, name, brightness=None: seen.append(brightness) or True  # noqa: E731
    assert _run(monkeypatch, ["play-select", "--filename", "a.png"], play_stored_asset=play) == 0
    assert _run(monkeypatch, ["play-select", "--filename", "a.png", "--brightness", "40"], play_stored_asset=play) == 0
    assert seen == [None, 40]


def test_info_prints_fields_and_json(monkeypatch, capsys):
    info = {"firmware": "turzx_0001_0008", "firmware_version": 8, "stream_queue": 0}
    assert _run(monkeypatch, ["info"], read_device_info=lambda dev: info) == 0
    out = capsys.readouterr().out
    assert "firmware: turzx_0001_0008" in out
    assert _run(monkeypatch, ["info", "--json"], read_device_info=lambda dev: info) == 0
    import json
    assert json.loads(capsys.readouterr().out) == info


def test_info_failure_exits_1(monkeypatch):
    assert _run(monkeypatch, ["info"], read_device_info=lambda dev: None) == 1


def test_send_image_format_option(monkeypatch):
    seen = []
    send = lambda dev, path, fmt="png", **kw: seen.append(fmt) or True  # noqa: E731
    assert _run(monkeypatch, ["send-image", "--path", "a.png"], send_image=send) == 0
    assert _run(monkeypatch, ["send-image", "--path", "a.png", "--format", "jpeg"], send_image=send) == 0
    assert seen == ["png", "jpeg"]


def test_send_video_rotation_option(monkeypatch):
    seen = []
    send = lambda dev, path, loop=False, brightness=None, rotation=None: seen.append(rotation) or True  # noqa: E731
    assert _run(monkeypatch, ["send-video", "--path", "a.mp4"], send_video=send) == 0
    assert _run(monkeypatch, ["send-video", "--path", "a.mp4", "--rotation", "2"], send_video=send) == 0
    assert seen == [None, 2]


def test_list_storage_prints_names(monkeypatch, capsys):
    assert _run(monkeypatch, ["list-storage", "--type", "video"],
                send_list_storage_command=lambda dev, p: ["a.h264", "b.h264"]) == 0
    assert capsys.readouterr().out == "a.h264\nb.h264\n"


def test_refresh_storage_prints_sizes(monkeypatch, capsys):
    assert _run(monkeypatch, ["refresh-storage"],
                send_refresh_storage_command=lambda dev: {"total_kb": 10, "used_kb": 1, "free_kb": 9}) == 0
    assert "used_kb: 1" in capsys.readouterr().out


def test_every_subcommand_has_a_handler():
    parser = cli.create_parser()
    sub = next(a for a in parser._actions if a.dest == "command")
    assert sub.choices is not None
    assert set(sub.choices) == set(cli.COMMANDS)


def test_power_off_needs_yes(monkeypatch):
    sent = []
    assert _run(monkeypatch, ["power-off"], power_off=lambda dev: sent.append(1) or b"ok") == 1
    assert sent == []
    assert _run(monkeypatch, ["power-off", "--yes"], power_off=lambda dev: sent.append(1) or b"ok") == 0
    assert sent == [1]


def test_brightness_range_is_0_to_100():
    parser = cli.create_parser()
    parser.parse_args(["brightness", "--value", "100"])
    import pytest
    with pytest.raises(SystemExit):
        parser.parse_args(["brightness", "--value", "101"])


def test_orientation_unsupported_exits_1(monkeypatch, capsys):
    assert _run(monkeypatch, ["orientation"], read_orientation=lambda dev: None) == 1
    assert "not supported" in capsys.readouterr().out


def test_set_clock_parses_time(monkeypatch):
    seen = []
    clock = lambda dev, when, mode=0: seen.append((when, mode)) or b"ok"  # noqa: E731
    assert _run(monkeypatch, ["set-clock", "--time", "2026-11-16T02:05:09", "--mode", "1"], set_clock=clock) == 0
    import datetime
    assert seen == [(datetime.datetime(2026, 11, 16, 2, 5, 9), 1)]


def test_every_gated_command_names_a_known_feature():
    for name, (_handler, _sync, feature) in cli.COMMANDS.items():
        assert feature is None or feature in cli.operations.FIRMWARE_FEATURES, name


def test_unsupported_feature_is_not_sent(monkeypatch, capsys):
    sent = []
    rc = _run(monkeypatch, ["voltage"], read_voltage=lambda dev: sent.append(1) or 0,
              supports=lambda dev, f: False, firmware_version=lambda dev: 8)
    assert rc == 1 and sent == []
    assert "not supported by firmware 8" in capsys.readouterr().out


def test_force_sends_anyway(monkeypatch):
    sent = []
    rc = _run(monkeypatch, ["--force", "voltage"], read_voltage=lambda dev: sent.append(1) or 0,
              supports=lambda dev, f: False, firmware_version=lambda dev: 8)
    assert rc == 0 and sent == [1]
