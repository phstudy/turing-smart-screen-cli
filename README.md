# Turing Smart Screen CLI

Warning: This is an unofficial implementation; use at your own risk.

## Overview

Turing Smart Screen CLI provides command-line utilities for managing the Turing Smart Screen 8.8" V1.1 USB display. All operations run on-demand—no background daemon is left running—and lower level USB/HID errors are surfaced directly to aid scripting.

## Supported Hardware

- Turing Smart Screen 8.8" V1.1 (USB VID 0x1CBE, PID 0x0088)

## Features

- Send sync, restart, and configuration commands to the device.
- Adjust brightness, frame rate, and persist settings to flash.
- Upload PNG/H264 assets to on-device storage and list or delete them.
- Stream PNG frames directly to the panel or play MP4 videos via FFmpeg conversion.
- Trigger playback of stored PNG or H264 assets and stop active playback.

## Installation

```bash
pip install turingscreencli

python -m venv .venv
source .venv/bin/activate
pip install -e .
# or grab tooling extras
pip install -e .[dev]
```

On macOS/Linux you may need to install FFmpeg separately (e.g., `brew install ffmpeg`).

## Usage Examples

```bash
# Enumerate and sanity check device connectivity
turing-screen sync

# Adjust device state
turing-screen brightness --value 64
turing-screen save --brightness 96 --rotation 2

# Send media directly to the panel
turing-screen send-image --path assets/sample.png
turing-screen send-video --path demo.mp4 --loop

# Work with on-device storage
turing-screen refresh-storage
turing-screen upload --path demo.png
turing-screen list-storage --type image
turing-screen delete --filename demo.png
turing-screen play-select --filename demo.png
turing-screen stop-play
```

Installing the package provides a `turing-screen` console script.

## Command Summary

- `sync` – Ping the panel to keep it responsive.
- `restart` – Reboot the panel.
- `refresh-storage` – Display SD card usage statistics.
- `clear-image` – Push a transparent frame to clear the panel.
- `stop-play` – Stop any active playback.
- `brightness --value` – Set LCD brightness.
- `save` – Persist brightness/startup/rotation/offline settings.
- `list-storage --type image|video` – List files on the device.
- `send-image --path` – Stream a PNG to the screen.
- `send-video --path [--loop]` – Stream MP4/H264 video.
- `upload --path` – Upload PNG/MP4 assets to storage.
- `delete --filename` – Delete PNG/H264 assets.
- `play-select --filename` – Play a stored PNG/H264 asset.

## Development & Testing

Install the dev extras and run the automated checks before submitting changes:

```bash
pip install -e .[dev]
python -m compileall src
python -m pytest
python -m pytest --cov=turingscreencli
python -m ruff check src tests  # if you have ruff installed
black src tests
flake8 src tests
mypy src
```

Tests under `tests/` rely on pytest and exercise CLI argument parsing and dispatch logic with USB communication stubbed out.

## Troubleshooting

- "USB device not found" – confirm the panel is connected and powered. On Linux detach the kernel driver with `dev.detach_kernel_driver(0)` or configure udev rules.
- Permission errors on Linux – create matching udev rules granting access to VID 0x1CBE/PID 0x0088 or run the CLI with elevated privileges.
- FFmpeg not found – ensure FFmpeg is installed and on your `PATH` for video encoding.
- Playback glitches – verify assets meet the device requirements (PNG 480×1920, H264 video when stored on the device).

## Device Storage Structure

- Images: `/tmp/sdcard/mmcblk0p1/img/`
- Videos: `/tmp/sdcard/mmcblk0p1/video/`

## License

Released under the MIT License. See `LICENSE` for details.
