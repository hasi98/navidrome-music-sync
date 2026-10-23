# 🎧 TubeSync + Navidrome Cloud Music Server

A personal, autonomous cloud music server with a modern Luminous Audio interface, seamless YouTube search & downloading, automatic Navidrome synchronization, Subsonic API streaming, and real-time audio previews.

---

## ⚡ Quick One-Click Installation

Run the automated installer on any clean Linux server (Ubuntu, Debian, AlmaLinux, Rocky, CentOS, Fedora, Arch):

```bash
git clone https://github.com/hasi98/navidrome-music-sync.git
cd navidrome-music-sync
sudo bash install.sh
```

Or install directly via a one-liner:

```bash
curl -sSL https://raw.githubusercontent.com/hasi98/navidrome-music-sync/main/install.sh | sudo bash
```

### What the Installer Does Automatically:
* Detects CPU architecture (`x86_64`, `aarch64` / ARM, `armv7`).
* Installs all system dependencies (`ffmpeg`, `python3-venv`, `curl`, `jq`, `tar`).
* Downloads and sets up the latest official **yt-dlp** binary and creates an automated daily update cron job.
* Downloads and configures **Navidrome** with systemd service units, lyrics engine, and automatic index rescan permissions.
* Creates the Python virtual environment and installs dependencies.
* Configures local firewall rules (UFW, Firewalld, iptables) for ports `4533` and `4534`.
* Detects your public IP and displays access links and setup instructions.

---

## 🌐 Server Endpoints & Ports

| Port | Service | Description | Default URL |
| :---: | :--- | :--- | :--- |
| **`4534`** | **TubeSync Web Manager** | YouTube Search, Direct Link Pipeline, Selective Playlist Inspector, Live Audio Preview, Cookie Auth, Logs Console | `http://<SERVER_IP>:4534` |
| **`4533`** | **Navidrome Music Streamer** | Modern web music player & Subsonic-compatible streaming server | `http://<SERVER_IP>:4533` |

---

## 🔒 Cloud VPS Firewall Setup (Important)

If you are hosting on a cloud VPS provider (Oracle Cloud, AWS EC2, Google Cloud, Azure, etc.), ensure you open inbound ports **`4533`** and **`4534`** in your cloud provider's web dashboard:

### 1. Oracle Cloud (OCI):
1. Go to **Networking** → **Virtual Cloud Networks (VCN)**.
2. Click your VCN → **Security Lists** → **Default Security List for...**.
3. Under **Ingress Rules**, click **Add Ingress Rules**:
   - **Source Type:** CIDR
   - **Source CIDR:** `0.0.0.0/0`
   - **IP Protocol:** TCP
   - **Destination Port Range:** `4533,4534`
   - **Description:** TubeSync & Navidrome Web Ports

### 2. AWS EC2:
1. Go to **EC2 Console** → **Instances** → Select your Instance → **Security** tab.
2. Click the attached **Security Group** → **Edit inbound rules**.
3. Click **Add rule**:
   - **Type:** Custom TCP
   - **Port range:** `4533-4534`
   - **Source:** `Anywhere-IPv4` (`0.0.0.0/0`)
   - **Description:** Navidrome & TubeSync

---

## 📱 Mobile App Setup (Background & Offline Playback)

Connect to your server using any Subsonic-compatible app:
* **Server Address:** `http://<YOUR_SERVER_IP>:4533`
* **Username & Password:** Your Navidrome credentials created on port 4533.

### Recommended Apps:
* **Android:** [Symfonium](https://symfonium.app/) (Best features & cached offline playback), [Substreamer](https://substreamerapp.com/), Ultrasonic.
* **iOS:** [Amperfy](https://apps.apple.com/app/amperfy/id1500609923), [Substreamer](https://apps.apple.com/app/substreamer/id1012991599), play:Sub.

---

## 🛠️ Management & Service Control

```bash
# Restart Web UI Manager
sudo systemctl restart music-web

# Restart Navidrome Streamer
sudo systemctl restart navidrome

# View Live Application Logs
sudo journalctl -u music-web -f

# View yt-dlp Download Pipeline Logs
tail -f logs/sync.log
```

---

## 🛡️ License & Architecture

- **Engine:** Python Flask & yt-dlp low-priority daemon (`nice -n 19`, `ionice -c 3`).
- **Streaming:** Navidrome (Subsonic v1.16.1 API).
- **Audio Quality:** 320kbps MP3 with embedded high-resolution artwork and ID3 metadata.
