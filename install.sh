#!/usr/bin/env bash
# ==============================================================================
#  TubeSync + Navidrome: Automated One-Click Linux Server Installer
#  Compatible with Ubuntu, Debian, CentOS, AlmaLinux, Rocky Linux, Fedora, Arch
# ==============================================================================

set -eo pipefail

# Colors for terminal output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
PURPLE='\033[0;35m'
CYAN='\033[0;36m'
WHITE='\033[1;37m'
NC='\033[0m' # No Color

# Print banner
echo -e "${PURPLE}"
cat << "EOF"
  _____       _             _____                    
 |_   _|     | |           / ____|                   
   | |  _   _| |__   ___  | (___  _   _ _ __   ___   
   | | | | | | '_ \ / _ \  \___ \| | | | '_ \ / __|  
   | | | |_| | |_) |  __/  ____) | |_| | | | | (__   
   |_|  \__,_|_.__/ \___| |_____/ \__, |_| |_|\___|  
                                   __/ |             
                                  |___/              
      + Navidrome Autonomous Music Cloud Server
EOF
echo -e "${NC}"
echo -e "${CYAN}================================================================${NC}"
echo -e "${WHITE}  One-Click Production Installer & Self-Healing Setup Pipeline${NC}"
echo -e "${CYAN}================================================================${NC}\n"

# 1. Root / Sudo check
if [ "$EUID" -ne 0 ]; then
    echo -e "${RED}[ERROR] This installer requires root privileges.${NC}"
    echo -e "${YELLOW}Please run with: ${WHITE}sudo bash install.sh${NC}"
    exit 1
fi

# Detect actual non-root user and home directory
if [ -n "$SUDO_USER" ] && [ "$SUDO_USER" != "root" ]; then
    TARGET_USER="$SUDO_USER"
else
    # Fallback to current user or detect first non-system user (UID >= 1000)
    FIRST_USER=$(awk -F: '$3 >= 1000 && $3 < 60000 {print $1; exit}' /etc/passwd || true)
    TARGET_USER="${FIRST_USER:-root}"
fi

TARGET_HOME=$(getent passwd "$TARGET_USER" | cut -d: -f6)
TARGET_GROUP=$(id -gn "$TARGET_USER")

echo -e "${GREEN}[INFO] Installing for user:${NC} ${WHITE}$TARGET_USER${NC} (Home: $TARGET_HOME)"

# Determine installation directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [ -f "$SCRIPT_DIR/web_manager.py" ]; then
    INSTALL_DIR="$SCRIPT_DIR"
else
    INSTALL_DIR="$TARGET_HOME/music"
fi
echo -e "${GREEN}[INFO] Destination Directory:${NC} ${WHITE}$INSTALL_DIR${NC}\n"

# 2. Detect OS and Package Manager
echo -e "${BLUE}[STEP 1/7] Detecting Linux Distribution and Installing System Packages...${NC}"
PKG_MGR=""
if [ -f /etc/os-release ]; then
    . /etc/os-release
    OS_ID="$ID"
    OS_LIKE="${ID_LIKE:-$ID}"
else
    OS_ID="unknown"
    OS_LIKE="unknown"
fi

if command -v apt-get >/dev/null 2>&1; then
    PKG_MGR="apt"
    export DEBIAN_FRONTEND=noninteractive
    echo -e "  --> Updating package repositories (apt)..."
    apt-get update -qq
    echo -e "  --> Installing dependencies (ffmpeg, python3-venv, git, curl, jq, etc.)..."
    apt-get install -y -qq \
        ffmpeg \
        python3 \
        python3-pip \
        python3-venv \
        curl \
        wget \
        git \
        jq \
        tar \
        unzip \
        cron \
        util-linux \
        ca-certificates
elif command -v dnf >/dev/null 2>&1; then
    PKG_MGR="dnf"
    echo -e "  --> Installing EPEL and dependencies (dnf)..."
    dnf install -y -q epel-release || true
    dnf install -y -q \
        ffmpeg \
        python3 \
        python3-pip \
        curl \
        wget \
        git \
        jq \
        tar \
        unzip \
        cronie \
        util-linux
elif command -v pacman >/dev/null 2>&1; then
    PKG_MGR="pacman"
    echo -e "  --> Installing dependencies (pacman)..."
    pacman -Sy --noconfirm ffmpeg python python-pip curl wget git jq tar unzip cronie util-linux
else
    echo -e "${YELLOW}[WARN] Unknown package manager. Checking for critical binaries...${NC}"
fi

# Verify ffmpeg
if ! command -v ffmpeg >/dev/null 2>&1; then
    echo -e "${RED}[ERROR] ffmpeg could not be installed automatically.${NC}"
    echo -e "${YELLOW}Please install ffmpeg using your package manager and re-run.${NC}"
    exit 1
fi
echo -e "  ${GREEN}✓ ffmpeg audio processing engine confirmed.${NC}"

# 3. Clone or Prepare Repository Files
echo -e "\n${BLUE}[STEP 2/7] Setting Up Application Files & Directories...${NC}"
mkdir -p "$INSTALL_DIR"
if [ ! -f "$INSTALL_DIR/web_manager.py" ]; then
    echo -e "  --> Cloning repository into ${INSTALL_DIR}..."
    git clone https://github.com/hasi98/navidrome-music-sync.git "$INSTALL_DIR"
fi

# Ensure all subdirectories and critical files exist (Self-healing)
mkdir -p "$INSTALL_DIR/library"
mkdir -p "$INSTALL_DIR/logs"
mkdir -p "$INSTALL_DIR/scripts"
mkdir -p "$INSTALL_DIR/config"
touch "$INSTALL_DIR/playlists.txt"
touch "$INSTALL_DIR/download_archive.txt"
[ -f "$INSTALL_DIR/cookies.txt" ] || touch "$INSTALL_DIR/cookies.txt"

# 4. Install / Update Standalone yt-dlp Binary
echo -e "\n${BLUE}[STEP 3/7] Installing Latest yt-dlp Audio Downloader Binary...${NC}"
curl -sL https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp -o /usr/local/bin/yt-dlp
chmod a+rx /usr/local/bin/yt-dlp

YTDLP_VER=$(/usr/local/bin/yt-dlp --version || echo "unknown")
echo -e "  ${GREEN}✓ yt-dlp installed successfully (version: ${YTDLP_VER}).${NC}"

# Setup daily yt-dlp self-updater cron so YouTube algorithm updates never break downloads
cat << 'EOF' > /etc/cron.daily/yt-dlp-update
#!/bin/sh
/usr/local/bin/yt-dlp -U >/dev/null 2>&1 || true
EOF
chmod +x /etc/cron.daily/yt-dlp-update
echo -e "  ${GREEN}✓ Configured daily automated yt-dlp updater in /etc/cron.daily/yt-dlp-update.${NC}"

# 5. Install & Configure Navidrome Music Streamer
echo -e "\n${BLUE}[STEP 4/7] Installing Navidrome Music Streaming Server...${NC}"
ARCH=$(uname -m)
case "$ARCH" in
    x86_64) NAVI_ARCH="amd64" ;;
    aarch64|arm64) NAVI_ARCH="arm64" ;;
    armv7l|armhf) NAVI_ARCH="armv7" ;;
    *) NAVI_ARCH="amd64" ;;
esac

mkdir -p /opt/navidrome
mkdir -p /var/lib/navidrome

# Create system user for navidrome if not present
if ! id -u navidrome >/dev/null 2>&1; then
    useradd -r -s /bin/false -U -d /var/lib/navidrome navidrome
fi

# Fetch and install Navidrome binary if missing or update requested
if [ ! -f /opt/navidrome/navidrome ]; then
    echo -e "  --> Fetching latest Navidrome release for ${NAVI_ARCH}..."
    LATEST_TAG=$(curl -s https://api.github.com/repos/navidrome/navidrome/releases/latest | jq -r '.tag_name' 2>/dev/null || echo "v0.64.2")
    [ -z "$LATEST_TAG" ] || [ "$LATEST_TAG" = "null" ] && LATEST_TAG="v0.64.2"
    
    DL_URL="https://github.com/navidrome/navidrome/releases/download/${LATEST_TAG}/navidrome_${LATEST_TAG#v}_linux_${NAVI_ARCH}.tar.gz"
    echo -e "  --> Downloading from: ${DL_URL}..."
    curl -sL "$DL_URL" -o /tmp/navidrome.tar.gz
    tar -xzf /tmp/navidrome.tar.gz -C /opt/navidrome/
    rm -f /tmp/navidrome.tar.gz
    chmod +x /opt/navidrome/navidrome
    chown -R navidrome:navidrome /opt/navidrome
fi

NAVI_VER=$(/opt/navidrome/navidrome --version 2>/dev/null | head -n 1 || echo "installed")
echo -e "  ${GREEN}✓ Navidrome installed (${NAVI_VER}).${NC}"

# Configure /var/lib/navidrome/navidrome.toml
cat << EOF > /var/lib/navidrome/navidrome.toml
MusicFolder = "$INSTALL_DIR/library"
DataFolder = "/var/lib/navidrome"
Port = 4533
Address = "0.0.0.0"
ScanInterval = "15m"
SessionTimeout = "72h"
LogLevel = "info"
LyricsPriority = ".lrc,embedded,nd-lyrics"
EOF

chown -R navidrome:navidrome /var/lib/navidrome
chmod 755 /var/lib/navidrome

# Ensure Navidrome user can access the library folder
usermod -aG "$TARGET_GROUP" navidrome 2>/dev/null || true
usermod -aG navidrome "$TARGET_USER" 2>/dev/null || true
chmod -R 775 "$INSTALL_DIR/library"

# Configure passwordless sudo for instant Navidrome rescan from the Web UI
echo -e "  --> Configuring passwordless Navidrome rescan authorization..."
cat << EOF > /etc/sudoers.d/tubesync-navidrome
$TARGET_USER ALL=(ALL) NOPASSWD: /opt/navidrome/navidrome scan *
EOF
chmod 0440 /etc/sudoers.d/tubesync-navidrome

# 6. Setup Python Virtual Environment for TubeSync
echo -e "\n${BLUE}[STEP 5/7] Configuring Python Environment for TubeSync Web Manager...${NC}"
if [ ! -d "$INSTALL_DIR/venv" ]; then
    echo -e "  --> Creating Python virtual environment..."
    sudo -u "$TARGET_USER" python3 -m venv "$INSTALL_DIR/venv"
fi

echo -e "  --> Installing Python packages (flask, requests)..."
sudo -u "$TARGET_USER" "$INSTALL_DIR/venv/bin/pip" install -q --upgrade pip
if [ -f "$INSTALL_DIR/requirements.txt" ]; then
    sudo -u "$TARGET_USER" "$INSTALL_DIR/venv/bin/pip" install -q -r "$INSTALL_DIR/requirements.txt"
else
    sudo -u "$TARGET_USER" "$INSTALL_DIR/venv/bin/pip" install -q flask requests
fi
echo -e "  ${GREEN}✓ Python dependencies installed.${NC}"

# Fix ownership of entire install directory
chown -R "$TARGET_USER:$TARGET_GROUP" "$INSTALL_DIR"
chmod +x "$INSTALL_DIR/scripts/sync.sh" 2>/dev/null || true

# 7. Configure & Enable Systemd Services
echo -e "\n${BLUE}[STEP 6/7] Deploying Systemd Services...${NC}"

# 7a. Navidrome service
cat << EOF > /etc/systemd/system/navidrome.service
[Unit]
Description=Navidrome Music Server and Streamer
After=remote-fs.target network.target
AssertPathExists=/var/lib/navidrome

[Service]
Type=simple
User=navidrome
Group=navidrome
WorkingDirectory=/var/lib/navidrome
ExecStart=/opt/navidrome/navidrome --configfile "/var/lib/navidrome/navidrome.toml"
Restart=always
RestartSec=5
MemoryMax=350M

[Install]
WantedBy=multi-user.target
EOF

# 7b. TubeSync Web Manager service
cat << EOF > /etc/systemd/system/music-web.service
[Unit]
Description=TubeSync Music Web Manager
After=network.target

[Service]
Type=simple
User=$TARGET_USER
WorkingDirectory=$INSTALL_DIR
Environment="MUSIC_DIR=$INSTALL_DIR"
ExecStart=$INSTALL_DIR/venv/bin/python $INSTALL_DIR/web_manager.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable navidrome.service music-web.service
systemctl restart navidrome.service music-web.service
echo -e "  ${GREEN}✓ navidrome.service and music-web.service enabled & active.${NC}"

# 8. Firewall & Ports Configuration
echo -e "\n${BLUE}[STEP 7/7] Opening Firewall Ports (4533, 4534)...${NC}"

PORT_4533_OPEN=false
PORT_4534_OPEN=false

# UFW Check
if command -v ufw >/dev/null 2>&1 && ufw status | grep -q "Status: active"; then
    echo -e "  --> Detected active UFW firewall. Adding rules..."
    ufw allow 4533/tcp comment "Navidrome Streamer" >/dev/null 2>&1 || true
    ufw allow 4534/tcp comment "TubeSync Downloader" >/dev/null 2>&1 || true
    echo -e "  ${GREEN}✓ UFW rules configured for ports 4533 and 4534.${NC}"
    PORT_4533_OPEN=true
    PORT_4534_OPEN=true
fi

# Firewalld Check
if command -v firewall-cmd >/dev/null 2>&1 && systemctl is-active --quiet firewalld; then
    echo -e "  --> Detected active firewalld. Adding rules..."
    firewall-cmd --permanent --add-port=4533/tcp >/dev/null 2>&1 || true
    firewall-cmd --permanent --add-port=4534/tcp >/dev/null 2>&1 || true
    firewall-cmd --reload >/dev/null 2>&1 || true
    echo -e "  ${GREEN}✓ firewalld rules configured for ports 4533 and 4534.${NC}"
    PORT_4533_OPEN=true
    PORT_4534_OPEN=true
fi

# Oracle Cloud / Generic iptables REJECT chain check
if iptables -L INPUT -n 2>/dev/null | grep -qE "REJECT|DROP"; then
    echo -e "  --> Detected iptables filtering rules. Inserting ACCEPT bypass..."
    iptables -C INPUT -p tcp -m multiport --dports 4533,4534 -j ACCEPT 2>/dev/null || \
    iptables -I INPUT 1 -p tcp -m multiport --dports 4533,4534 -j ACCEPT 2>/dev/null || true
    
    # Save iptables rules if tools exist
    if command -v netfilter-persistent >/dev/null 2>&1; then
        netfilter-persistent save >/dev/null 2>&1 || true
    elif [ -f /etc/iptables/rules.v4 ]; then
        iptables-save > /etc/iptables/rules.v4 2>/dev/null || true
    fi
    echo -e "  ${GREEN}✓ iptables bypass applied for ports 4533 and 4534.${NC}"
    PORT_4533_OPEN=true
    PORT_4534_OPEN=true
fi

# Detect Server Public IP
PUB_IP=$(curl -s --connect-timeout 3 https://api.ipify.org || curl -s --connect-timeout 3 https://ifconfig.me || hostname -I | awk '{print $1}')

# ==============================================================================
#  Installation Summary & User Guidance
# ==============================================================================
echo -e "\n${CYAN}================================================================${NC}"
echo -e "${GREEN}  🎉 INSTALLATION COMPLETE! YOUR MUSIC SERVER IS ONLINE 🎉${NC}"
echo -e "${CYAN}================================================================${NC}\n"

echo -e "${WHITE}Server Web Endpoints:${NC}"
echo -e "  ${YELLOW}▶ TubeSync Web Manager:${NC}    ${CYAN}http://${PUB_IP}:4534${NC}"
echo -e "  ${YELLOW}▶ Navidrome Music Player:${NC}  ${CYAN}http://${PUB_IP}:4533${NC}\n"

echo -e "${WHITE}Quick 3-Step Setup Guide:${NC}"
echo -e "  ${CYAN}1.${NC} Open ${WHITE}http://${PUB_IP}:4533${NC} in your browser to create your initial Navidrome admin username and password."
echo -e "  ${CYAN}2.${NC} Open ${WHITE}http://${PUB_IP}:4534${NC} to search YouTube, paste playlist links, or stream audio previews."
echo -e "  ${CYAN}3.${NC} Connect your phone app (Substreamer, Symfonium, Amperfy) using your server URL ${WHITE}http://${PUB_IP}:4533${NC}.\n"

echo -e "${PURPLE}----------------------------------------------------------------${NC}"
echo -e "${YELLOW}IMPORTANT: Cloud Provider Firewall (Security Groups / VCN)${NC}"
echo -e "${PURPLE}----------------------------------------------------------------${NC}"
echo -e "If you cannot connect from your browser, your cloud hosting provider"
echo -e "requires you to open ports ${WHITE}4533${NC} and ${WHITE}4534${NC} in their web console:\n"
echo -e "  ${WHITE}• Oracle Cloud (OCI):${NC}  Networking → Virtual Cloud Networks → Security Lists →"
echo -e "                         Ingress Rules → Add Ingress Rule:"
echo -e "                         Source CIDR: ${CYAN}0.0.0.0/0${NC}, Port: ${CYAN}4533,4534${NC}, Protocol: ${CYAN}TCP${NC}"
echo -e "  ${WHITE}• AWS (EC2):${NC}          EC2 Console → Security Groups → Inbound Rules →"
echo -e "                         Add Custom TCP rules for ports ${CYAN}4533${NC} and ${CYAN}4534${NC} (Source: 0.0.0.0/0)"
echo -e "  ${WHITE}• Google Cloud (GCP):${NC}  VPC Network → Firewall → Create Firewall Rule →"
echo -e "                         Target: All instances, TCP ports: ${CYAN}4533,4534${NC}\n"

echo -e "${WHITE}Service Management Commands:${NC}"
echo -e "  Restart Web UI:        ${CYAN}sudo systemctl restart music-web${NC}"
echo -e "  Restart Navidrome:     ${CYAN}sudo systemctl restart navidrome${NC}"
echo -e "  View Web UI logs:      ${CYAN}sudo journalctl -u music-web -f${NC}"
echo -e "  View Download logs:    ${CYAN}tail -f ${INSTALL_DIR}/logs/sync.log${NC}\n"

echo -e "${GREEN}Enjoy your personal, high-fidelity music streaming cloud! 🎧${NC}\n"
