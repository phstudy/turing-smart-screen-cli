# Turing Smart Screen CLI

A command-line interface tool for controlling Turing Smart Screen 8.8 inch V1.1 device via USB.

## Overview

This CLI tool allows you to interact with Turing Smart Screen device (VENDOR_ID: 0x1cbe, PRODUCT_ID: 0x0088) from your computer. You can send various commands including displaying images, playing videos, adjusting brightness, and configuring device settings.

## Requirements

- Python 3.6+
- Dependencies:
  - pyusb
  - pycryptodome
  - Pillow (PIL)
- FFmpeg (for video processing)

## Installation

1. Clone this repository:
```
git clone https://github.com/phstudy/turing_screen_cli.git
cd turing_screen_cli
```

2. Install the required Python packages:
```
pip install -r requirements.txt
```

3. Make sure FFmpeg is installed on your system (required for video functionality).

## Usage

```
python turing_screen_cli.py [command] [options]
```

### Available Commands

- **sync**: Send sync command to the device
  ```
  python turing_screen_cli.py sync
  ```

- **restart**: Restart the device
  ```
  python turing_screen_cli.py restart
  ```

- **refresh-storage**: Get storage information
  ```
  python turing_screen_cli.py refresh-storage
  ```

- **brightness**: Set screen brightness (0-102)
  ```
  python turing_screen_cli.py brightness --value 50
  ```

- **save**: Save device settings
  ```
  python turing_screen_cli.py save [options]
  ```
  Options:
  - `--brightness [0-102]`: Default 102
  - `--startup [0|1|2]`: 0 = default, 1 = play image, 2 = play video
  - `--rotation [0|2]`: 0 = 0°, 2 = 180°
  - `--sleep [0-255]`: Sleep timeout
  - `--offline [0|1]`: 0 = Disabled, 1 = Enabled

- **clear-image**: Clear the current image
  ```
  python turing_screen_cli.py clear-image
  ```

- **send-image**: Display an image on the screen
  ```
  python turing_screen_cli.py send-image --path /path/to/image.png
  ```
  Note: Images must be exactly 480x1920 pixels in PNG format

- **send-video**: Play a video on the screen
  ```
  python turing_screen_cli.py send-video --path /path/to/video.mp4 [--loop]
  ```
  Add `--loop` to continuously play the video until interrupted with Ctrl+C

## Image Requirements

- Format: PNG only
- Resolution: 480x1920 pixels

## Troubleshooting

- If you get "USB device not found" error, make sure the device is properly connected
- For permission issues on Linux, try running with sudo or configure udev rules
- Make sure FFmpeg is installed and in your system PATH for video functionality

## License

[Add your license information here]

## Author

[Your Name/Organization]