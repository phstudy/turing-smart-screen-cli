# Turing Smart Screen CLI

A command-line interface tool for controlling Turing Smart Screen 8.8 inch V1.1 device via USB.

## Overview

This CLI tool allows you to interact with Turing Smart Screen device (VENDOR_ID: 0x1cbe, PRODUCT_ID: 0x0088) from your computer. You can send various commands including displaying images, playing videos, adjusting brightness, and configuring device settings.

## Requirements

- Python 3.7+
- Dependencies:
  - pyusb
  - pycryptodome
  - Pillow (PIL)
  - pytest (for testing)
  - mypy, black, flake8 (for development)
- FFmpeg (for video processing)

## Installation

1. Clone this repository:
   ```sh
   git clone https://github.com/phstudy/turing_screen_cli.git
   cd turing_screen_cli
   ```

2. Install the required Python packages:
   ```sh
   pip install -r requirements.txt
   ```

3. (Optional, for development) Install in editable mode:
   ```sh
   pip install -e .
   ```

4. Make sure FFmpeg is installed on your system (required for video functionality).

## Usage

After installation, you can use the CLI as follows:

```sh
turing-screen [command] [options]
```

### Available Commands

#### Image Operations
- **Display an image on the screen**
  ```sh
  turing-screen image /path/to/image.png
  ```
- **Clear the current image**
  ```sh
  turing-screen image /path/to/image.png --clear
  ```

#### Video Operations
- **Play a video on the screen**
  ```sh
  turing-screen video /path/to/video.mp4
  ```
- **Loop the video**
  ```sh
  turing-screen video /path/to/video.mp4 --loop
  ```
- **Stop video playback**
  ```sh
  turing-screen video /path/to/video.mp4 --stop
  ```

#### File Operations
- **Upload a file to device storage**
  ```sh
  turing-screen file upload /path/to/file.png
  ```
- **Delete a file from device storage**
  ```sh
  turing-screen file delete filename.png
  ```
- **List files in device storage**
  ```sh
  turing-screen file list /path/on/device
  ```

#### Device Operations
- **Send sync command**
  ```sh
  turing-screen device sync
  ```
- **Restart the device**
  ```sh
  turing-screen device restart
  ```
- **Set screen brightness (0-102)**
  ```sh
  turing-screen device brightness 50
  ```
- **Set frame rate**
  ```sh
  turing-screen device frame-rate 30
  ```

### Help

```sh
turing-screen --help
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

## Development & Testing

- **Run tests:**
  ```sh
  pytest
  ```
- **Type checking:**
  ```sh
  mypy src/turing_screen_cli
  ```
- **Code formatting:**
  ```sh
  black src/turing_screen_cli
  ```
- **Linting:**
  ```sh
  flake8 src/turing_screen_cli
  ```

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