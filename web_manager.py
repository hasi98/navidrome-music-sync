import os
import subprocess
import threading
import urllib.request
import urllib.parse
import json
import re
import datetime
from flask import Flask, render_template_string, request, redirect, url_for, jsonify, send_from_directory

app = Flask(__name__)
app.secret_key = "music_manager_secret_key"

MUSIC_DIR = "/home/ubuntu/music"
LIBRARY_DIR = os.path.join(MUSIC_DIR, "library")
PLAYLISTS_FILE = os.path.join(MUSIC_DIR, "playlists.txt")
SYNC_SCRIPT = os.path.join(MUSIC_DIR, "scripts", "sync.sh")
LOG_FILE = os.path.join(MUSIC_DIR, "logs", "sync.log")
COOKIES_FILE = os.path.join(MUSIC_DIR, "cookies.txt")

sync_lock = threading.Lock()
download_lock = threading.Lock()
is_syncing = False

current_download = {
    "url": "",
    "id": "",
    "title": "",
    "percent": 0.0,
    "speed": "",
    "eta": "",
    "status": "idle",  # "idle", "starting", "downloading", "converting", "finalizing", "completed", "failed", "cancelled"
    "message": ""
}

def run_sync_thread():
    global is_syncing
    with sync_lock:
        is_syncing = True
        try:
            subprocess.run(["bash", SYNC_SCRIPT], check=False)
        finally:
            is_syncing = False

def trigger_sync_async():
    global is_syncing
    if not is_syncing:
        t = threading.Thread(target=run_sync_thread, daemon=True)
        t.start()
        return True
    return False

def run_single_download(url, title="", video_id=""):
    global is_syncing, current_download
    with sync_lock:
        is_syncing = True
        
        # Extract video ID if not provided
        if not video_id:
            m = re.search(r"(?:v=|\/)([0-9A-Za-z_-]{11})", url)
            if m:
                video_id = m.group(1)

        with download_lock:
            current_download.update({
                "url": url,
                "id": video_id or "",
                "title": title or "YouTube Track",
                "percent": 5.0,
                "speed": "Starting...",
                "eta": "",
                "status": "starting",
                "message": "Initializing download..."
            })

        try:
            # Strip radio mix and extraneous playlist parameters from single track downloads
            clean_url = re.sub(r"[?&]list=RD[^&]*", "", url)
            clean_url = re.sub(r"[?&]start_radio=[^&]*", "", clean_url)
            
            cookie_arg = []
            if os.path.exists(COOKIES_FILE) and os.path.getsize(COOKIES_FILE) > 10:
                cookie_arg = ["--cookies", COOKIES_FILE]
            
            cmd = [
                "nice", "-n", "19", "ionice", "-c", "3",
                "/usr/local/bin/yt-dlp",
                *cookie_arg,
                "--newline",
                "--extract-audio",
                "--audio-format", "mp3",
                "--audio-quality", "0",
                "--embed-thumbnail",
                "--embed-metadata",
                "--add-metadata",
                "--output", f"{MUSIC_DIR}/library/%(uploader,creator,artist)s/%(playlist,album)s/%(title)s.%(ext)s",
                "--ignore-errors",
                "--no-abort-on-error",
                "--no-overwrites",
                "--no-post-overwrites",
                clean_url
            ]
            
            with open(LOG_FILE, "a", encoding="utf-8") as f:
                now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                f.write(f"\n==========================================\nDownloading single track: {clean_url}\nStarted at: {now_str}\n")
                f.flush()

                proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
                
                for line in iter(proc.stdout.readline, ''):
                    f.write(line)
                    f.flush()
                    line_str = line.strip()
                    
                    # Parse percentage
                    p_match = re.search(r"\[download\]\s+([\d\.]+)%", line_str)
                    if p_match:
                        try:
                            pct = float(p_match.group(1))
                            with download_lock:
                                current_download["percent"] = min(92.0, pct)
                                current_download["status"] = "downloading"
                        except ValueError:
                            pass
                    
                    # Parse speed
                    s_match = re.search(r"at\s+([\d\.]+[A-Za-z/]+)", line_str)
                    if s_match:
                        with download_lock:
                            current_download["speed"] = s_match.group(1)
                            
                    # Parse ETA
                    eta_match = re.search(r"ETA\s+([\d:]+)", line_str)
                    if eta_match:
                        with download_lock:
                            current_download["eta"] = eta_match.group(1)
                            
                    if "[ExtractAudio]" in line_str:
                        with download_lock:
                            current_download["status"] = "converting"
                            current_download["percent"] = 94.0
                            current_download["speed"] = "Converting to MP3..."
                            
                    if "[Metadata]" in line_str or "[Thumbnails" in line_str:
                        with download_lock:
                            current_download["status"] = "finalizing"
                            current_download["percent"] = 98.0
                            current_download["speed"] = "Embedding metadata..."

                proc.stdout.close()
                ret = proc.wait()
                subprocess.run(["chmod", "-R", "775", f"{MUSIC_DIR}/library"], check=False)
                end_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                
                if ret == 0:
                    f.write(f"✓ Single track downloaded successfully at: {end_str}\n==========================================\n")
                    with download_lock:
                        current_download["status"] = "completed"
                        current_download["percent"] = 100.0
                        current_download["speed"] = "Completed!"
                else:
                    f.write(f"❌ Download failed (code {ret}) at: {end_str}\n==========================================\n")
                    with download_lock:
                        current_download["status"] = "failed"
                        current_download["message"] = f"Failed with code {ret}"
        except Exception as e:
            with open(LOG_FILE, "a", encoding="utf-8") as f:
                f.write(f"Error during single download: {e}\n")
            with download_lock:
                current_download["status"] = "failed"
                current_download["message"] = str(e)
        finally:
            is_syncing = False
            # Reset idle status after delay
            def reset_idle():
                import time
                time.sleep(6)
                with download_lock:
                    if current_download["status"] in ("completed", "failed", "cancelled"):
                        current_download["status"] = "idle"
                        current_download["percent"] = 0.0
                        current_download["speed"] = ""
            threading.Thread(target=reset_idle, daemon=True).start()

def trigger_single_download_async(url, title="", video_id=""):
    global is_syncing
    if not is_syncing:
        t = threading.Thread(target=run_single_download, args=(url, title, video_id), daemon=True)
        t.start()
        return True
    return False

def stop_sync_process():
    global is_syncing, current_download
    try:
        subprocess.run(["pkill", "-9", "-f", "yt-dlp"], check=False)
        subprocess.run(["pkill", "-9", "-f", "sync.sh"], check=False)
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            f.write(f"\n==========================================\n[CANCELLED] Download process stopped by user via Web UI at {now_str}\n==========================================\n")
        with download_lock:
            current_download["status"] = "cancelled"
            current_download["speed"] = "Cancelled"
    except Exception as e:
        print("Error stopping sync:", e)
    finally:
        is_syncing = False
    return True

def get_playlists():
    if not os.path.exists(PLAYLISTS_FILE):
        return []
    with open(PLAYLISTS_FILE, "r", encoding="utf-8") as f:
        lines = [line.strip() for line in f if line.strip() and not line.startswith("#")]
    return lines

def save_playlists(playlists):
    with open(PLAYLISTS_FILE, "w", encoding="utf-8") as f:
        f.write("# Managed by Web UI\n")
        for p in playlists:
            f.write(f"{p}\n")

def has_cookies():
    if not (os.path.exists(COOKIES_FILE) and os.path.getsize(COOKIES_FILE) > 10):
        return False
    try:
        with open(COOKIES_FILE, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read()
            return "LOGIN_INFO" in content or "SAPISID" in content
    except Exception:
        return False

def get_recent_logs(lines_count=35):
    if not os.path.exists(LOG_FILE):
        return "No logs generated yet. Add a track to begin downloading."
    try:
        with open(LOG_FILE, "r", encoding="utf-8", errors="ignore") as f:
            lines = f.readlines()
            return "".join(lines[-lines_count:]) if lines else "Log file is currently empty."
    except Exception as e:
        return f"Error reading log file: {e}"

def search_youtube(query, limit=40):
    if not query.strip():
        return []

    # Method 1: Fast direct desktop scrape (sub-second)
    url = f"https://www.youtube.com/results?search_query={urllib.parse.quote(query)}"
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept-Language": "en-US,en;q=0.9"
    })
    try:
        with urllib.request.urlopen(req, timeout=6) as response:
            html = response.read().decode('utf-8')
        
        match = re.search(r"var ytInitialData = ({.*?});</script>", html)
        if not match:
            match = re.search(r"ytInitialData = ({.*?});", html)
        
        if match:
            data = json.loads(match.group(1))
            items = []
            contents = data.get("contents", {}).get("twoColumnSearchResultsRenderer", {}).get("primaryContents", {}).get("sectionListRenderer", {}).get("contents", [])
            
            for sec in contents:
                item_sec = sec.get("itemSectionRenderer", {}).get("contents", [])
                for item in item_sec:
                    vr = item.get("videoRenderer")
                    pr = item.get("playlistRenderer")
                    rr = item.get("radioRenderer")
                    
                    if vr:
                        video_id = vr.get("videoId")
                        title = vr.get("title", {}).get("runs", [{}])[0].get("text", "")
                        author = vr.get("ownerText", {}).get("runs", [{}])[0].get("text", "")
                        duration = vr.get("lengthText", {}).get("simpleText", "")
                        thumbs = vr.get("thumbnail", {}).get("thumbnails", [])
                        thumb = thumbs[-1]["url"] if thumbs else f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg"
                        badges = [b.get("metadataBadgeRenderer", {}).get("label", "") for b in vr.get("badges", [])]
                        is_live = any("LIVE" in b.upper() for b in badges)
                        
                        # Category detection: song vs radio
                        cat = "song"
                        title_lower = title.lower()
                        if is_live or "radio" in title_lower or "24/7" in title_lower or ("lofi" in title_lower and "live" in title_lower):
                            cat = "radio"

                        if video_id and title:
                            items.append({
                                "type": "video",
                                "category": cat,
                                "id": video_id,
                                "title": title,
                                "channel": author,
                                "duration": duration if not is_live else "LIVE",
                                "thumbnail": thumb,
                                "url": f"https://www.youtube.com/watch?v={video_id}"
                            })
                    elif rr:
                        pl_id = rr.get("playlistId")
                        title = rr.get("title", {}).get("simpleText", "")
                        author = rr.get("shortBylineText", {}).get("runs", [{}])[0].get("text", "")
                        count = rr.get("videoCountText", {}).get("runs", [{}])[0].get("text", "") if rr.get("videoCountText") else "Mix"
                        thumbs = rr.get("thumbnail", {}).get("thumbnails", [{}])
                        thumb = thumbs[-1]["url"] if thumbs else ""
                        if pl_id and title:
                            items.append({
                                "type": "radio",
                                "category": "radio",
                                "id": pl_id,
                                "title": title,
                                "channel": author or "YouTube Mix",
                                "duration": count,
                                "thumbnail": thumb,
                                "url": f"https://www.youtube.com/watch?v={pl_id}&list={pl_id}"
                            })
                    elif pr:
                        pl_id = pr.get("playlistId", "")
                        title = pr.get("title", {}).get("simpleText", "")
                        author = pr.get("shortBylineText", {}).get("runs", [{}])[0].get("text", "")
                        count = pr.get("videoCount", "")
                        thumbs = pr.get("thumbnails", [{}])[0].get("thumbnails", [])
                        thumb = thumbs[-1]["url"] if thumbs else ""
                        
                        is_radio = pl_id.startswith("RD") or "mix" in title.lower() or "radio" in title.lower()
                        cat = "radio" if is_radio else "playlist"
                        
                        if pl_id and title:
                            items.append({
                                "type": "radio" if is_radio else "playlist",
                                "category": cat,
                                "id": pl_id,
                                "title": title,
                                "channel": author,
                                "duration": f"{count} videos" if count else ("Mix" if is_radio else "Playlist"),
                                "thumbnail": thumb,
                                "url": f"https://www.youtube.com/playlist?list={pl_id}" if not is_radio else f"https://www.youtube.com/watch?v={pl_id}&list={pl_id}"
                            })
                    if len(items) >= limit:
                        break
                if len(items) >= limit:
                    break
            
            if items:
                return items
    except Exception as e:
        print("Scrape search error:", e)

    # Method 2: Fallback to yt-dlp native search
    try:
        cmd = ["/usr/local/bin/yt-dlp", "--flat-playlist", "--dump-single-json", f"ytsearch{limit}:{query}"]
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=12)
        if res.returncode == 0 and res.stdout:
            data = json.loads(res.stdout)
            entries = data.get("entries", [])
            items = []
            for e in entries:
                v_id = e.get("id")
                v_title = e.get("title")
                if v_id and v_title:
                    duration_sec = e.get("duration") or 0
                    mins = int(duration_sec // 60)
                    secs = int(duration_sec % 60)
                    dur_str = f"{mins}:{secs:02d}" if duration_sec else ""
                    
                    is_playlist = e.get("_type") == "playlist" or "playlist" in e.get("url", "").lower()
                    is_radio = "list=RD" in e.get("url", "") or "mix" in v_title.lower() or "radio" in v_title.lower()
                    cat = "radio" if is_radio else ("playlist" if is_playlist else "song")
                    
                    items.append({
                        "type": "playlist" if is_playlist else ("radio" if is_radio else "video"),
                        "category": cat,
                        "id": v_id,
                        "title": v_title,
                        "channel": e.get("uploader") or e.get("channel") or "",
                        "duration": dur_str,
                        "thumbnail": f"https://i.ytimg.com/vi/{v_id}/hqdefault.jpg",
                        "url": e.get("url") if e.get("url", "").startswith("http") else f"https://www.youtube.com/watch?v={v_id}"
                    })
            if items:
                return items
    except Exception as e:
        print("yt-dlp fallback search error:", e)

    return []

HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="en" class="dark">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no, viewport-fit=cover">
    <meta name="theme-color" content="#090d16">
    <meta name="apple-mobile-web-app-capable" content="yes">
    <meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
    <title>TubeSync Mobile - Navidrome Manager</title>
    
    <!-- Tailwind CSS & FontAwesome -->
    <script src="https://cdn.tailwindcss.com"></script>
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.5.1/css/all.min.css">
    
    <script>
        tailwind.config = {
            darkMode: 'class',
            theme: {
                extend: {
                    colors: {
                        darkbg: '#090d16',
                        glass: 'rgba(15, 23, 42, 0.78)',
                    }
                }
            }
        }
    </script>
    
    <style>
        body {
            background-color: #090d16;
            color: #f1f5f9;
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
            -webkit-tap-highlight-color: transparent;
            user-select: none;
        }
        /* Custom scrollbar */
        ::-webkit-scrollbar { width: 4px; height: 4px; }
        ::-webkit-scrollbar-track { background: transparent; }
        ::-webkit-scrollbar-thumb { background: #1e293b; border-radius: 4px; }
        ::-webkit-scrollbar-thumb:hover { background: #334155; }
        
        .glass-panel {
            background: rgba(15, 23, 42, 0.82);
            backdrop-filter: blur(20px);
            -webkit-backdrop-filter: blur(20px);
            border: 1px solid rgba(255, 255, 255, 0.08);
        }
        .touch-action-btn:active {
            transform: scale(0.95);
        }
        /* Equalizer visualizer animation */
        @keyframes eq-dance {
            0%, 100% { height: 3px; }
            50% { height: 13px; }
        }
        .eq-bar-1 { animation: eq-dance 0.5s ease-in-out infinite; }
        .eq-bar-2 { animation: eq-dance 0.7s ease-in-out infinite 0.15s; }
        .eq-bar-3 { animation: eq-dance 0.4s ease-in-out infinite 0.3s; }
        .eq-bar-4 { animation: eq-dance 0.6s ease-in-out infinite 0.1s; }

        .line-clamp-1 {
            display: -webkit-box;
            -webkit-line-clamp: 1;
            -webkit-box-orient: vertical;
            overflow: hidden;
        }
        .line-clamp-2 {
            display: -webkit-box;
            -webkit-line-clamp: 2;
            -webkit-box-orient: vertical;
            overflow: hidden;
        }
        .line-clamp-3 {
            display: -webkit-box;
            -webkit-line-clamp: 3;
            -webkit-box-orient: vertical;
            overflow: hidden;
        }
    </style>
</head>
<body class="min-h-screen pb-32 flex flex-col items-center antialiased selection:bg-rose-500 selection:text-white">

    <!-- Toast Notification Container -->
    <div id="toastBox" class="fixed top-3 z-50 left-1/2 -translate-x-1/2 w-11/12 max-w-sm pointer-events-none transition-all duration-300 opacity-0 -translate-y-4"></div>

    <!-- Main Container -->
    <main class="w-full max-w-xl px-3 sm:px-4 py-3 space-y-4">
        
        <!-- App Header -->
        <header class="glass-panel p-3 rounded-2xl shadow-xl flex items-center justify-between sticky top-2 z-40">
            <div class="flex items-center space-x-2.5">
                <div class="w-9 h-9 rounded-xl bg-gradient-to-tr from-rose-500 via-pink-600 to-indigo-600 flex items-center justify-center text-white text-base shadow-md shadow-rose-500/20">
                    <i class="fa-solid fa-compact-disc animate-[spin_8s_linear_infinite]"></i>
                </div>
                <div>
                    <h1 class="text-sm sm:text-base font-bold tracking-tight text-white flex items-center gap-1.5">
                        <span>TubeSync</span>
                        <span id="headerSyncBadge" class="px-2 py-0.5 text-3xs rounded-full font-medium bg-emerald-500/10 text-emerald-400 border border-emerald-500/20">
                            ● Ready
                        </span>
                    </h1>
                    <p class="text-3xs text-slate-400">Navidrome Cloud Sync</p>
                </div>
            </div>

            <div class="flex items-center space-x-2">
                <!-- Stop Download Button -->
                <button onclick="stopDownload()" id="headerStopBtn" class="hidden touch-action-btn px-2.5 py-1.5 bg-rose-600/30 hover:bg-rose-600 text-rose-300 hover:text-white border border-rose-500/50 rounded-xl text-3xs font-bold transition flex items-center space-x-1 animate-pulse" title="Cancel active download">
                    <i class="fa-solid fa-stop text-3xs"></i>
                    <span>Stop</span>
                </button>

                <!-- Open Navidrome Link -->
                <a href="http://{{ host }}:4533" target="_blank"
                    class="touch-action-btn px-3 py-1.5 bg-gradient-to-r from-indigo-600 to-violet-600 hover:from-indigo-500 hover:to-violet-500 text-white text-xs font-semibold rounded-xl shadow-md flex items-center space-x-1.5 transition">
                    <i class="fa-solid fa-headphones"></i>
                    <span>Player</span>
                </a>
            </div>
        </header>

        <!-- ================= TAB 1: SEARCH (DEFAULT) ================= -->
        <section id="tab-search" class="tab-content space-y-3">
            
            <!-- Search Bar -->
            <div class="glass-panel p-3.5 rounded-2xl space-y-3 shadow-lg">
                <div class="relative flex items-center">
                    <span class="absolute left-3.5 text-slate-400 pointer-events-none">
                        <i class="fa-solid fa-magnifying-glass"></i>
                    </span>
                    <input type="text" id="searchInput" placeholder="Search track, playlist, radio mix..."
                        class="w-full pl-10 pr-9 py-2.5 bg-slate-900/90 border border-slate-700/80 rounded-xl text-xs sm:text-sm focus:outline-none focus:border-rose-500 focus:ring-1 focus:ring-rose-500 transition text-slate-100 placeholder-slate-500 shadow-inner"
                        onkeydown="if(event.key==='Enter') doSearch()">
                    <button id="clearSearchBtn" onclick="clearSearch()" class="hidden absolute right-3 text-slate-500 hover:text-slate-300 p-1">
                        <i class="fa-solid fa-circle-xmark"></i>
                    </button>
                </div>

                <!-- Action Button & Filter Chips -->
                <div class="flex items-center justify-between gap-2">
                    <div class="flex items-center space-x-1.5 overflow-x-auto pb-0.5 no-scrollbar text-2xs">
                        <button onclick="quickSearch('Daft Punk')" class="touch-action-btn px-2.5 py-1 bg-slate-800/90 hover:bg-slate-700 border border-slate-700/60 rounded-lg text-slate-300 shrink-0">⚡ Daft Punk</button>
                        <button onclick="quickSearch('Lofi Hip Hop Radio')" class="touch-action-btn px-2.5 py-1 bg-slate-800/90 hover:bg-slate-700 border border-slate-700/60 rounded-lg text-slate-300 shrink-0">📻 Lofi Radio</button>
                        <button onclick="quickSearch('Synthwave Playlist')" class="touch-action-btn px-2.5 py-1 bg-slate-800/90 hover:bg-slate-700 border border-slate-700/60 rounded-lg text-slate-300 shrink-0">🌌 Synthwave</button>
                    </div>

                    <button onclick="doSearch()" id="searchBtn"
                        class="touch-action-btn px-4 py-2 bg-gradient-to-r from-rose-600 to-pink-600 hover:from-rose-500 hover:to-pink-500 font-semibold text-xs text-white rounded-xl shadow-md transition flex items-center space-x-1.5 shrink-0">
                        <i class="fa-solid fa-arrow-right"></i>
                        <span>Find</span>
                    </button>
                </div>
            </div>

            <!-- Dedicated Search Results Section -->
            <div id="searchResultsSection" class="hidden glass-panel p-3.5 rounded-2xl space-y-3 border border-slate-700/80 shadow-2xl">
                
                <!-- Category Filter Buttons Bar (Songs / Playlists / Radios) -->
                <div class="flex items-center justify-between pb-2 border-b border-slate-800/80">
                    <div class="flex items-center space-x-1.5 overflow-x-auto no-scrollbar py-0.5">
                        <button onclick="setSearchFilter('all')" id="filterBtn-all"
                            class="filter-chip touch-action-btn px-2.5 py-1 rounded-xl text-3xs font-semibold bg-rose-600 text-white shadow-sm flex items-center space-x-1 shrink-0">
                            <span>All</span>
                            <span id="filterCount-all" class="px-1.5 py-0.2 bg-black/30 rounded-full font-mono">0</span>
                        </button>
                        <button onclick="setSearchFilter('song')" id="filterBtn-song"
                            class="filter-chip touch-action-btn px-2.5 py-1 rounded-xl text-3xs font-semibold bg-slate-800/90 hover:bg-slate-700 text-slate-300 border border-slate-700/60 flex items-center space-x-1 shrink-0">
                            <span>🎵 Songs</span>
                            <span id="filterCount-song" class="px-1.5 py-0.2 bg-slate-900 rounded-full font-mono">0</span>
                        </button>
                        <button onclick="setSearchFilter('playlist')" id="filterBtn-playlist"
                            class="filter-chip touch-action-btn px-2.5 py-1 rounded-xl text-3xs font-semibold bg-slate-800/90 hover:bg-slate-700 text-slate-300 border border-slate-700/60 flex items-center space-x-1 shrink-0">
                            <span>📁 Playlists</span>
                            <span id="filterCount-playlist" class="px-1.5 py-0.2 bg-slate-900 rounded-full font-mono">0</span>
                        </button>
                        <button onclick="setSearchFilter('radio')" id="filterBtn-radio"
                            class="filter-chip touch-action-btn px-2.5 py-1 rounded-xl text-3xs font-semibold bg-slate-800/90 hover:bg-slate-700 text-slate-300 border border-slate-700/60 flex items-center space-x-1 shrink-0">
                            <span>📻 Radios & Mixes</span>
                            <span id="filterCount-radio" class="px-1.5 py-0.2 bg-slate-900 rounded-full font-mono">0</span>
                        </button>
                    </div>

                    <button onclick="clearSearch()" class="touch-action-btn text-3xs text-slate-400 hover:text-rose-400 p-1 shrink-0 ml-2">
                        <i class="fa-solid fa-xmark"></i>
                    </button>
                </div>

                <!-- Results Scroll Container -->
                <div id="searchResultsList" class="space-y-2.5 max-h-[440px] overflow-y-auto pr-1"></div>

                <!-- Load More Button -->
                <div id="loadMoreContainer" class="pt-2 hidden border-t border-slate-800/80">
                    <button onclick="loadMoreResults()" id="loadMoreBtn"
                        class="touch-action-btn w-full py-2 px-3 bg-slate-800/90 hover:bg-slate-700/90 border border-slate-700/60 rounded-xl text-xs font-semibold text-slate-200 flex items-center justify-center space-x-2 transition shadow">
                        <i class="fa-solid fa-angles-down text-rose-400"></i>
                        <span id="loadMoreBtnText">Load More Results</span>
                    </button>
                </div>
            </div>

            <!-- Empty Search Prompt -->
            <div id="searchPlaceholder" class="p-8 text-center text-slate-500 text-xs italic glass-panel rounded-2xl border-dashed">
                <i class="fa-solid fa-music text-slate-600 text-2xl mb-2 block"></i>
                Search above to download songs, playlists, or radio mixes directly into your Navidrome server!
            </div>
        </section>

        <!-- ================= TAB 2: DOWNLOADED MUSIC LIBRARY ================= -->
        <section id="tab-library" class="tab-content hidden space-y-3">
            <div class="glass-panel p-3.5 rounded-2xl space-y-3 shadow-lg">
                <div class="flex items-center justify-between">
                    <div class="flex items-center space-x-2">
                        <div class="w-7 h-7 rounded-lg bg-pink-500/20 text-pink-400 flex items-center justify-center text-xs">
                            <i class="fa-solid fa-music"></i>
                        </div>
                        <div>
                            <h2 class="text-xs sm:text-sm font-semibold text-slate-200">Server Music Library</h2>
                            <p class="text-3xs text-slate-400" id="libSummaryText">Loading library...</p>
                        </div>
                    </div>

                    <button onclick="fetchLibrary()" class="touch-action-btn p-2 bg-slate-800 hover:bg-slate-700 text-slate-300 rounded-xl text-xs flex items-center space-x-1" title="Reload library">
                        <i class="fa-solid fa-arrows-rotate" id="libRefreshIcon"></i>
                    </button>
                </div>

                <!-- Library Real-Time Filter -->
                <div class="relative flex items-center">
                    <span class="absolute left-3 text-slate-400 pointer-events-none text-xs">
                        <i class="fa-solid fa-filter"></i>
                    </span>
                    <input type="text" id="libFilterInput" placeholder="Filter downloaded songs or artists..."
                        oninput="filterLibrary(this.value)"
                        class="w-full pl-9 pr-3 py-2 bg-slate-900 border border-slate-700/80 rounded-xl text-xs text-slate-100 placeholder-slate-500 focus:outline-none focus:border-pink-500">
                </div>

                <!-- Downloaded Songs List -->
                <div id="librarySongsList" class="space-y-2 max-h-[480px] overflow-y-auto pr-1"></div>
            </div>
        </section>

        <!-- ================= TAB 3: SOURCES & PLAYLIST TRACKING ================= -->
        <section id="tab-sources" class="tab-content hidden space-y-3">
            
            <!-- Add Direct Link Card -->
            <div class="glass-panel p-3.5 rounded-2xl shadow-lg space-y-3">
                <div class="flex items-center space-x-2">
                    <div class="w-7 h-7 rounded-lg bg-indigo-500/20 text-indigo-400 flex items-center justify-center text-xs">
                        <i class="fa-solid fa-link"></i>
                    </div>
                    <h2 class="text-xs sm:text-sm font-semibold text-slate-200">Add YouTube Link</h2>
                </div>
                <p class="text-3xs text-slate-400">Queue an individual track, an album, or a full recurring YouTube playlist.</p>

                <form id="directAddForm" onsubmit="submitDirectLink(event)" class="space-y-2.5">
                    <div>
                        <input type="url" id="directUrlInput" placeholder="https://www.youtube.com/playlist?list=... or watch URL" required
                            class="w-full px-3 py-2 bg-slate-900 border border-slate-700/80 rounded-xl text-xs text-slate-100 placeholder-slate-500 focus:outline-none focus:border-indigo-500">
                    </div>
                    <div class="flex items-center justify-between pt-1">
                        <label class="flex items-center space-x-2 text-3xs text-slate-300 cursor-pointer">
                            <input type="checkbox" id="directSyncNow" checked class="rounded border-slate-700 text-indigo-600 focus:ring-0">
                            <span>Download immediately</span>
                        </label>
                        <button type="submit" id="directAddBtn"
                            class="touch-action-btn px-3 py-1.5 bg-indigo-600 hover:bg-indigo-500 text-white font-semibold text-xs rounded-xl shadow transition flex items-center space-x-1.5">
                            <i class="fa-solid fa-plus"></i>
                            <span>Queue</span>
                        </button>
                    </div>
                </form>
            </div>

            <!-- Tracked Playlists List -->
            <div class="glass-panel p-3.5 rounded-2xl shadow-lg space-y-3">
                <div class="flex items-center justify-between">
                    <div class="flex items-center space-x-2">
                        <div class="w-7 h-7 rounded-lg bg-emerald-500/20 text-emerald-400 flex items-center justify-center text-xs">
                            <i class="fa-solid fa-list-check"></i>
                        </div>
                        <h2 class="text-xs sm:text-sm font-semibold text-slate-200">Tracked Playlists</h2>
                    </div>

                    <button onclick="triggerFullSync()" id="syncAllBtn"
                        class="touch-action-btn px-3 py-1 bg-slate-800 hover:bg-slate-700 text-slate-200 border border-slate-700/60 rounded-xl text-3xs font-semibold transition flex items-center space-x-1.5">
                        <i class="fa-solid fa-rotate" id="syncAllIcon"></i>
                        <span>Sync All</span>
                    </button>
                </div>

                <div id="playlistsList" class="divide-y divide-slate-800/80 max-h-64 overflow-y-auto pr-1">
                    {% if playlists %}
                        {% for pl in playlists %}
                        <div class="py-2.5 flex items-center justify-between space-x-3 group">
                            <div class="truncate flex items-center space-x-2 text-xs text-slate-300 min-w-0">
                                <i class="fa-brands fa-youtube text-rose-500 text-xs shrink-0"></i>
                                <span class="truncate font-mono text-3xs">{{ pl }}</span>
                            </div>
                            <button onclick="deletePlaylist('{{ pl }}')" class="touch-action-btn text-slate-500 hover:text-rose-400 p-1.5 transition text-xs shrink-0" title="Remove link">
                                <i class="fa-solid fa-trash-can"></i>
                            </button>
                        </div>
                        {% endfor %}
                    {% else %}
                        <p class="text-3xs text-slate-500 italic text-center py-5">No playlists tracked yet.</p>
                    {% endif %}
                </div>
            </div>
        </section>

        <!-- ================= TAB 4: COOKIES & AUTH ================= -->
        <section id="tab-cookies" class="tab-content hidden space-y-3">
            <div class="glass-panel p-4 rounded-2xl shadow-lg space-y-3">
                <div class="flex items-center justify-between">
                    <div class="flex items-center space-x-2">
                        <div class="w-7 h-7 rounded-lg bg-amber-500/20 text-amber-400 flex items-center justify-center text-xs">
                            <i class="fa-solid fa-shield-halved"></i>
                        </div>
                        <h2 class="text-xs sm:text-sm font-semibold text-slate-200">YouTube Authentication</h2>
                    </div>

                    {% if cookies_ok %}
                    <span class="px-2 py-0.5 rounded-full text-3xs bg-emerald-500/10 text-emerald-400 border border-emerald-500/20 font-medium flex items-center gap-1">
                        <i class="fa-solid fa-check"></i> Active
                    </span>
                    {% else %}
                    <span class="px-2 py-0.5 rounded-full text-3xs bg-amber-500/20 text-amber-300 border border-amber-500/30 font-medium animate-pulse flex items-center gap-1">
                        <i class="fa-solid fa-triangle-exclamation"></i> Required
                    </span>
                    {% endif %}
                </div>

                <p class="text-3xs text-slate-400 leading-relaxed">
                    YouTube blocks cloud datacenter IPs with <i>"Sign in to confirm you're not a bot"</i>. Pasting your browser cookies bypasses this permanently.
                </p>

                <form action="/cookies" method="POST" enctype="multipart/form-data" class="space-y-3">
                    <div>
                        <textarea name="cookies_text" rows="3" placeholder="Paste your Netscape format cookies text here..."
                            class="w-full px-3 py-2 bg-slate-900 border border-slate-700/80 rounded-xl text-3xs font-mono focus:outline-none focus:border-amber-500 text-slate-200 placeholder-slate-600"></textarea>
                    </div>
                    <div class="flex flex-col sm:flex-row sm:items-center justify-between gap-2">
                        <div class="text-3xs text-slate-400 flex items-center space-x-1.5">
                            <span>Or upload:</span>
                            <input type="file" name="cookies_file" accept=".txt" class="text-3xs text-slate-400 file:mr-2 file:py-1 file:px-2 file:rounded-lg file:border-0 file:text-3xs file:bg-slate-800 file:text-slate-200 hover:file:bg-slate-700 cursor-pointer">
                        </div>
                        <button type="submit" class="touch-action-btn px-4 py-1.5 bg-amber-600 hover:bg-amber-500 text-white font-semibold text-xs rounded-xl shadow transition">
                            Save Cookies
                        </button>
                    </div>
                </form>
            </div>
        </section>

        <!-- ================= TAB 5: LIVE LOGS ================= -->
        <section id="tab-logs" class="tab-content hidden space-y-3">
            <div class="glass-panel p-3.5 rounded-2xl shadow-lg space-y-3">
                <div class="flex items-center justify-between">
                    <div class="flex items-center space-x-2">
                        <div class="w-7 h-7 rounded-lg bg-slate-800 text-slate-300 flex items-center justify-center text-xs">
                            <i class="fa-solid fa-terminal"></i>
                        </div>
                        <h2 class="text-xs sm:text-sm font-semibold text-slate-200">Live Sync Terminal</h2>
                    </div>

                    <div class="flex items-center space-x-2">
                        <button onclick="stopDownload()" id="logsStopBtn"
                            class="hidden touch-action-btn px-2 py-1 bg-rose-600/20 hover:bg-rose-600 text-rose-300 hover:text-white border border-rose-500/40 rounded-lg text-3xs font-semibold transition flex items-center space-x-1 animate-pulse">
                            <i class="fa-solid fa-stop text-3xs"></i>
                            <span>Cancel</span>
                        </button>
                        <label class="flex items-center space-x-1.5 text-3xs text-slate-400 cursor-pointer">
                            <input type="checkbox" id="autoRefreshLogs" checked class="rounded border-slate-700 text-rose-500 focus:ring-0">
                            <span>Auto</span>
                        </label>
                        <button onclick="fetchLogs()" class="touch-action-btn p-1.5 text-slate-400 hover:text-slate-200 text-xs">
                            <i class="fa-solid fa-arrows-rotate"></i>
                        </button>
                    </div>
                </div>

                <div class="relative">
                    <pre id="logConsole" class="bg-slate-950 p-3 rounded-xl text-3xs font-mono text-emerald-400/90 overflow-x-auto max-h-72 border border-slate-800/80 whitespace-pre-wrap leading-relaxed">{{ logs }}</pre>
                </div>
            </div>
        </section>

    </main>

    <!-- Floating Active Download & Audio Dock (Sits above bottom bar) -->
    <div id="floatingDock" class="fixed bottom-16 left-1/2 -translate-x-1/2 w-11/12 max-w-xl z-40 hidden transition-all duration-300">
        <!-- Download Status Card -->
        <div id="activeDownloadBar" class="glass-panel p-2.5 rounded-2xl shadow-2xl border border-rose-500/30 flex items-center justify-between gap-2.5 hidden">
            <div class="flex items-center space-x-2 min-w-0 flex-1">
                <div class="flex items-end space-x-0.5 h-3.5 shrink-0 px-1">
                    <span class="w-1 bg-rose-500 rounded-full eq-bar-1"></span>
                    <span class="w-1 bg-pink-500 rounded-full eq-bar-2"></span>
                    <span class="w-1 bg-indigo-500 rounded-full eq-bar-3"></span>
                    <span class="w-1 bg-rose-400 rounded-full eq-bar-4"></span>
                </div>
                <div class="min-w-0 flex-1">
                    <p class="text-xs font-semibold text-slate-100 truncate" id="dockTrackTitle">Downloading track...</p>
                    <div class="flex items-center space-x-2 text-3xs text-slate-400 mt-0.5">
                        <span id="dockSpeedText" class="text-rose-400 font-mono">0%</span>
                        <span id="dockStatusText" class="truncate">Preparing</span>
                    </div>
                    <div class="w-full bg-slate-800 rounded-full h-1 mt-1 overflow-hidden">
                        <div id="dockProgressBar" class="bg-gradient-to-r from-rose-500 to-indigo-500 h-full rounded-full transition-all duration-300" style="width: 5%"></div>
                    </div>
                </div>
            </div>
            <button onclick="stopDownload()" class="touch-action-btn p-2 bg-rose-600/20 hover:bg-rose-600 text-rose-300 hover:text-white rounded-xl text-xs shrink-0" title="Stop Download">
                <i class="fa-solid fa-stop"></i>
            </button>
        </div>

        <!-- Audio Preview Player Bar -->
        <div id="audioPreviewBar" class="glass-panel p-2.5 rounded-2xl shadow-2xl border border-pink-500/30 flex items-center justify-between gap-2 hidden">
            <div class="flex items-center space-x-2 min-w-0 flex-1">
                <button onclick="toggleAudioPreview()" id="previewPlayBtn" class="w-8 h-8 rounded-xl bg-pink-600 hover:bg-pink-500 text-white flex items-center justify-center text-xs shrink-0 shadow">
                    <i class="fa-solid fa-play" id="previewPlayIcon"></i>
                </button>
                <div class="min-w-0 flex-1">
                    <p class="text-xs font-semibold text-slate-100 truncate" id="previewSongTitle">Playing Preview</p>
                    <p class="text-3xs text-slate-400 truncate" id="previewSongArtist">Artist</p>
                </div>
            </div>
            <audio id="globalAudioPlayer" onended="onAudioEnded()" ontimeupdate="onAudioTimeUpdate()"></audio>
            <button onclick="closeAudioPreview()" class="touch-action-btn p-1.5 text-slate-400 hover:text-slate-200 text-xs shrink-0">
                <i class="fa-solid fa-xmark"></i>
            </button>
        </div>
    </div>

    <!-- Fixed Bottom Mobile Navigation Bar -->
    <nav class="fixed bottom-0 left-0 right-0 z-50 glass-panel border-t border-slate-800/90 shadow-2xl px-2 py-1.5 pb-[max(0.5rem,env(safe-area-inset-bottom))]">
        <div class="max-w-xl mx-auto flex items-center justify-around">
            <button onclick="switchTab('search')" id="tabBtn-search" class="bottom-nav-btn flex-1 py-1.5 flex flex-col items-center justify-center text-rose-500 transition">
                <i class="fa-solid fa-magnifying-glass text-base"></i>
                <span class="text-3xs font-medium mt-1">Search</span>
                <div class="tab-indicator w-1 h-1 bg-rose-500 rounded-full mt-0.5"></div>
            </button>

            <button onclick="switchTab('library')" id="tabBtn-library" class="bottom-nav-btn flex-1 py-1.5 flex flex-col items-center justify-center text-slate-400 hover:text-slate-200 transition relative">
                <i class="fa-solid fa-music text-base"></i>
                <span class="text-3xs font-medium mt-1">Library</span>
                <div class="tab-indicator w-1 h-1 bg-rose-500 rounded-full mt-0.5 hidden"></div>
            </button>

            <button onclick="switchTab('sources')" id="tabBtn-sources" class="bottom-nav-btn flex-1 py-1.5 flex flex-col items-center justify-center text-slate-400 hover:text-slate-200 transition relative">
                <i class="fa-solid fa-layer-group text-base"></i>
                <span class="text-3xs font-medium mt-1">Sources</span>
                <div class="tab-indicator w-1 h-1 bg-rose-500 rounded-full mt-0.5 hidden"></div>
            </button>

            <button onclick="switchTab('cookies')" id="tabBtn-cookies" class="bottom-nav-btn flex-1 py-1.5 flex flex-col items-center justify-center text-slate-400 hover:text-slate-200 transition relative">
                <i class="fa-solid fa-cookie-bite text-base"></i>
                <span class="text-3xs font-medium mt-1">Auth</span>
                <div class="tab-indicator w-1 h-1 bg-rose-500 rounded-full mt-0.5 hidden"></div>
                {% if not cookies_ok %}
                <span class="w-2 h-2 rounded-full bg-amber-400 absolute top-1.5 right-6 animate-ping"></span>
                <span class="w-2 h-2 rounded-full bg-amber-400 absolute top-1.5 right-6"></span>
                {% endif %}
            </button>

            <button onclick="switchTab('logs')" id="tabBtn-logs" class="bottom-nav-btn flex-1 py-1.5 flex flex-col items-center justify-center text-slate-400 hover:text-slate-200 transition">
                <i class="fa-solid fa-terminal text-base"></i>
                <span class="text-3xs font-medium mt-1">Console</span>
                <div class="tab-indicator w-1 h-1 bg-rose-500 rounded-full mt-0.5 hidden"></div>
            </button>
        </div>
    </nav>

    <!-- Client-Side Application Logic -->
    <script>
        // Global State
        let allSearchResults = [];
        let displayedCount = 0;
        let currentFilter = 'all';
        const BATCH_SIZE = 8;
        
        let allLibrarySongs = [];
        let activeDownloadingId = null;

        // Tab Switching
        function switchTab(tabName) {
            document.querySelectorAll('.tab-content').forEach(el => el.classList.add('hidden'));
            document.querySelectorAll('.bottom-nav-btn').forEach(btn => {
                btn.classList.remove('text-rose-500');
                btn.classList.add('text-slate-400');
                const indicator = btn.querySelector('.tab-indicator');
                if (indicator) indicator.classList.add('hidden');
            });

            const activeTab = document.getElementById(`tab-${tabName}`);
            const activeBtn = document.getElementById(`tabBtn-${tabName}`);
            if (activeTab && activeBtn) {
                activeTab.classList.remove('hidden');
                activeBtn.classList.add('text-rose-500');
                activeBtn.classList.remove('text-slate-400');
                const indicator = activeBtn.querySelector('.tab-indicator');
                if (indicator) indicator.classList.remove('hidden');
            }

            if (tabName === 'logs') {
                fetchLogs();
            } else if (tabName === 'library') {
                fetchLibrary();
            }
        }

        // Toast Messages
        function showToast(message, isError = false) {
            const toastBox = document.getElementById('toastBox');
            toastBox.innerHTML = `
                <div class="glass-panel p-3 rounded-xl shadow-2xl flex items-center space-x-3 border ${isError ? 'border-rose-500/50 bg-rose-950/80 text-rose-200' : 'border-emerald-500/50 bg-slate-900/90 text-slate-100'}">
                    <i class="fa-solid ${isError ? 'fa-circle-exclamation text-rose-400' : 'fa-circle-check text-emerald-400'} text-base"></i>
                    <p class="text-xs font-medium">${message}</p>
                </div>
            `;
            toastBox.classList.remove('opacity-0', '-translate-y-4');
            toastBox.classList.add('opacity-100', 'translate-y-0');

            setTimeout(() => {
                toastBox.classList.add('opacity-0', '-translate-y-4');
                toastBox.classList.remove('opacity-100', 'translate-y-0');
            }, 3000);
        }

        // Search Filter (All / Songs / Playlists / Radios)
        function setSearchFilter(cat) {
            currentFilter = cat;
            document.querySelectorAll('.filter-chip').forEach(btn => {
                btn.className = "filter-chip touch-action-btn px-2.5 py-1 rounded-xl text-3xs font-semibold bg-slate-800/90 hover:bg-slate-700 text-slate-300 border border-slate-700/60 flex items-center space-x-1 shrink-0";
            });
            const activeChip = document.getElementById(`filterBtn-${cat}`);
            if (activeChip) {
                activeChip.className = "filter-chip touch-action-btn px-2.5 py-1 rounded-xl text-3xs font-semibold bg-rose-600 text-white shadow-sm flex items-center space-x-1 shrink-0";
            }
            
            const list = document.getElementById('searchResultsList');
            list.innerHTML = '';
            displayedCount = 0;
            renderBatch();
        }

        function getFilteredResults() {
            if (currentFilter === 'all') return allSearchResults;
            return allSearchResults.filter(item => item.category === currentFilter);
        }

        // Batch Rendering for Search
        function renderBatch() {
            const list = document.getElementById('searchResultsList');
            const loadMoreContainer = document.getElementById('loadMoreContainer');
            const loadMoreBtnText = document.getElementById('loadMoreBtnText');

            const filtered = getFilteredResults();
            const nextBatch = filtered.slice(displayedCount, displayedCount + BATCH_SIZE);
            displayedCount += nextBatch.length;

            const html = nextBatch.map(item => {
                const isDownloadingThis = activeDownloadingId && (activeDownloadingId === item.id || activeDownloadingId === item.url);
                return `
                <div class="glass-panel p-2.5 rounded-2xl flex items-center justify-between gap-3 hover:border-slate-700 transition relative" id="search-card-${item.id}">
                    <!-- Left: Square Thumbnail -->
                    <div class="relative w-14 h-14 rounded-xl overflow-hidden shrink-0 bg-slate-950 border border-slate-800">
                        <img src="${item.thumbnail}" alt="" class="w-full h-full object-cover" loading="lazy">
                        ${item.duration ? `<span class="absolute bottom-1 right-1 bg-black/85 text-white text-3xs px-1 py-0.2 rounded font-mono font-medium">${item.duration}</span>` : ''}
                    </div>

                    <!-- Center: Full Title (2 Lines) & Metadata -->
                    <div class="min-w-0 flex-1">
                        <h3 class="text-xs font-semibold text-slate-100 line-clamp-2 leading-snug break-words" title="${escapeHtml(item.title)}">${item.title}</h3>
                        <div class="text-3xs text-slate-400 mt-1 flex items-center gap-1.5 flex-wrap">
                            <span class="truncate max-w-[130px]">${item.channel}</span>
                            <span class="px-1.5 py-0.2 rounded font-medium ${
                                item.category === 'song' ? 'bg-rose-500/10 text-rose-400 border border-rose-500/20' :
                                item.category === 'playlist' ? 'bg-indigo-500/10 text-indigo-400 border border-indigo-500/20' :
                                'bg-amber-500/10 text-amber-400 border border-amber-500/20'
                            }">${item.category === 'song' ? '🎵 Song' : item.category === 'playlist' ? '📁 Playlist' : '📻 Radio'}</span>
                        </div>
                    </div>

                    <!-- Right: Download Action / Visualizer Progress -->
                    <div class="shrink-0 flex items-center" id="action-wrap-${item.id}">
                        ${isDownloadingThis ? renderInCardProgress(item) : `
                            <button onclick="downloadTrack('${item.url}', '${item.id}', '${escapeHtml(item.title)}', this)"
                                class="touch-action-btn px-3 py-1.5 bg-gradient-to-r from-rose-600 to-pink-600 hover:from-rose-500 hover:to-pink-500 text-white font-semibold text-3xs rounded-xl shadow-md transition flex items-center space-x-1 shrink-0">
                                <i class="fa-solid fa-download"></i>
                                <span>Get</span>
                            </button>
                        `}
                    </div>
                </div>
            `}).join('');

            list.insertAdjacentHTML('beforeend', html);

            if (displayedCount < filtered.length) {
                loadMoreContainer.classList.remove('hidden');
                loadMoreBtnText.innerText = `Load More (+${Math.min(BATCH_SIZE, filtered.length - displayedCount)})`;
            } else {
                loadMoreContainer.classList.add('hidden');
            }
        }

        function renderInCardProgress(item) {
            return `
                <div class="w-24 sm:w-28 flex flex-col items-end space-y-1">
                    <div class="flex items-center space-x-1 text-3xs font-mono text-rose-400">
                        <div class="flex items-end space-x-0.5 h-3">
                            <span class="w-0.5 bg-rose-500 rounded-full eq-bar-1"></span>
                            <span class="w-0.5 bg-pink-500 rounded-full eq-bar-2"></span>
                            <span class="w-0.5 bg-indigo-500 rounded-full eq-bar-3"></span>
                        </div>
                        <span id="card-pct-${item.id}">...</span>
                    </div>
                    <div class="w-full bg-slate-800 rounded-full h-1.5 overflow-hidden">
                        <div id="card-bar-${item.id}" class="bg-gradient-to-r from-rose-500 to-indigo-500 h-full rounded-full transition-all duration-300" style="width: 15%"></div>
                    </div>
                </div>
            `;
        }

        function loadMoreResults() {
            renderBatch();
        }

        function escapeHtml(text) {
            return (text || '').replace(/'/g, "\\'").replace(/"/g, '&quot;');
        }

        async function doSearch() {
            const input = document.getElementById('searchInput');
            const query = input.value.trim();
            if (!query) return;

            const btn = document.getElementById('searchBtn');
            const clearBtn = document.getElementById('clearSearchBtn');
            const section = document.getElementById('searchResultsSection');
            const placeholder = document.getElementById('searchPlaceholder');
            const list = document.getElementById('searchResultsList');
            const loadMoreContainer = document.getElementById('loadMoreContainer');

            clearBtn.classList.remove('hidden');
            btn.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i>';
            btn.disabled = true;

            placeholder.classList.add('hidden');
            section.classList.remove('hidden');
            loadMoreContainer.classList.add('hidden');

            list.innerHTML = `
                <div class="p-8 text-center text-slate-400 text-xs">
                    <i class="fa-solid fa-spinner fa-spin text-rose-500 text-xl mb-2 block"></i>
                    Searching YouTube for "${query}"...
                </div>
            `;

            try {
                const res = await fetch(`/api/search?q=${encodeURIComponent(query)}`);
                const data = await res.json();

                if (!data.results || data.results.length === 0) {
                    list.innerHTML = `
                        <div class="p-8 text-center text-slate-400 text-xs">
                            <i class="fa-solid fa-face-frown text-slate-500 text-xl mb-2 block"></i>
                            No results found for "${query}". Try different terms.
                        </div>
                    `;
                    return;
                }

                allSearchResults = data.results;
                
                // Update Filter Counts
                const songsCount = allSearchResults.filter(i => i.category === 'song').length;
                const playlistsCount = allSearchResults.filter(i => i.category === 'playlist').length;
                const radiosCount = allSearchResults.filter(i => i.category === 'radio').length;

                document.getElementById('filterCount-all').innerText = allSearchResults.length;
                document.getElementById('filterCount-song').innerText = songsCount;
                document.getElementById('filterCount-playlist').innerText = playlistsCount;
                document.getElementById('filterCount-radio').innerText = radiosCount;

                displayedCount = 0;
                list.innerHTML = '';
                renderBatch();

            } catch (err) {
                list.innerHTML = `<div class="p-6 text-center text-rose-400 text-xs">Search error: ${err.message}</div>`;
            } finally {
                btn.innerHTML = '<i class="fa-solid fa-arrow-right"></i><span>Find</span>';
                btn.disabled = false;
            }
        }

        function quickSearch(term) {
            document.getElementById('searchInput').value = term;
            doSearch();
        }

        function clearSearch() {
            document.getElementById('searchInput').value = '';
            document.getElementById('clearSearchBtn').classList.add('hidden');
            document.getElementById('searchResultsSection').classList.add('hidden');
            document.getElementById('searchPlaceholder').classList.remove('hidden');
            allSearchResults = [];
            displayedCount = 0;
        }

        // Download Action
        async function downloadTrack(url, id, title, btnElement) {
            activeDownloadingId = id;
            if (btnElement) {
                const wrap = document.getElementById(`action-wrap-${id}`);
                if (wrap) wrap.innerHTML = renderInCardProgress({ id: id });
            }

            try {
                const formData = new FormData();
                formData.append('url', url);
                formData.append('title', title);
                formData.append('video_id', id);
                formData.append('sync_now', 'true');

                await fetch('/add', { method: 'POST', body: formData });
                showToast(`Queued: ${title}`);
                updateDownloadDock({
                    title: title,
                    percent: 5,
                    speed: 'Starting...',
                    status: 'downloading'
                });
            } catch (err) {
                showToast("Failed to start download", true);
            }
        }

        // Direct Link Submission
        async function submitDirectLink(e) {
            e.preventDefault();
            const input = document.getElementById('directUrlInput');
            const syncNow = document.getElementById('directSyncNow').checked;
            const btn = document.getElementById('directAddBtn');
            const url = input.value.trim();
            if (!url) return;

            btn.disabled = true;
            btn.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i><span>Adding...</span>';

            try {
                const formData = new FormData();
                formData.append('url', url);
                if (syncNow) formData.append('sync_now', 'true');

                await fetch('/add', { method: 'POST', body: formData });
                showToast("Link added to tracking list!");
                input.value = '';
                setTimeout(() => location.reload(), 800);
            } catch (err) {
                showToast("Error adding link", true);
                btn.disabled = false;
                btn.innerHTML = '<i class="fa-solid fa-plus"></i><span>Queue</span>';
            }
        }

        // Delete Playlist
        async function deletePlaylist(url) {
            if (!confirm("Remove this link from auto sync?")) return;
            const formData = new FormData();
            formData.append('url', url);
            await fetch('/delete', { method: 'POST', body: formData });
            showToast("Removed from tracking list");
            setTimeout(() => location.reload(), 600);
        }

        // Trigger Full Sync
        async function triggerFullSync() {
            const btn = document.getElementById('syncAllBtn');
            const icon = document.getElementById('syncAllIcon');
            icon.classList.add('fa-spin');
            btn.disabled = true;

            await fetch('/sync', { method: 'POST' });
            showToast("Full sync started for all playlists!");

            setTimeout(() => {
                icon.classList.remove('fa-spin');
                btn.disabled = false;
            }, 2000);
        }

        // Stop / Abort Download
        async function stopDownload() {
            if (!confirm("Are you sure you want to stop the active download?")) return;
            try {
                await fetch('/api/stop', { method: 'POST' });
                showToast("Download cancelled.");
                activeDownloadingId = null;
                hideDownloadDock();
                updateStatusBadge(false);
            } catch (err) {
                showToast("Failed to stop download", true);
            }
        }

        // ================= LIBRARY MANAGEMENT =================
        async function fetchLibrary() {
            const list = document.getElementById('librarySongsList');
            const summary = document.getElementById('libSummaryText');
            const refreshIcon = document.getElementById('libRefreshIcon');
            if (refreshIcon) refreshIcon.classList.add('fa-spin');

            try {
                const res = await fetch('/api/library');
                const data = await res.json();
                allLibrarySongs = data.songs || [];
                summary.innerText = `${data.total_count} tracks • ${data.storage_display} stored`;
                renderLibrary(allLibrarySongs);
            } catch (err) {
                list.innerHTML = `<div class="p-4 text-center text-rose-400 text-xs">Failed to load library: ${err.message}</div>`;
            } finally {
                if (refreshIcon) refreshIcon.classList.remove('fa-spin');
            }
        }

        function filterLibrary(query) {
            const q = query.toLowerCase().trim();
            if (!q) {
                renderLibrary(allLibrarySongs);
                return;
            }
            const filtered = allLibrarySongs.filter(s =>
                s.title.toLowerCase().includes(q) ||
                s.artist.toLowerCase().includes(q) ||
                s.album.toLowerCase().includes(q)
            );
            renderLibrary(filtered);
        }

        function renderLibrary(songs) {
            const list = document.getElementById('librarySongsList');
            if (!songs || songs.length === 0) {
                list.innerHTML = `
                    <div class="p-8 text-center text-slate-500 text-xs italic">
                        <i class="fa-solid fa-folder-open text-xl mb-1.5 block"></i>
                        No matching songs found in library.
                    </div>
                `;
                return;
            }

            list.innerHTML = songs.map(song => `
                <div class="glass-panel p-2.5 rounded-2xl flex items-center justify-between gap-3 hover:border-slate-700 transition" id="lib-song-${escapePathId(song.path)}">
                    <!-- Play Preview Button -->
                    <button onclick="playSongPreview('${escapeHtml(song.path)}', '${escapeHtml(song.title)}', '${escapeHtml(song.artist)}')"
                        class="w-9 h-9 rounded-xl bg-pink-600/20 hover:bg-pink-600 text-pink-400 hover:text-white flex items-center justify-center text-xs shrink-0 transition" title="Preview track">
                        <i class="fa-solid fa-play"></i>
                    </button>

                    <!-- Title & Details -->
                    <div class="min-w-0 flex-1">
                        <h4 class="text-xs font-semibold text-slate-100 line-clamp-1 leading-snug break-words" title="${escapeHtml(song.title)}">${song.title}</h4>
                        <div class="text-3xs text-slate-400 mt-0.5 flex items-center gap-2">
                            <span class="truncate max-w-[120px]">${song.artist}</span>
                            <span>•</span>
                            <span>${song.size_mb} MB</span>
                            <span>•</span>
                            <span class="hidden sm:inline">${song.date}</span>
                        </div>
                    </div>

                    <!-- Delete Button -->
                    <button onclick="deleteSong('${escapeHtml(song.path)}', '${escapeHtml(song.title)}')"
                        class="touch-action-btn p-2 text-slate-500 hover:text-rose-400 transition text-xs shrink-0" title="Delete song from server">
                        <i class="fa-solid fa-trash-can"></i>
                    </button>
                </div>
            `).join('');
        }

        function escapePathId(p) {
            return encodeURIComponent(p).replace(/[^a-zA-Z0-9]/g, '_');
        }

        async function deleteSong(path, title) {
            if (!confirm(`Delete "${title}" permanently from your server?`)) return;
            
            try {
                const formData = new FormData();
                formData.append('path', path);
                const res = await fetch('/api/library/delete', { method: 'POST', body: formData });
                const data = await res.json();
                
                if (data.status === 'ok') {
                    showToast(`Deleted: ${title}`);
                    const el = document.getElementById(`lib-song-${escapePathId(path)}`);
                    if (el) el.remove();
                    // Update cache
                    allLibrarySongs = allLibrarySongs.filter(s => s.path !== path);
                    const summary = document.getElementById('libSummaryText');
                    if (summary) summary.innerText = `${allLibrarySongs.length} tracks`;
                } else {
                    showToast(data.error || "Failed to delete song", true);
                }
            } catch (err) {
                showToast("Error deleting song", true);
            }
        }

        // ================= IN-BROWSER AUDIO PREVIEW =================
        function playSongPreview(relPath, title, artist) {
            const player = document.getElementById('globalAudioPlayer');
            const dock = document.getElementById('floatingDock');
            const bar = document.getElementById('audioPreviewBar');
            const titleEl = document.getElementById('previewSongTitle');
            const artistEl = document.getElementById('previewSongArtist');
            const icon = document.getElementById('previewPlayIcon');

            player.src = `/api/library/stream?path=${encodeURIComponent(relPath)}`;
            player.play().catch(() => {});

            titleEl.innerText = title;
            artistEl.innerText = artist;
            icon.className = "fa-solid fa-pause";

            dock.classList.remove('hidden');
            bar.classList.remove('hidden');
        }

        function toggleAudioPreview() {
            const player = document.getElementById('globalAudioPlayer');
            const icon = document.getElementById('previewPlayIcon');
            if (player.paused) {
                player.play();
                icon.className = "fa-solid fa-pause";
            } else {
                player.pause();
                icon.className = "fa-solid fa-play";
            }
        }

        function onAudioEnded() {
            const icon = document.getElementById('previewPlayIcon');
            if (icon) icon.className = "fa-solid fa-play";
        }

        function closeAudioPreview() {
            const player = document.getElementById('globalAudioPlayer');
            player.pause();
            player.src = "";
            document.getElementById('audioPreviewBar').classList.add('hidden');
            checkDockVisibility();
        }

        // ================= FLOATING DOCK & STATUS =================
        function updateDownloadDock(dl) {
            const dock = document.getElementById('floatingDock');
            const bar = document.getElementById('activeDownloadBar');
            const title = document.getElementById('dockTrackTitle');
            const speed = document.getElementById('dockSpeedText');
            const status = document.getElementById('dockStatusText');
            const progress = document.getElementById('dockProgressBar');

            title.innerText = dl.title || "Active Download";
            speed.innerText = `${Math.round(dl.percent)}%`;
            status.innerText = dl.speed || dl.status || 'Downloading...';
            progress.style.width = `${Math.max(5, dl.percent)}%`;

            dock.classList.remove('hidden');
            bar.classList.remove('hidden');

            // In-card update if present
            if (dl.id) {
                const cardPct = document.getElementById(`card-pct-${dl.id}`);
                const cardBar = document.getElementById(`card-bar-${dl.id}`);
                if (cardPct) cardPct.innerText = `${Math.round(dl.percent)}%`;
                if (cardBar) cardBar.style.width = `${Math.max(5, dl.percent)}%`;
            }
        }

        function hideDownloadDock() {
            document.getElementById('activeDownloadBar').classList.add('hidden');
            checkDockVisibility();
        }

        function checkDockVisibility() {
            const dlBar = document.getElementById('activeDownloadBar');
            const audioBar = document.getElementById('audioPreviewBar');
            const dock = document.getElementById('floatingDock');
            if (dlBar.classList.contains('hidden') && audioBar.classList.contains('hidden')) {
                dock.classList.add('hidden');
            }
        }

        function updateStatusBadge(syncing) {
            const badge = document.getElementById('headerSyncBadge');
            const stopBtn = document.getElementById('headerStopBtn');
            const logsStopBtn = document.getElementById('logsStopBtn');

            if (syncing) {
                if (badge) {
                    badge.className = "px-2 py-0.5 text-3xs rounded-full font-medium bg-amber-500/20 text-amber-300 border border-amber-500/30 animate-pulse";
                    badge.innerText = "⚡ Syncing";
                }
                if (stopBtn) stopBtn.classList.remove('hidden');
                if (logsStopBtn) logsStopBtn.classList.remove('hidden');
            } else {
                if (badge) {
                    badge.className = "px-2 py-0.5 text-3xs rounded-full font-medium bg-emerald-500/10 text-emerald-400 border border-emerald-500/20";
                    badge.innerText = "● Ready";
                }
                if (stopBtn) stopBtn.classList.add('hidden');
                if (logsStopBtn) logsStopBtn.classList.add('hidden');
            }
        }

        // Fetch Logs
        async function fetchLogs() {
            try {
                const res = await fetch('/api/logs');
                const data = await res.json();
                document.getElementById('logConsole').innerText = data.logs;
                updateStatusBadge(data.is_syncing);
            } catch (e) {}
        }

        // Periodic Background Status Poller
        setInterval(() => {
            const autoLogs = document.getElementById('autoRefreshLogs');
            const logsTabVisible = !document.getElementById('tab-logs').classList.contains('hidden');
            if (logsTabVisible && autoLogs && autoLogs.checked) {
                fetchLogs();
            }

            fetch('/api/status').then(r => r.json()).then(d => {
                updateStatusBadge(d.is_syncing);
                if (d.is_syncing && d.current_download && d.current_download.status !== 'idle') {
                    activeDownloadingId = d.current_download.id || d.current_download.url;
                    updateDownloadDock(d.current_download);
                } else {
                    if (activeDownloadingId) {
                        // Completed or idle
                        hideDownloadDock();
                        activeDownloadingId = null;
                    }
                }
            }).catch(() => {});
        }, 2200);
    </script>
</body>
</html>
"""

@app.route("/")
def index():
    playlists = get_playlists()
    logs = get_recent_logs(35)
    host = request.host.split(":")[0]
    return render_template_string(
        HTML_TEMPLATE,
        playlists=playlists,
        logs=logs,
        is_syncing=is_syncing,
        host=host,
        cookies_ok=has_cookies()
    )

@app.route("/api/search")
def api_search():
    query = request.args.get("q", "").strip()
    results = search_youtube(query)
    return jsonify({"results": results})

@app.route("/api/library")
def api_library():
    songs = []
    total_bytes = 0
    if os.path.exists(LIBRARY_DIR):
        for root, dirs, files in os.walk(LIBRARY_DIR):
            for file in files:
                if file.lower().endswith((".mp3", ".m4a", ".flac", ".opus", ".ogg")):
                    full_path = os.path.join(root, file)
                    try:
                        stat = os.stat(full_path)
                        size_mb = round(stat.st_size / (1024 * 1024), 2)
                        total_bytes += stat.st_size
                        mtime = stat.st_mtime
                        date_str = datetime.datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M")
                        rel_path = os.path.relpath(full_path, LIBRARY_DIR).replace("\\", "/")
                        parts = rel_path.split("/")
                        artist = parts[0] if len(parts) > 1 else "Unknown Artist"
                        album = parts[1] if len(parts) > 2 else "Singles"
                        title = os.path.splitext(file)[0]
                        songs.append({
                            "title": title,
                            "artist": artist,
                            "album": album,
                            "path": rel_path,
                            "size_mb": size_mb,
                            "date": date_str,
                            "timestamp": mtime
                        })
                    except Exception:
                        pass
    # Sort newest first
    songs.sort(key=lambda x: x["timestamp"], reverse=True)
    total_mb = round(total_bytes / (1024 * 1024), 1)
    total_gb = round(total_bytes / (1024 * 1024 * 1024), 2)
    storage_display = f"{total_gb} GB" if total_gb >= 1.0 else f"{total_mb} MB"
    return jsonify({
        "songs": songs,
        "total_count": len(songs),
        "storage_display": storage_display
    })

@app.route("/api/library/delete", methods=["POST"])
def api_library_delete():
    rel_path = request.form.get("path") or (request.json and request.json.get("path"))
    if not rel_path:
        return jsonify({"error": "No path provided"}), 400
    
    full_path = os.path.realpath(os.path.join(LIBRARY_DIR, rel_path))
    if not full_path.startswith(os.path.realpath(LIBRARY_DIR)):
        return jsonify({"error": "Access denied"}), 403
    
    if not os.path.exists(full_path):
        return jsonify({"error": "File does not exist"}), 404
        
    try:
        os.remove(full_path)
        # Clean empty folders
        parent = os.path.dirname(full_path)
        lib_root = os.path.realpath(LIBRARY_DIR)
        while parent != lib_root and os.path.exists(parent):
            if not os.listdir(parent):
                os.rmdir(parent)
                parent = os.path.dirname(parent)
            else:
                break
        return jsonify({"status": "ok", "deleted": rel_path})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/library/stream")
def api_library_stream():
    rel_path = request.args.get("path", "")
    full_path = os.path.realpath(os.path.join(LIBRARY_DIR, rel_path))
    if not full_path.startswith(os.path.realpath(LIBRARY_DIR)) or not os.path.isfile(full_path):
        return "File not found", 404
    dir_name = os.path.dirname(full_path)
    file_name = os.path.basename(full_path)
    return send_from_directory(dir_name, file_name, mimetype="audio/mpeg")

@app.route("/api/logs")
def api_logs():
    return jsonify({
        "logs": get_recent_logs(40),
        "is_syncing": is_syncing
    })

@app.route("/api/status")
def api_status():
    with download_lock:
        dl_copy = dict(current_download)
    return jsonify({
        "is_syncing": is_syncing,
        "current_download": dl_copy,
        "playlists_count": len(get_playlists()),
        "cookies_ok": has_cookies()
    })

@app.route("/add", methods=["POST"])
def add():
    url = request.form.get("url", "").strip()
    title = request.form.get("title", "").strip()
    video_id = request.form.get("video_id", "").strip()
    sync_now = request.form.get("sync_now") == "true"
    is_playlist = "list=PL" in url or "list=OL" in url or "list=UU" in url
    
    if url:
        if is_playlist:
            playlists = get_playlists()
            if url not in playlists:
                playlists.append(url)
                save_playlists(playlists)
            if sync_now:
                trigger_sync_async()
        else:
            if sync_now:
                trigger_single_download_async(url, title=title, video_id=video_id)
                
    if request.headers.get("X-Requested-With") == "XMLHttpRequest" or request.is_json:
        return jsonify({"status": "ok", "url": url, "is_playlist": is_playlist})
    return redirect(url_for("index"))

@app.route("/cookies", methods=["POST"])
def update_cookies():
    cookies_content = ""
    file = request.files.get("cookies_file")
    if file and file.filename:
        cookies_content = file.read().decode("utf-8", errors="ignore")
    else:
        cookies_content = request.form.get("cookies_text", "").strip()

    if cookies_content:
        with open(COOKIES_FILE, "w", encoding="utf-8") as f:
            f.write(cookies_content)
    return redirect(url_for("index"))

@app.route("/delete", methods=["POST"])
def delete():
    url = request.form.get("url", "").strip()
    if url:
        playlists = get_playlists()
        if url in playlists:
            playlists.remove(url)
            save_playlists(playlists)
    return redirect(url_for("index"))

@app.route("/sync", methods=["POST"])
def sync():
    trigger_sync_async()
    return redirect(url_for("index"))

@app.route("/api/stop", methods=["POST"])
def api_stop():
    stop_sync_process()
    return jsonify({"status": "stopped", "is_syncing": False})

@app.route("/stop", methods=["POST"])
def stop():
    stop_sync_process()
    return redirect(url_for("index"))

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=4534)
