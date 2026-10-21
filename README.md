# 🎵 Private Cloud Music Server (Navidrome + yt-dlp Sync)

Complete source code, scripts, configuration, and systemd service units for a self-hosted personal music streaming and download server on Linux / Cloud VPS.

---

## 📂 Directory Structure

```text
D:\Projects\Music Server\
├── web_manager.py          # Flask Web UI (Search YouTube, Paste Links, Cookie Auth, Live Logs)
├── playlists.txt           # Active playlist and video tracking file
├── scripts\
│   └── sync.sh             # Low-priority yt-dlp automated download & tag pipeline
├── config\
│   └── navidrome.toml      # Navidrome server configuration file
└── systemd\
    ├── navidrome.service   # Systemd unit for Navidrome streamer
    ├── music-web.service   # Systemd unit for Web Manager UI
    ├── yt-dlp-sync.service # Systemd unit for yt-dlp batch sync
    └── yt-dlp-sync.timer   # Systemd timer (Runs sync every 2 hours)
```

---

## 🌐 Server Endpoints

| Service | Port | Description |
| :--- | :--- | :--- |
| **Navidrome Web Player** | `4533` | Subsonic-compatible music streamer & player |
| **Web Downloader Manager** | `4534` | Search YouTube, add playlists, upload cookies, view logs |

---

## 📱 Mobile Apps (Background Playback)

Connect using **Subsonic** protocol:
* **Server URL:** `http://<SERVER_IP>:4533`
* **Username & Password:** Your Navidrome user credentials

**Recommended Client Apps:**
* **Android:** [Symfonium](https://symfonium.app/) (Recommended), Substreamer, Ultrasonic
* **iOS:** Amperfy, Substreamer, play:Sub

---

## ⚙️ Key Technical Features

1. **YouTube Search:** Integrated YouTube search directly in the Web UI without needing API keys.
2. **Audio Quality:** Downloads in 320kbps MP3 format with embedded metadata and album covers.
3. **Smart Deduplication:** Tracks downloaded track IDs in `download_archive.txt` so existing songs are never re-downloaded.
4. **Low Priority Execution:** Runs with `nice -n 19` and `ionice -c 3` so video extraction never impacts VoIP (TeamSpeak) or gaming latency.
5. **Memory Cap:** Capped with systemd memory limits for ultra-lightweight resource usage (~25MB for Navidrome, ~20MB for Web UI).
