#!/bin/bash

cd "$(dirname "$0")"

# read token from config file
CONFIG_FILE="$HOME/.immich_config"
if [ ! -f "$CONFIG_FILE" ]; then
    echo "Error: Config file not found at $CONFIG_FILE"
    exit 1
fi

source "$CONFIG_FILE"
if [ -z "$IMMICH_TOKEN" ]; then
    echo "Error: IMMICH_TOKEN not set in config file"
    exit 1
fi

if [ -z "$IMMICH_PERSON_IDS" ]; then
    echo "Error: IMMICH_PERSON_IDS not set in config file"
    exit 1
fi

# choose a random person_id
RANDOM_INDEX=$((RANDOM % ${#IMMICH_PERSON_IDS[@]}))
SELECTED_PERSON_ID=${IMMICH_PERSON_IDS[$RANDOM_INDEX]}

HOUR=$(date +%H)
DAY=$(date +%u)  # 1-7, where 1 is Monday

# Monday-Friday 17-22
if [ $DAY -le 5 ] && [ $HOUR -ge 17 ] && [ $HOUR -le 22 ]; then
    python ../../turing_screen_cli.py brightness --value 50
    python immich_photo_display.py --token $IMMICH_TOKEN --output /tmp --person-id $SELECTED_PERSON_ID
    python ../../turing_screen_cli.py send-image --path /tmp/random.png
# Saturday-Sunday 7-22
elif [ $DAY -ge 6 ] && [ $HOUR -ge 7 ] && [ $HOUR -le 22 ]; then
    python ../../turing_screen_cli.py brightness --value 50
    python immich_photo_display.py --token $IMMICH_TOKEN --output /tmp --person-id $SELECTED_PERSON_ID
    python ../../turing_screen_cli.py send-image --path /tmp/random.png
else
    python ../../turing_screen_cli.py brightness --value 0
fi
