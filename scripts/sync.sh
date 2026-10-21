#!/bin/bash
MUSIC_DIR="/home/ubuntu/music/library"
CONFIG_DIR="/home/ubuntu/music"
LOG_FILE="/home/ubuntu/music/logs/sync.log"
PLAYLISTS_FILE="$CONFIG_DIR/playlists.txt"
ARCHIVE_FILE="$CONFIG_DIR/download_archive.txt"
COOKIES_FILE="$CONFIG_DIR/cookies.txt"

mkdir -p "$MUSIC_DIR"
mkdir -p "$CONFIG_DIR/logs"

echo "==========================================" >> "$LOG_FILE"
echo "Sync started at: $(date '+%Y-%m-%d %H:%M:%S')" >> "$LOG_FILE"

# Check if playlists file has any active URLs
ACTIVE_URLS=$(grep -v '^#' "$PLAYLISTS_FILE" | grep -v '^[[:space:]]*$' || true)

if [ -z "$ACTIVE_URLS" ]; then
    echo "No playlist URLs configured in $PLAYLISTS_FILE. Skipping." >> "$LOG_FILE"
    exit 0
fi

COOKIE_ARG=""
if [ -f "$COOKIES_FILE" ] && [ -s "$COOKIES_FILE" ]; then
    COOKIE_ARG="--cookies $COOKIES_FILE"
    echo "Using authentication cookies from $COOKIES_FILE" >> "$LOG_FILE"
else
    echo "No cookies.txt found. If YouTube blocks with 'Sign in to confirm you are not a bot', add cookies via Web UI." >> "$LOG_FILE"
fi

while IFS= read -r url; do
    [ -z "$url" ] && continue
    [[ "$url" =~ ^#.* ]] && continue

    echo "Syncing: $url" >> "$LOG_FILE"

    # Download with yt-dlp using low CPU & disk priority
    nice -n 19 ionice -c 3 /usr/local/bin/yt-dlp \
        $COOKIE_ARG \
        --extract-audio \
        --audio-format mp3 \
        --audio-quality 0 \
        --embed-thumbnail \
        --embed-metadata \
        --add-metadata \
        --download-archive "$ARCHIVE_FILE" \
        --output "$MUSIC_DIR/%(uploader,creator,artist)s/%(playlist,album)s/%(title)s.%(ext)s" \
        --ignore-errors \
        --no-abort-on-error \
        --no-overwrites \
        --no-post-overwrites \
        "$url" >> "$LOG_FILE" 2>&1 || true

done <<< "$ACTIVE_URLS"

# Ensure navidrome can read new downloads
chmod -R 775 "$MUSIC_DIR"

echo "Sync finished at: $(date '+%Y-%m-%d %H:%M:%S')" >> "$LOG_FILE"
