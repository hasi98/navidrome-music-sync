import os
import subprocess
import threading
import urllib.request
import urllib.parse
import json
import re
import datetime
from flask import Flask, render_template_string, request, redirect, url_for, jsonify

app = Flask(__name__)
app.secret_key = "music_manager_secret_key"

MUSIC_DIR = "/home/ubuntu/music"
PLAYLISTS_FILE = os.path.join(MUSIC_DIR, "playlists.txt")
SYNC_SCRIPT = os.path.join(MUSIC_DIR, "scripts", "sync.sh")
LOG_FILE = os.path.join(MUSIC_DIR, "logs", "sync.log")
COOKIES_FILE = os.path.join(MUSIC_DIR, "cookies.txt")

sync_lock = threading.Lock()
is_syncing = False

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

def run_single_download(url):
    global is_syncing
    with sync_lock:
        is_syncing = True
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
                "--extract-audio",
                "--audio-format", "mp3",
                "--audio-quality", "0",
                "--embed-thumbnail",
                "--embed-metadata",
                "--add-metadata",
                "--download-archive", os.path.join(MUSIC_DIR, "download_archive.txt"),
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
                res = subprocess.run(cmd, stdout=f, stderr=f, check=False)
                subprocess.run(["chmod", "-R", "775", f"{MUSIC_DIR}/library"], check=False)
                end_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                if res.returncode == 0:
                    f.write(f"✓ Single track downloaded successfully at: {end_str}\n==========================================\n")
                else:
                    f.write(f"❌ Download failed (code {res.returncode}) at: {end_str}\nCheck above logs. If 'Sign in to confirm you are not a bot' appears, update cookies in Auth tab.\n==========================================\n")
        except Exception as e:
            with open(LOG_FILE, "a", encoding="utf-8") as f:
                f.write(f"Error during single download: {e}\n")
        finally:
            is_syncing = False

def trigger_single_download_async(url):
    global is_syncing
    if not is_syncing:
        t = threading.Thread(target=run_single_download, args=(url,), daemon=True)
        t.start()
        return True
    return False

def stop_sync_process():
    global is_syncing
    try:
        subprocess.run(["pkill", "-9", "-f", "yt-dlp"], check=False)
        subprocess.run(["pkill", "-9", "-f", "sync.sh"], check=False)
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            f.write(f"\n==========================================\n[CANCELLED] Download process stopped by user via Web UI at {now_str}\n==========================================\n")
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

def search_youtube(query, limit=35):
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
                    if vr:
                        video_id = vr.get("videoId")
                        title = vr.get("title", {}).get("runs", [{}])[0].get("text", "")
                        author = vr.get("ownerText", {}).get("runs", [{}])[0].get("text", "")
                        duration = vr.get("lengthText", {}).get("simpleText", "")
                        thumbs = vr.get("thumbnail", {}).get("thumbnails", [])
                        thumb = thumbs[-1]["url"] if thumbs else f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg"
                        if video_id and title:
                            items.append({
                                "type": "video",
                                "id": video_id,
                                "title": title,
                                "channel": author,
                                "duration": duration,
                                "thumbnail": thumb,
                                "url": f"https://www.youtube.com/watch?v={video_id}"
                            })
                    elif pr:
                        pl_id = pr.get("playlistId")
                        title = pr.get("title", {}).get("simpleText", "")
                        author = pr.get("shortBylineText", {}).get("runs", [{}])[0].get("text", "")
                        count = pr.get("videoCount", "")
                        thumbs = pr.get("thumbnails", [{}])[0].get("thumbnails", [])
                        thumb = thumbs[-1]["url"] if thumbs else ""
                        if pl_id and title:
                            items.append({
                                "type": "playlist",
                                "id": pl_id,
                                "title": title,
                                "channel": author,
                                "duration": f"{count} videos",
                                "thumbnail": thumb,
                                "url": f"https://www.youtube.com/playlist?list={pl_id}"
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
                    items.append({
                        "type": "video",
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
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
    <meta name="theme-color" content="#090d16">
    <meta name="apple-mobile-web-app-capable" content="yes">
    <meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
    <title>Navidrome Music Sync</title>
    
    <!-- Tailwind CSS & Icons -->
    <script src="https://cdn.tailwindcss.com"></script>
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.5.1/css/all.min.css">
    
    <script>
        tailwind.config = {
            darkMode: 'class',
            theme: {
                extend: {
                    colors: {
                        brand: {
                            50: '#fdf2f8',
                            500: '#ec4899',
                            600: '#db2777',
                            accent: '#6366f1'
                        }
                    }
                }
            }
        }
    </script>
    
    <style>
        body {
            background-color: #090d16;
            color: #f1f5f9;
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
            -webkit-tap-highlight-color: transparent;
        }
        /* Custom scrollbar */
        ::-webkit-scrollbar { width: 5px; height: 5px; }
        ::-webkit-scrollbar-track { background: #0b1120; }
        ::-webkit-scrollbar-thumb { background: #1e293b; border-radius: 4px; }
        ::-webkit-scrollbar-thumb:hover { background: #334155; }
        .glass-panel {
            background: rgba(15, 23, 42, 0.75);
            backdrop-filter: blur(16px);
            -webkit-backdrop-filter: blur(16px);
            border: 1px solid rgba(255, 255, 255, 0.08);
        }
        .touch-action-btn:active {
            transform: scale(0.96);
        }
    </style>
</head>
<body class="min-h-screen pb-20 md:pb-8 flex flex-col items-center antialiased selection:bg-rose-500 selection:text-white">

    <!-- Toast Notification Container -->
    <div id="toastBox" class="fixed top-4 z-50 left-1/2 -translate-x-1/2 w-11/12 max-w-sm pointer-events-none transition-all duration-300 opacity-0 -translate-y-4"></div>

    <div class="w-full max-w-xl px-3 sm:px-4 py-3 sm:py-6 space-y-4">
        
        <!-- Top App Bar -->
        <header class="glass-panel p-3.5 sm:p-4 rounded-2xl shadow-xl flex items-center justify-between sticky top-2 z-40">
            <div class="flex items-center space-x-3">
                <div class="w-10 h-10 rounded-xl bg-gradient-to-tr from-rose-500 via-pink-600 to-indigo-600 flex items-center justify-center text-white text-lg shadow-lg shadow-rose-500/20">
                    <i class="fa-solid fa-compact-disc animate-[spin_10s_linear_infinite]"></i>
                </div>
                <div>
                    <h1 class="text-base sm:text-lg font-bold tracking-tight text-white flex items-center gap-1.5">
                        <span>TubeSync</span>
                        <span id="headerSyncBadge" class="hidden sm:inline-block px-2 py-0.5 text-2xs rounded-full font-medium bg-emerald-500/10 text-emerald-400 border border-emerald-500/20">
                            {% if is_syncing %}⚡ Syncing{% else %}● Ready{% endif %}
                        </span>
                    </h1>
                    <p class="text-2xs sm:text-xs text-slate-400">Navidrome Cloud Music</p>
                </div>
            </div>

            <div class="flex items-center space-x-2">
                <!-- Status Pill (Mobile) -->
                <span id="mobileStatusPill" class="sm:hidden px-2 py-1 text-2xs rounded-full font-medium {% if is_syncing %}bg-amber-500/20 text-amber-300 border border-amber-500/30 animate-pulse{% else %}bg-emerald-500/20 text-emerald-400 border border-emerald-500/30{% endif %}">
                    {% if is_syncing %}Syncing...{% else %}Ready{% endif %}
                </span>

                <!-- Stop Download Button (Visible when downloading) -->
                <button onclick="stopDownload()" id="headerStopBtn" class="{% if not is_syncing %}hidden{% endif %} touch-action-btn px-2.5 py-1.5 bg-rose-600/30 hover:bg-rose-600 text-rose-300 hover:text-white border border-rose-500/50 rounded-xl text-2xs font-bold transition flex items-center space-x-1 animate-pulse" title="Cancel active download">
                    <i class="fa-solid fa-stop text-3xs"></i>
                    <span>Stop</span>
                </button>

                <!-- Open Navidrome Link -->
                <a href="http://{{ host }}:4533" target="_blank"
                    class="touch-action-btn px-3 py-2 bg-gradient-to-r from-indigo-600 to-violet-600 hover:from-indigo-500 hover:to-violet-500 text-white text-xs font-semibold rounded-xl shadow-md flex items-center space-x-1.5 transition">
                    <i class="fa-solid fa-headphones"></i>
                    <span class="hidden xs:inline">Player</span>
                </a>
            </div>
        </header>

        <!-- Navigation Tabs (Mobile Segmented Control) -->
        <nav class="glass-panel p-1.5 rounded-2xl flex items-center justify-between text-xs font-medium space-x-1">
            <button onclick="switchTab('search')" id="tabBtn-search" class="tab-btn flex-1 py-2 px-2 rounded-xl text-center transition flex items-center justify-center space-x-1.5 bg-rose-600 text-white shadow-sm font-semibold">
                <i class="fa-solid fa-magnifying-glass"></i>
                <span>Search</span>
            </button>
            <button onclick="switchTab('direct')" id="tabBtn-direct" class="tab-btn flex-1 py-2 px-2 rounded-xl text-center transition flex items-center justify-center space-x-1.5 text-slate-400 hover:text-slate-200">
                <i class="fa-solid fa-link"></i>
                <span class="hidden xs:inline">Link</span>
            </button>
            <button onclick="switchTab('playlists')" id="tabBtn-playlists" class="tab-btn flex-1 py-2 px-2 rounded-xl text-center transition flex items-center justify-center space-x-1.5 text-slate-400 hover:text-slate-200">
                <i class="fa-solid fa-list-ul"></i>
                <span class="hidden xs:inline">Library</span>
                <span id="plCountBadge" class="ml-0.5 px-1.5 py-0.2 bg-slate-800 text-slate-300 rounded-full text-2xs font-mono">{{ playlists|length }}</span>
            </button>
            <button onclick="switchTab('cookies')" id="tabBtn-cookies" class="tab-btn flex-1 py-2 px-2 rounded-xl text-center transition flex items-center justify-center space-x-1.5 text-slate-400 hover:text-slate-200 relative">
                <i class="fa-solid fa-cookie-bite"></i>
                <span class="hidden xs:inline">Auth</span>
                {% if not cookies_ok %}
                <span class="w-2 h-2 rounded-full bg-amber-400 absolute top-1.5 right-1.5 animate-ping"></span>
                <span class="w-2 h-2 rounded-full bg-amber-400 absolute top-1.5 right-1.5"></span>
                {% endif %}
            </button>
            <button onclick="switchTab('logs')" id="tabBtn-logs" class="tab-btn flex-1 py-2 px-2 rounded-xl text-center transition flex items-center justify-center space-x-1.5 text-slate-400 hover:text-slate-200">
                <i class="fa-solid fa-terminal"></i>
                <span class="hidden xs:inline">Logs</span>
            </button>
        </nav>

        <!-- ================= TAB 1: SEARCH (DEFAULT) ================= -->
        <section id="tab-search" class="tab-content space-y-4">
            
            <!-- Search Input Box -->
            <div class="glass-panel p-4 rounded-2xl space-y-3 shadow-lg">
                <div class="relative flex items-center">
                    <span class="absolute left-3.5 text-slate-400 pointer-events-none">
                        <i class="fa-solid fa-magnifying-glass"></i>
                    </span>
                    <input type="text" id="searchInput" placeholder="Search track, artist, album..."
                        class="w-full pl-10 pr-10 py-3 bg-slate-900/90 border border-slate-700/80 rounded-xl text-sm focus:outline-none focus:border-rose-500 focus:ring-1 focus:ring-rose-500 transition text-slate-100 placeholder-slate-500 shadow-inner"
                        onkeydown="if(event.key==='Enter') doSearch()">
                    <button id="clearSearchBtn" onclick="clearSearch()" class="hidden absolute right-3 text-slate-500 hover:text-slate-300 p-1">
                        <i class="fa-solid fa-circle-xmark"></i>
                    </button>
                </div>

                <!-- Action Button & Filter Chips -->
                <div class="flex items-center justify-between gap-2">
                    <div class="flex items-center space-x-1.5 overflow-x-auto pb-1 no-scrollbar text-2xs">
                        <button onclick="quickSearch('Daft Punk')" class="touch-action-btn px-2.5 py-1 bg-slate-800/90 hover:bg-slate-700 border border-slate-700/60 rounded-lg text-slate-300 shrink-0">⚡ Daft Punk</button>
                        <button onclick="quickSearch('Synthwave Chill')" class="touch-action-btn px-2.5 py-1 bg-slate-800/90 hover:bg-slate-700 border border-slate-700/60 rounded-lg text-slate-300 shrink-0">🌌 Synthwave</button>
                        <button onclick="quickSearch('Lo-Fi Hip Hop')" class="touch-action-btn px-2.5 py-1 bg-slate-800/90 hover:bg-slate-700 border border-slate-700/60 rounded-lg text-slate-300 shrink-0">☕ Lo-Fi</button>
                        <button onclick="quickSearch('The Weeknd')" class="touch-action-btn px-2.5 py-1 bg-slate-800/90 hover:bg-slate-700 border border-slate-700/60 rounded-lg text-slate-300 shrink-0">🎙️ The Weeknd</button>
                    </div>

                    <button onclick="doSearch()" id="searchBtn"
                        class="touch-action-btn px-4 py-2 bg-gradient-to-r from-rose-600 to-pink-600 hover:from-rose-500 hover:to-pink-500 font-semibold text-xs text-white rounded-xl shadow-md transition flex items-center space-x-1.5 shrink-0">
                        <i class="fa-solid fa-arrow-right"></i>
                        <span>Find</span>
                    </button>
                </div>
            </div>

            <!-- Dedicated Search Results Box Section -->
            <div id="searchResultsSection" class="hidden glass-panel p-4 rounded-2xl space-y-3 border border-slate-700/80 shadow-2xl">
                <div class="flex items-center justify-between pb-2.5 border-b border-slate-800">
                    <div class="flex items-center space-x-2 min-w-0">
                        <span class="text-xs font-semibold uppercase tracking-wider text-slate-300">Results</span>
                        <span id="resultsCountBadge" class="px-2 py-0.5 text-2xs rounded-full font-mono bg-rose-500/10 text-rose-400 border border-rose-500/20">0</span>
                    </div>
                    <button onclick="clearSearch()" class="touch-action-btn text-2xs text-slate-400 hover:text-rose-400 flex items-center space-x-1 p-1 transition">
                        <i class="fa-solid fa-xmark"></i>
                        <span>Dismiss</span>
                    </button>
                </div>

                <!-- Scrollable Box Container -->
                <div id="searchResultsList" class="space-y-2.5 max-h-[460px] overflow-y-auto pr-1"></div>

                <!-- Load More Button -->
                <div id="loadMoreContainer" class="pt-2 hidden border-t border-slate-800/80">
                    <button onclick="loadMoreResults()" id="loadMoreBtn"
                        class="touch-action-btn w-full py-2.5 px-4 bg-slate-800/90 hover:bg-slate-700/90 border border-slate-700/60 rounded-xl text-xs font-semibold text-slate-200 flex items-center justify-center space-x-2 transition shadow">
                        <i class="fa-solid fa-angles-down text-rose-400"></i>
                        <span id="loadMoreBtnText">Load More Results</span>
                    </button>
                </div>
            </div>

            <!-- Empty Search Prompt -->
            <div id="searchPlaceholder" class="p-8 text-center text-slate-500 text-xs italic glass-panel rounded-2xl border-dashed">
                <i class="fa-solid fa-music text-slate-600 text-2xl mb-2 block"></i>
                Search above to download any song or album in 320kbps MP3
            </div>
        </section>

        <!-- ================= TAB 2: DIRECT LINK ================= -->
        <section id="tab-direct" class="tab-content hidden space-y-4">
            <div class="glass-panel p-5 rounded-2xl shadow-lg space-y-4">
                <div class="flex items-center space-x-2">
                    <div class="w-7 h-7 rounded-lg bg-indigo-500/20 text-indigo-400 flex items-center justify-center text-xs">
                        <i class="fa-solid fa-link"></i>
                    </div>
                    <h2 class="text-sm font-semibold text-slate-200">Paste YouTube Link</h2>
                </div>
                <p class="text-2xs text-slate-400">Supports individual songs, albums, and full public/unlisted YouTube playlists.</p>

                <form id="directAddForm" onsubmit="submitDirectLink(event)" class="space-y-3">
                    <div>
                        <input type="url" id="directUrlInput" placeholder="https://www.youtube.com/playlist?list=... or watch URL" required
                            class="w-full px-3.5 py-2.5 bg-slate-900 border border-slate-700/80 rounded-xl text-xs text-slate-100 placeholder-slate-500 focus:outline-none focus:border-indigo-500">
                    </div>
                    <div class="flex items-center justify-between pt-1">
                        <label class="flex items-center space-x-2 text-2xs text-slate-300 cursor-pointer">
                            <input type="checkbox" id="directSyncNow" checked class="rounded border-slate-700 text-indigo-600 focus:ring-0">
                            <span>Start download immediately</span>
                        </label>
                        <button type="submit" id="directAddBtn"
                            class="touch-action-btn px-4 py-2 bg-indigo-600 hover:bg-indigo-500 text-white font-semibold text-xs rounded-xl shadow transition flex items-center space-x-1.5">
                            <i class="fa-solid fa-plus"></i>
                            <span>Add & Queue</span>
                        </button>
                    </div>
                </form>
            </div>
        </section>

        <!-- ================= TAB 3: PLAYLISTS / LIBRARY ================= -->
        <section id="tab-playlists" class="tab-content hidden space-y-4">
            <div class="glass-panel p-5 rounded-2xl shadow-lg space-y-4">
                <div class="flex items-center justify-between">
                    <div class="flex items-center space-x-2">
                        <div class="w-7 h-7 rounded-lg bg-emerald-500/20 text-emerald-400 flex items-center justify-center text-xs">
                            <i class="fa-solid fa-list-check"></i>
                        </div>
                        <h2 class="text-sm font-semibold text-slate-200">Tracked Sources</h2>
                    </div>

                    <div class="flex items-center space-x-2">
                        <button onclick="stopDownload()" id="tabStopBtn"
                            class="{% if not is_syncing %}hidden{% endif %} touch-action-btn px-2.5 py-1.5 bg-rose-600/20 hover:bg-rose-600 text-rose-300 hover:text-white border border-rose-500/40 rounded-xl text-2xs font-semibold transition flex items-center space-x-1.5 animate-pulse">
                            <i class="fa-solid fa-stop text-3xs"></i>
                            <span>Stop Sync</span>
                        </button>
                        <button onclick="triggerFullSync()" id="syncAllBtn"
                            class="touch-action-btn px-3 py-1.5 bg-slate-800 hover:bg-slate-700 text-slate-200 border border-slate-700/60 rounded-xl text-2xs font-semibold transition flex items-center space-x-1.5">
                            <i class="fa-solid fa-rotate" id="syncAllIcon"></i>
                            <span>Sync All Now</span>
                        </button>
                    </div>
                </div>

                <div id="playlistsList" class="divide-y divide-slate-800/80 max-h-80 overflow-y-auto pr-1">
                    {% if playlists %}
                        {% for pl in playlists %}
                        <div class="py-3 flex items-center justify-between space-x-3 group">
                            <div class="truncate flex items-center space-x-2.5 text-xs text-slate-300 min-w-0">
                                <i class="fa-brands fa-youtube text-rose-500 text-xs shrink-0"></i>
                                <span class="truncate font-mono text-2xs">{{ pl }}</span>
                            </div>
                            <button onclick="deletePlaylist('{{ pl }}')" class="touch-action-btn text-slate-500 hover:text-rose-400 p-1.5 transition text-xs shrink-0" title="Remove link">
                                <i class="fa-solid fa-trash-can"></i>
                            </button>
                        </div>
                        {% endfor %}
                    {% else %}
                        <p class="text-2xs text-slate-500 italic text-center py-6">No links tracked yet. Use the Search tab to queue songs!</p>
                    {% endif %}
                </div>
            </div>
        </section>

        <!-- ================= TAB 4: COOKIES & AUTH ================= -->
        <section id="tab-cookies" class="tab-content hidden space-y-4">
            <div class="glass-panel p-5 rounded-2xl shadow-lg space-y-4">
                <div class="flex items-center justify-between">
                    <div class="flex items-center space-x-2">
                        <div class="w-7 h-7 rounded-lg bg-amber-500/20 text-amber-400 flex items-center justify-center text-xs">
                            <i class="fa-solid fa-shield-halved"></i>
                        </div>
                        <h2 class="text-sm font-semibold text-slate-200">YouTube Authentication</h2>
                    </div>

                    {% if cookies_ok %}
                    <span class="px-2 py-0.5 rounded-full text-2xs bg-emerald-500/10 text-emerald-400 border border-emerald-500/20 font-medium flex items-center gap-1">
                        <i class="fa-solid fa-check"></i> Active
                    </span>
                    {% else %}
                    <span class="px-2 py-0.5 rounded-full text-2xs bg-amber-500/20 text-amber-300 border border-amber-500/30 font-medium animate-pulse flex items-center gap-1">
                        <i class="fa-solid fa-triangle-exclamation"></i> Required
                    </span>
                    {% endif %}
                </div>

                <p class="text-2xs text-slate-400 leading-relaxed">
                    YouTube blocks cloud datacenter IPs with <i>"Sign in to confirm you're not a bot"</i>. Pasting your browser cookies bypasses this permanently.
                </p>

                <!-- Step-by-Step Dropdown -->
                <details class="bg-slate-900/80 rounded-xl border border-slate-800 p-3 text-2xs text-slate-300">
                    <summary class="cursor-pointer font-semibold text-amber-400 hover:text-amber-300 flex items-center justify-between">
                        <span>How to export cookies in 30 seconds</span>
                        <i class="fa-solid fa-chevron-down text-2xs transition-transform"></i>
                    </summary>
                    <ol class="mt-2.5 space-y-1.5 text-slate-400 list-decimal list-inside pl-1">
                        <li>Install Chrome/Firefox extension: <b class="text-slate-200">"Get cookies.txt LOCALLY"</b>.</li>
                        <li>Open <a href="https://www.youtube.com" target="_blank" class="text-indigo-400 underline">youtube.com</a> logged into your account.</li>
                        <li>Click the extension icon and copy/export the cookies text.</li>
                        <li>Paste or upload the file below and tap <b>Save Cookies</b>.</li>
                    </ol>
                </details>

                <form action="/cookies" method="POST" enctype="multipart/form-data" class="space-y-3 pt-1">
                    <div>
                        <textarea name="cookies_text" rows="3" placeholder="Paste your Netscape format cookies text here..."
                            class="w-full px-3 py-2 bg-slate-900 border border-slate-700/80 rounded-xl text-2xs font-mono focus:outline-none focus:border-amber-500 text-slate-200 placeholder-slate-600"></textarea>
                    </div>
                    <div class="flex flex-col sm:flex-row sm:items-center justify-between gap-2">
                        <div class="text-2xs text-slate-400 flex items-center space-x-1.5">
                            <span>Or upload:</span>
                            <input type="file" name="cookies_file" accept=".txt" class="text-2xs text-slate-400 file:mr-2 file:py-1 file:px-2.5 file:rounded-lg file:border-0 file:text-2xs file:bg-slate-800 file:text-slate-200 hover:file:bg-slate-700 cursor-pointer">
                        </div>
                        <button type="submit" class="touch-action-btn px-4 py-2 bg-amber-600 hover:bg-amber-500 text-white font-semibold text-xs rounded-xl shadow transition">
                            Save Cookies
                        </button>
                    </div>
                </form>
            </div>
        </section>

        <!-- ================= TAB 5: LIVE LOGS ================= -->
        <section id="tab-logs" class="tab-content hidden space-y-4">
            <div class="glass-panel p-4 rounded-2xl shadow-lg space-y-3">
                <div class="flex items-center justify-between">
                    <div class="flex items-center space-x-2">
                        <div class="w-7 h-7 rounded-lg bg-slate-800 text-slate-300 flex items-center justify-center text-xs">
                            <i class="fa-solid fa-terminal"></i>
                        </div>
                        <h2 class="text-sm font-semibold text-slate-200">Live Sync Terminal</h2>
                    </div>

                    <div class="flex items-center space-x-2">
                        <button onclick="stopDownload()" id="logsStopBtn"
                            class="{% if not is_syncing %}hidden{% endif %} touch-action-btn px-2 py-1 bg-rose-600/20 hover:bg-rose-600 text-rose-300 hover:text-white border border-rose-500/40 rounded-lg text-2xs font-semibold transition flex items-center space-x-1 animate-pulse">
                            <i class="fa-solid fa-stop text-3xs"></i>
                            <span>Cancel</span>
                        </button>
                        <label class="flex items-center space-x-1.5 text-2xs text-slate-400 cursor-pointer">
                            <input type="checkbox" id="autoRefreshLogs" checked class="rounded border-slate-700 text-rose-500 focus:ring-0">
                            <span>Auto</span>
                        </label>
                        <button onclick="fetchLogs()" class="touch-action-btn p-1.5 text-slate-400 hover:text-slate-200 text-xs">
                            <i class="fa-solid fa-arrows-rotate"></i>
                        </button>
                    </div>
                </div>

                <div class="relative">
                    <pre id="logConsole" class="bg-slate-950 p-3 rounded-xl text-2xs font-mono text-emerald-400/90 overflow-x-auto max-h-72 border border-slate-800/80 whitespace-pre-wrap leading-relaxed">{{ logs }}</pre>
                </div>
            </div>
        </section>

    </div>

    <!-- Scripts -->
    <script>
        // Tab Navigation
        function switchTab(tabName) {
            document.querySelectorAll('.tab-content').forEach(el => el.classList.add('hidden'));
            document.querySelectorAll('.tab-btn').forEach(btn => {
                btn.classList.remove('bg-rose-600', 'text-white', 'shadow-sm', 'font-semibold');
                btn.classList.add('text-slate-400');
            });

            const activeTab = document.getElementById(`tab-${tabName}`);
            const activeBtn = document.getElementById(`tabBtn-${tabName}`);
            if (activeTab && activeBtn) {
                activeTab.classList.remove('hidden');
                activeBtn.classList.add('bg-rose-600', 'text-white', 'shadow-sm', 'font-semibold');
                activeBtn.classList.remove('text-slate-400');
            }

            if (tabName === 'logs') {
                fetchLogs();
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

        // Search Engine & Pagination State
        let allSearchResults = [];
        let displayedCount = 0;
        const BATCH_SIZE = 8;

        function renderBatch() {
            const list = document.getElementById('searchResultsList');
            const loadMoreContainer = document.getElementById('loadMoreContainer');
            const loadMoreBtnText = document.getElementById('loadMoreBtnText');
            const badge = document.getElementById('resultsCountBadge');

            const nextBatch = allSearchResults.slice(displayedCount, displayedCount + BATCH_SIZE);
            displayedCount += nextBatch.length;

            const html = nextBatch.map(item => `
                <div class="glass-panel p-2.5 rounded-2xl flex items-center justify-between space-x-3 hover:border-slate-700 transition">
                    <div class="relative w-20 h-14 rounded-xl overflow-hidden shrink-0 bg-slate-950 border border-slate-800">
                        <img src="${item.thumbnail}" alt="" class="w-full h-full object-cover">
                        ${item.duration ? `<span class="absolute bottom-1 right-1 bg-black/80 text-white text-3xs px-1.5 py-0.2 rounded font-mono font-medium">${item.duration}</span>` : ''}
                    </div>
                    <div class="min-w-0 flex-1">
                        <h3 class="text-xs font-semibold text-slate-100 line-clamp-1 leading-snug">${item.title}</h3>
                        <p class="text-2xs text-slate-400 truncate mt-0.5 flex items-center gap-1">
                            <span>${item.channel}</span>
                            ${item.type === 'playlist' ? '<span class="text-indigo-400 font-semibold">• Playlist</span>' : ''}
                        </p>
                    </div>
                    <button onclick="downloadTrack('${item.url}', this)"
                        class="touch-action-btn px-3 py-2 bg-gradient-to-r from-rose-600 to-pink-600 hover:from-rose-500 hover:to-pink-500 text-white font-semibold text-2xs rounded-xl shadow-md transition shrink-0 flex items-center space-x-1">
                        <i class="fa-solid fa-download"></i>
                        <span>Download</span>
                    </button>
                </div>
            `).join('');

            list.insertAdjacentHTML('beforeend', html);
            badge.innerText = `${displayedCount} of ${allSearchResults.length}`;

            if (displayedCount < allSearchResults.length) {
                loadMoreContainer.classList.remove('hidden');
                loadMoreBtnText.innerText = `Load More (+${Math.min(BATCH_SIZE, allSearchResults.length - displayedCount)})`;
            } else {
                loadMoreContainer.classList.add('hidden');
            }
        }

        function loadMoreResults() {
            renderBatch();
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
            const badge = document.getElementById('resultsCountBadge');

            clearBtn.classList.remove('hidden');
            btn.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i>';
            btn.disabled = true;

            placeholder.classList.add('hidden');
            section.classList.remove('hidden');
            loadMoreContainer.classList.add('hidden');
            badge.innerText = 'Searching...';

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
                            No results found for "${query}". Try another search term.
                        </div>
                    `;
                    badge.innerText = '0';
                    return;
                }

                allSearchResults = data.results;
                displayedCount = 0;
                list.innerHTML = '';
                renderBatch();

            } catch (err) {
                list.innerHTML = `<div class="p-6 text-center text-rose-400 text-xs">Search error: ${err.message}</div>`;
                badge.innerText = 'Error';
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

        // Direct Download Action
        async function downloadTrack(url, btnElement) {
            if (btnElement) {
                btnElement.disabled = true;
                btnElement.className = "px-3 py-2 bg-emerald-600 text-white font-semibold text-2xs rounded-xl shrink-0 flex items-center space-x-1";
                btnElement.innerHTML = '<i class="fa-solid fa-check"></i><span>Queued!</span>';
            }

            try {
                const formData = new FormData();
                formData.append('url', url);
                formData.append('sync_now', 'true');

                const res = await fetch('/add', { method: 'POST', body: formData });
                showToast("Track added! Download started in background.");
                updateStatusBadge(true);
            } catch (err) {
                showToast("Failed to queue download", true);
            }
        }

        // Direct Link Form Submit
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
                btn.innerHTML = '<i class="fa-solid fa-plus"></i><span>Add & Queue</span>';
            }
        }

        // Delete Playlist
        async function deletePlaylist(url) {
            if (!confirm("Remove this link from automatic sync?")) return;
            const formData = new FormData();
            formData.append('url', url);
            await fetch('/delete', { method: 'POST', body: formData });
            showToast("Removed from tracking list");
            setTimeout(() => location.reload(), 600);
        }

        // Full Sync Action
        async function triggerFullSync() {
            const btn = document.getElementById('syncAllBtn');
            const icon = document.getElementById('syncAllIcon');
            icon.classList.add('fa-spin');
            btn.disabled = true;

            await fetch('/sync', { method: 'POST' });
            showToast("Full sync started for all playlists!");
            updateStatusBadge(true);

            setTimeout(() => {
                icon.classList.remove('fa-spin');
                btn.disabled = false;
            }, 2000);
        }

        // Stop/Abort Download Action
        async function stopDownload() {
            if (!confirm("Are you sure you want to stop the active download?")) return;
            try {
                const res = await fetch('/api/stop', { method: 'POST' });
                const data = await res.json();
                showToast("Download cancelled successfully.");
                updateStatusBadge(false);
                if (typeof fetchLogs === 'function') fetchLogs();
            } catch (err) {
                showToast("Failed to stop download", true);
            }
        }

        // Status updater
        function updateStatusBadge(syncing) {
            const badge = document.getElementById('headerSyncBadge');
            const mobilePill = document.getElementById('mobileStatusPill');
            const stopBtns = [
                document.getElementById('headerStopBtn'),
                document.getElementById('tabStopBtn'),
                document.getElementById('logsStopBtn')
            ];

            stopBtns.forEach(btn => {
                if (btn) {
                    if (syncing) btn.classList.remove('hidden');
                    else btn.classList.add('hidden');
                }
            });

            if (syncing) {
                if (badge) {
                    badge.className = "hidden sm:inline-block px-2 py-0.5 text-2xs rounded-full font-medium bg-amber-500/20 text-amber-300 border border-amber-500/30 animate-pulse";
                    badge.innerText = "⚡ Syncing";
                }
                if (mobilePill) {
                    mobilePill.className = "sm:hidden px-2 py-1 text-2xs rounded-full font-medium bg-amber-500/20 text-amber-300 border border-amber-500/30 animate-pulse";
                    mobilePill.innerText = "Syncing...";
                }
            } else {
                if (badge) {
                    badge.className = "hidden sm:inline-block px-2 py-0.5 text-2xs rounded-full font-medium bg-emerald-500/10 text-emerald-400 border border-emerald-500/20";
                    badge.innerText = "● Ready";
                }
                if (mobilePill) {
                    mobilePill.className = "sm:hidden px-2 py-1 text-2xs rounded-full font-medium bg-emerald-500/20 text-emerald-400 border border-emerald-500/30";
                    mobilePill.innerText = "Ready";
                }
            }
        }

        // Log Fetcher
        async function fetchLogs() {
            try {
                const res = await fetch('/api/logs');
                const data = await res.json();
                const consoleEl = document.getElementById('logConsole');
                consoleEl.innerText = data.logs;
                updateStatusBadge(data.is_syncing);
            } catch (e) {}
        }

        // Periodic background poll for status & logs
        setInterval(() => {
            const autoLogs = document.getElementById('autoRefreshLogs');
            const logsTabVisible = !document.getElementById('tab-logs').classList.contains('hidden');
            if (logsTabVisible && autoLogs && autoLogs.checked) {
                fetchLogs();
            } else {
                // background status ping
                fetch('/api/status').then(r => r.json()).then(d => {
                    updateStatusBadge(d.is_syncing);
                }).catch(() => {});
            }
        }, 3500);
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

@app.route("/api/logs")
def api_logs():
    return jsonify({
        "logs": get_recent_logs(40),
        "is_syncing": is_syncing
    })

@app.route("/api/status")
def api_status():
    return jsonify({
        "is_syncing": is_syncing,
        "playlists_count": len(get_playlists()),
        "cookies_ok": has_cookies()
    })

@app.route("/add", methods=["POST"])
def add():
    url = request.form.get("url", "").strip()
    sync_now = request.form.get("sync_now") == "true"
    is_playlist = "list=PL" in url or "list=OL" in url or "list=UU" in url
    
    if url:
        if is_playlist:
            # Persistent playlist: save in playlists.txt and run sync
            playlists = get_playlists()
            if url not in playlists:
                playlists.append(url)
                save_playlists(playlists)
            if sync_now:
                trigger_sync_async()
        else:
            # Single track from search or direct URL: download immediately without re-checking old playlists!
            if sync_now:
                trigger_single_download_async(url)
                
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
