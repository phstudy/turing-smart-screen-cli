# Immich Photo Display for Turing Screen

This example demonstrates how to display photos from your Immich server on a Turing Screen. It automatically shows random photos of specified people during different time periods.

## Features

- Displays random photos of selected people from your Immich server
- Automatic brightness control based on time of day
- Configurable display schedules:
  - Weekdays (Mon-Fri): 17:00-22:00
  - Weekends (Sat-Sun): 07:00-22:00
  - Screen turns off outside these hours

## Setup

1. Create a config file at `~/.immich_config` with the following content:
```bash
IMMICH_TOKEN="your_immich_api_token"
IMMICH_PERSON_IDS=("person_id_1" "person_id_2" "person_id_3")
```

2. Get your Immich API token from your Immich server settings
3. Get person IDs from your Immich server (you can find these in the URL when viewing a person's photos)

## Usage

Run the script:
```bash
./send_image.sh
```

The script will:
1. Check the current time
2. Select a random person from your configured list
3. Download and display a random photo of that person
4. Adjust screen brightness automatically

## Requirements

- Python 3.x
- `turing_screen_cli` package
- Immich server access
- Bash shell

## Notes

- Photos are temporarily stored in `/tmp/random.png`
- Screen brightness is set to 50% during active hours and 0% during off hours 