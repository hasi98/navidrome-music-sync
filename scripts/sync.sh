#!/bin/bash
set -e

# ==========================================
# TubeSync Automated Library Sync Pipeline
# ==========================================

# Dynamically locate directories
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BASE_DIR="${MUSIC_DIR:-$(cd "$SCRIPT_DIR/.." && pwd)}"
CONFIG_DIR="$BASE_DIR"
MUSIC_DIR="$BASE_DIR/library"
LOG_FILE="$BASE_DIR/logs/sync.log"
PLAYLISTS_FILE="$CONFIG_DIR/playlists.txt"
ARCHIVE_FILE="$CONFIG_DIR/download_archive.txt"
COOKIES_FILE="$CONFIG_DIR/cookies.txt"

# Self-healing: create missing directories & files
mkdir -p "$MUSIC_DIR"
mkdir -p "$BASE_DIR/logs"
touch "$PLAYLISTS_FILE" "$ARCHIVE_FILE"

echo "==========================================" >> "$LOG_FILE"
echo "Sync started at: $(date '+%Y-%m-%d %H:%M:%S')" >> "$LOG_FILE"

# Dynamically locate yt-dlp binary
YTDLP_BIN="$(which yt-dlp 2>/dev/null || echo "")"
if [ -z "$YTDLP_BIN" ] || [ ! -x "$YTDLP_BIN" ]; then
    if [ -x "/usr/local/bin/yt-dlp" ]; then
        YTDLP_BIN="/usr/local/bin/yt-dlp"
    elif [ -x "/usr/bin/yt-dlp" ]; then
        YTDLP_BIN="/usr/bin/yt-dlp"
    elif [ -x "$BASE_DIR/venv/bin/yt-dlp" ]; then
        YTDLP_BIN="$BASE_DIR/venv/bin/yt-dlp"
    else
        echo "ERROR: yt-dlp binary not found! Please run installer or install yt-dlp." >> "$LOG_FILE"
        exit 1
    fi
fi

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

    # Strip dynamic radio mixes to prevent infinite loops
    if [[ "$url" =~ "list=RD" ]] || [[ "$url" =~ "list=UL" ]]; then
        echo "Stripping dynamic radio mix parameters from: $url" >> "$LOG_FILE"
        url=$(echo "$url" | sed 's/[?&]list=[A-Za-z0-9_-]*//g' | sed 's/[?&]start_radio=[^&]*//g')
    fi

    echo "Syncing: $url" >> "$LOG_FILE"

    # Download with yt-dlp using low CPU & disk priority
    nice -n 19 ionice -c 3 "$YTDLP_BIN" \
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
chmod -R 775 "$MUSIC_DIR" 2>/dev/null || true

# Trigger instant Navidrome rescan if binary exists
if [ -x "/opt/navidrome/navidrome" ] && [ -f "/var/lib/navidrome/navidrome.toml" ]; then
    sudo /opt/navidrome/navidrome scan --configfile /var/lib/navidrome/navidrome.toml >> "$LOG_FILE" 2>&1 || true
fi

echo "Sync finished at: $(date '+%Y-%m-%d %H:%M:%S')" >> "$LOG_FILE"
