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
    monkeypatch.setattr(cli.operations, "play_stored_asset", lambda dev, name: False)

    rc = cli.run(["play-select", "--filename", "invalid.txt"], device_factory=lambda: object())

    assert rc == 1
