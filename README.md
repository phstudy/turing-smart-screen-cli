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

Results go to stdout; warnings and errors go to stderr (`-v` / `-vv` for more).

- `info [--json]` – Firmware version, SD card usage, playback status and stream queue; chip id from firmware 15.
- `sync` – Ping the panel (also returns the firmware version).
- `restart` – Reboot the panel.
- `refresh-storage` – SD card total / used / free in KB.
- `clear-image` – Push a transparent full-screen PNG (what the vendor app does before playing).
- `stop-play` – Stop stored playback (111, then poll 112 until stopped, like the vendor app).
- `brightness --value N` – Brightness 0–100 (the range the vendor app sends).
- `frame-rate --value N` – Playback frame rate 1–60.
- `rotate --value 0|2` – Rotation 0° or 180°; the panel saves it.
- `save [--brightness N] [--startup 0|1|2] [--rotation 0|2] [--sleep N] [--offline 0|1] [--clock-direction 0-3]` –
  Save all settings at once (125). Every field is written: an option you leave out is saved as its default
  (brightness 100, everything else 0), and there is no way to read the current values back first.
  Firmware 0015 ignores `--clock-direction`.
- `list-storage --type image|video` – Stored file names, one per line.
- `file-size --filename NAME` – Size in bytes of a stored file (0 if it does not exist).
- `send-image --path FILE [--format png|jpeg|auto] [--quality 10-100]` – Show an image now. `png` (default)
  splits a PNG over 512 KB into horizontal layers (a workaround for the panel's size limit, not something the vendor
  app does); `jpeg` sends one baseline JPEG, lowering the quality until it is under 1 MB; `auto` uses PNG when it
  fits one push, otherwise JPEG. `--quality` applies to JPEG only.
- `send-video --path FILE.mp4 [--loop] [--frame-rate N] [--brightness N] [--rotation 0|2]` – Stream the MP4's H.264
  (copied, not re-encoded). The frame rate is the video's own (read with ffprobe; 25 if it cannot be read) unless
  `--frame-rate` is given; brightness and rotation are left alone unless given. With `--loop`, Ctrl-C stops it normally.
- `upload --path FILE.png|FILE.mp4` – Store a PNG, or an MP4 as `<name>.mp4.h264`, then check its size on the panel.
- `delete --filename NAME` – Delete a stored file (the panel also reports success for a missing file).
- `play-select --filename NAME [--brightness N]` – Play a stored `.h264` or show a stored `.png`, with the vendor
  app's sequence (video: 98, 111, 112…, 102, 110; image: 111, 112…, 102, 113).
- `voltage`, `orientation`, `block-size` – Queries the vendor app has (50, 60, 17); no firmware checked so far
  supports them, see below.
- `set-clock [--time ISO] [--mode 0|1|2]` – Set the panel clock (51; the vendor app sends it to the 4.6" only;
  the mode byte's meaning is not decoded). Not supported by firmware checked so far.
- `run-state on|off` – The vendor app's "Run all / Stop all" flag (49). Not supported by firmware checked so far.
- `power-off --yes` – Halt the panel (12); it stays off until its power is cycled. Refused without `--yes`.

`delete`, `file-size` and `play-select` take a plain file name (no directories).

Exit codes: 0 on success; 1 when a command fails, the panel does not confirm it, or the firmware is known not to
support it; 2 for usage errors; 130 when interrupted with Ctrl-C. Exceptions, by design: `play-select` of a PNG
counts a missing reply as success on firmware 0008/0015 (see below); `info` succeeds when the version query is
answered and leaves out any field whose query was not.

## Firmware differences

The CLI reads the panel's firmware version (the reply to `sync`; `info` shows it, e.g. `turzx_0001_0015` = 15)
and checks `FIRMWARE_FEATURES` in `operations.py`, the single list of behaviour that differs between versions.
Each entry says from which version the feature exists (or that none checked so far has it) and the highest version
it was checked on; firmware newer than that is assumed to have it until someone checks.

| Feature | From firmware | Checked up to | When missing |
|---|---|---|---|
| `stored_image_reply` (113 always answers) | none | 15 | `play-select` of a PNG counts a missing reply as success (0008 and 0015 answer only the first time after a restart or upload); check the screen |
| `chip_id` (240) | 15 | 15 | `info` does not ask for it |
| `voltage`, `orientation`, `block_size`, `set_clock`, `run_state` | none | 15 | the command exits 1 with "not supported by firmware N"; `--force` (before or after the command) sends it anyway |

Measured on firmware 0008 and 0015.
To add a difference, add an entry to `FIRMWARE_FEATURES` and check it with `supports(dev, "<feature>")`;
when a new firmware has been checked, raise that entry's `checked_up_to`.

## Protocol notes

Packets match the vendor app byte for byte: a 500-byte header (`command, 0, 0x1A, 0x6D`, a millisecond timestamp,
payload from byte 8), DES-CBC with key = IV = `slv3tuzx` and PKCS7 padding (like .NET), in a 512-byte packet ending
`0xA1 0x1A`; bulk data follows the packet. Replies are 512 bytes with the command at `[0]` and 200 at `[1]` on
success (38 also puts 200 at `[8]`). A failed USB write is an error; a missing reply is reported per command.

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

Tests need real pycryptodome and Pillow. `tests/test_operations.py` checks every command at the packet level against a
fake panel that answers like the real one (decrypting what the CLI sends); `tests/test_cli.py` runs every subcommand
through `cli.run` with the operations stubbed. Neither touches USB. Check a change on a real panel as well.

## Troubleshooting

- "USB device not found" – confirm the panel is connected and powered. On Linux detach the kernel driver with `dev.detach_kernel_driver(0)` or configure udev rules.
- Permission errors on Linux – create matching udev rules granting access to VID 0x1CBE/PID 0x0088 or run the CLI with elevated privileges.
- FFmpeg not found – `upload`/`send-video` of an MP4 need `ffmpeg` (and `ffprobe` for the frame rate) on `PATH`.
- Playback glitches – verify assets meet the device requirements (PNG 480×1920, H264 video when stored on the device).

## Device Storage Structure

- Images: `/tmp/sdcard/mmcblk0p1/img/`
- Videos: `/tmp/sdcard/mmcblk0p1/video/`

## License

Released under the MIT License. See `LICENSE` for details.
