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

- **list-storage**: List files stored on the device
  ```
  python turing_screen_cli.py list-storage --type [image|video]
  ```
  Options:
  - `--type [image|video]`: Type of files to list

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

- **upload**: Upload PNG or MP4 file to device storage
  ```
  python turing_screen_cli.py upload --path /path/to/file.png
  ```
  Note: PNG files will be stored in /tmp/sdcard/mmcblk0p1/img/ and MP4 files will be converted to H264 and stored in /tmp/sdcard/mmcblk0p1/video/

- **delete**: Delete a file from device storage
  ```
  python turing_screen_cli.py delete --filename filename.png
  ```

- **play-select**: Play a stored PNG image or H264 video from device storage
  ```
  python turing_screen_cli.py play-select --filename filename.png
  ```

- **stop-play**: Stop current playback
  ```
  python turing_screen_cli.py stop-play
  ```

## Device Storage Structure

The Turing Smart Screen has the following storage structure:
- Images are stored in: `/tmp/sdcard/mmcblk0p1/img/`
- Videos are stored in: `/tmp/sdcard/mmcblk0p1/video/`

## Image and Video Requirements

- Images:
  - Format: PNG only
  - Resolution: 480x1920 pixels
- Videos:
  - For direct streaming: MP4 format (will be converted to H264)
  - For storage: H264 format

## Troubleshooting

- If you get "USB device not found" error, make sure the device is properly connected
- For permission issues on Linux, try running with sudo or configure udev rules
  - Linux users may need to detach the kernel driver using `dev.detach_kernel_driver(0)`
- Make sure FFmpeg is installed and in your system PATH for video functionality
- For video playback issues, ensure the video has been properly converted to H264 format

## License

This project is licensed under the MIT License - see the text below for details:

```
MIT License

Copyright (c) 2025

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```