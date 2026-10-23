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
abort_queue = False
completed_download_ids = set()

current_download = {
    "url": "",
    "id": "",
    "title": "",
    "percent": 0.0,
    "speed": "",
    "eta": "",
    "status": "idle",  # "idle", "starting", "downloading", "converting", "finalizing", "completed", "failed", "cancelled"
    "message": "",
    "error": "",
    "queue_text": "",
    "playlist_title": ""
}

playlist_queue = {
    "is_active": False,
    "title": "",
    "total": 0,
    "current_index": 0,
    "current_id": "",
    "completed_ids": [],
    "failed_ids": []
}

def get_library_titles():
    titles = set()
    if os.path.exists(LIBRARY_DIR):
        for root, dirs, files in os.walk(LIBRARY_DIR):
            for file in files:
                if file.lower().endswith((".mp3", ".m4a", ".flac", ".opus", ".ogg")):
                    title = os.path.splitext(file)[0].lower()
                    titles.add(title)
    return titles

def is_track_in_library(track_title):
    clean = re.sub(r"[^\w\s]", "", track_title.lower()).strip()
    if not clean:
        return False
    lib_titles = get_library_titles()
    for lt in lib_titles:
        clean_lt = re.sub(r"[^\w\s]", "", lt).strip()
        if clean in clean_lt or clean_lt in clean:
            return True
    return False

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

def run_single_download(url, title="", video_id="", queue_info=None):
    global is_syncing, current_download, completed_download_ids
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
                "message": "Initializing download...",
                "error": "",
                "queue_text": f"Track {queue_info['current']} of {queue_info['total']}" if queue_info else "",
                "playlist_title": queue_info.get("playlist_title", "") if queue_info else ""
            })

        error_reason = ""
        try:
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
                f.write(f"\n==========================================\nDownloading track: {clean_url}\nStarted at: {now_str}\n")
                f.flush()

                proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
                
                for line in iter(proc.stdout.readline, ''):
                    f.write(line)
                    f.flush()
                    line_str = line.strip()
                    
                    if "Sign in to confirm" in line_str:
                        error_reason = "YouTube bot check: Update cookies in Auth tab"
                    elif "Video unavailable" in line_str or "Private video" in line_str:
                        error_reason = "Video is private or unavailable"
                    elif "HTTP Error 429" in line_str:
                        error_reason = "Rate limited by YouTube. Please wait."
                    elif line_str.startswith("ERROR:") and not error_reason:
                        error_reason = line_str.replace("ERROR: ", "")[:85]

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
                    f.write(f"✓ Track downloaded successfully at: {end_str}\n==========================================\n")
                    with download_lock:
                        current_download["status"] = "completed"
                        current_download["percent"] = 100.0
                        current_download["speed"] = "Completed!"
                        current_download["error"] = ""
                        if video_id:
                            completed_download_ids.add(video_id)
                    return True
                else:
                    err_msg = error_reason or f"Failed (exit code {ret})"
                    f.write(f"❌ Download failed ({err_msg}) at: {end_str}\n==========================================\n")
                    with download_lock:
                        current_download["status"] = "failed"
                        current_download["error"] = err_msg
                        current_download["message"] = err_msg
                        current_download["percent"] = 0.0
                        current_download["speed"] = "Failed"
                    return False
        except Exception as e:
            with open(LOG_FILE, "a", encoding="utf-8") as f:
                f.write(f"Error during download: {e}\n")
            with download_lock:
                current_download["status"] = "failed"
                current_download["error"] = str(e)
                current_download["message"] = str(e)
            return False
        finally:
            is_syncing = False
            def reset_idle():
                import time
                time.sleep(8)
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

def run_playlist_download_queue(tracks, playlist_title):
    global is_syncing, abort_queue, playlist_queue, completed_download_ids
    abort_queue = False
    with download_lock:
        playlist_queue["is_active"] = True
        playlist_queue["title"] = playlist_title
        playlist_queue["total"] = len(tracks)
        playlist_queue["current_index"] = 0
        playlist_queue["completed_ids"] = []
        playlist_queue["failed_ids"] = []

    consecutive_bot_errors = 0
    try:
        for idx, tr in enumerate(tracks):
            if abort_queue:
                break
            with download_lock:
                playlist_queue["current_index"] = idx + 1
                playlist_queue["current_id"] = tr["id"]

            success = run_single_download(
                tr["url"],
                title=tr["title"],
                video_id=tr["id"],
                queue_info={"current": idx + 1, "total": len(tracks), "playlist_title": playlist_title}
            )
            with download_lock:
                if success:
                    consecutive_bot_errors = 0
                    playlist_queue["completed_ids"].append(tr["id"])
                    completed_download_ids.add(tr["id"])
                else:
                    playlist_queue["failed_ids"].append(tr["id"])
                    if "bot" in current_download.get("error", "").lower():
                        consecutive_bot_errors += 1
                        if consecutive_bot_errors >= 2:
                            current_download["status"] = "failed"
                            current_download["error"] = "Playlist paused: YouTube bot check. Please update cookies in Auth tab."
                            break
    finally:
        with download_lock:
            playlist_queue["is_active"] = False

def trigger_playlist_queue_async(tracks, playlist_title):
    global is_syncing
    if not is_syncing:
        t = threading.Thread(target=run_playlist_download_queue, args=(tracks, playlist_title), daemon=True)
        t.start()
        return True
    return False

def stop_sync_process():
    global is_syncing, current_download, abort_queue, playlist_queue
    abort_queue = True
    try:
        subprocess.run(["pkill", "-9", "-f", "yt-dlp"], check=False)
        subprocess.run(["pkill", "-9", "-f", "sync.sh"], check=False)
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            f.write(f"\n==========================================\n[CANCELLED] Download stopped by user at {now_str}\n==========================================\n")
        with download_lock:
            current_download["status"] = "cancelled"
            current_download["speed"] = "Cancelled"
            playlist_queue["is_active"] = False
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

    # Fast direct scrape
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
                    # 1. New YouTube 2024 lockupViewModel (Playlists, Mixes & Videos)
                    lvm = item.get("lockupViewModel")
                    if lvm:
                        c_type = lvm.get("contentType", "")
                        c_id = lvm.get("contentId", "")
                        meta = lvm.get("metadata", {}).get("lockupMetadataViewModel", {})
                        title = meta.get("title", {}).get("content", "")
                        
                        rows = meta.get("metadata", {}).get("contentMetadataViewModel", {}).get("metadataRows", [])
                        author = ""
                        duration = ""
                        for r in rows:
                            parts = r.get("metadataParts", [])
                            for p in parts:
                                text = p.get("text", {}).get("content", "")
                                if (":" in text or "video" in text.lower()) and not duration:
                                    duration = text
                                elif not author:
                                    author = text
                                    
                        img = lvm.get("contentImage", {})
                        sources = img.get("thumbnailViewModel", {}).get("image", {}).get("sources", [])
                        if not sources:
                            sources = img.get("collectionThumbnailViewModel", {}).get("primaryThumbnail", {}).get("thumbnailViewModel", {}).get("image", {}).get("sources", [])
                        thumb = sources[-1].get("url") if sources else f"https://i.ytimg.com/vi/{c_id}/hqdefault.jpg"
                        
                        is_pl = "PLAYLIST" in c_type or c_id.startswith("PL") or c_id.startswith("OL")
                        is_rd = "RADIO" in c_type or c_id.startswith("RD") or "mix" in title.lower()
                        cat = "radio" if is_rd else ("playlist" if is_pl else "song")
                        
                        if c_id and title:
                            items.append({
                                "type": "playlist" if is_pl else ("radio" if is_rd else "video"),
                                "category": cat,
                                "id": c_id,
                                "title": title,
                                "channel": author,
                                "duration": duration,
                                "thumbnail": thumb,
                                "url": f"https://www.youtube.com/playlist?list={c_id}" if is_pl else f"https://www.youtube.com/watch?v={c_id}",
                                "in_library": (c_id in completed_download_ids) or is_track_in_library(title)
                            })
                        if len(items) >= limit:
                            break
                        continue

                    # 2. Classic Video Renderer
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
                                "url": f"https://www.youtube.com/watch?v={video_id}",
                                "in_library": (video_id in completed_download_ids) or is_track_in_library(title)
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
                                "url": f"https://www.youtube.com/watch?v={pl_id}&list={pl_id}",
                                "in_library": False
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
                                "url": f"https://www.youtube.com/playlist?list={pl_id}" if not is_radio else f"https://www.youtube.com/watch?v={pl_id}&list={pl_id}",
                                "in_library": False
                            })
                    if len(items) >= limit:
                        break
                if len(items) >= limit:
                    break
            
            if items:
                return items
    except Exception as e:
        print("Scrape search error:", e)

    # Fallback yt-dlp search
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
                        "url": e.get("url") if e.get("url", "").startswith("http") else f"https://www.youtube.com/watch?v={v_id}",
                        "in_library": (v_id in completed_download_ids) or is_track_in_library(v_title)
                    })
            if items:
                return items
    except Exception as e:
        print("yt-dlp fallback search error:", e)

    return []

def inspect_playlist(url):
    url = (url or "").strip()
    if not url:
        return None

    # Detect IDs
    list_m = re.search(r"[?&]list=([a-zA-Z0-9_-]+)", url)
    pl_id = list_m.group(1) if list_m else None

    vid_m = re.search(r"(?:v=|\/v\/|youtu\.be\/|\/embed\/|\/shorts\/|^)([a-zA-Z0-9_-]{11})(?:[?&/]|$)", url)
    vid = vid_m.group(1) if vid_m else None

    is_radio = bool(pl_id and (pl_id.startswith("RD") or pl_id.startswith("UL")))
    is_real_playlist = bool(pl_id and not is_radio)

    cookie_arg = []
    if os.path.exists(COOKIES_FILE) and os.path.getsize(COOKIES_FILE) > 10:
        cookie_arg = ["--cookies", COOKIES_FILE]

    # 1. If real playlist or dynamic radio mix, inspect via yt-dlp flat-playlist with a safe limit
    if is_real_playlist or is_radio:
        limit = "30" if is_radio else "100"
        cmd = [
            "/usr/local/bin/yt-dlp",
            *cookie_arg,
            "--flat-playlist",
            "--playlist-end", limit,
            "--dump-single-json",
            "--no-warnings",
            url
        ]
        res = None
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=16)
        except Exception as e:
            print("yt-dlp inspect error/timeout with cookies:", e)

        # Retry without cookies if cookies failed or produced empty output
        if (not res or res.returncode != 0 or not (res.stdout and res.stdout.strip())) and cookie_arg:
            try:
                cmd_no_cookies = [
                    "/usr/local/bin/yt-dlp",
                    "--flat-playlist",
                    "--playlist-end", limit,
                    "--dump-single-json",
                    "--no-warnings",
                    url
                ]
                res = subprocess.run(cmd_no_cookies, capture_output=True, text=True, timeout=16)
            except Exception as e:
                print("yt-dlp inspect error/timeout without cookies:", e)

        if res and res.stdout:
            try:
                data = json.loads(res.stdout)
                title = data.get("title") or ("YouTube Mix" if is_radio else "YouTube Playlist")
                entries = data.get("entries") or []
                tracks = []
                for e in entries:
                    v_id = e.get("id")
                    v_title = e.get("title")
                    if v_id and v_title:
                        duration_sec = e.get("duration") or 0
                        mins = int(duration_sec // 60)
                        secs = int(duration_sec % 60)
                        dur_str = f"{mins}:{secs:02d}" if duration_sec else ""
                        in_lib = (v_id in completed_download_ids) or is_track_in_library(v_title)
                        tracks.append({
                            "id": v_id,
                            "title": v_title,
                            "uploader": e.get("uploader") or e.get("channel") or "",
                            "duration": dur_str,
                            "thumbnail": f"https://i.ytimg.com/vi/{v_id}/hqdefault.jpg",
                            "url": f"https://www.youtube.com/watch?v={v_id}",
                            "in_library": in_lib
                        })
                if tracks:
                    return {
                        "id": data.get("id") or pl_id or (vid or ""),
                        "title": title,
                        "total": len(tracks),
                        "tracks": tracks
                    }
            except Exception as err:
                print("Error parsing playlist inspection:", err)

    # 2. Fallback: If single video ID is found (e.g. youtu.be/<id>, watch?v=<id>, or seed from radio mix)
    if vid:
        try:
            oembed_url = f"https://www.youtube.com/oembed?url=https://www.youtube.com/watch?v={vid}&format=json"
            req = urllib.request.Request(oembed_url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
            with urllib.request.urlopen(req, timeout=5) as resp:
                o_data = json.loads(resp.read().decode('utf-8'))
                v_title = o_data.get("title") or "YouTube Track"
                uploader = o_data.get("author_name") or ""
                thumb = o_data.get("thumbnail_url") or f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg"
                in_lib = (vid in completed_download_ids) or is_track_in_library(v_title)
                return {
                    "id": vid,
                    "title": v_title,
                    "total": 1,
                    "tracks": [{
                        "id": vid,
                        "title": v_title,
                        "uploader": uploader,
                        "duration": "",
                        "thumbnail": thumb,
                        "url": f"https://www.youtube.com/watch?v={vid}",
                        "in_library": in_lib
                    }]
                }
        except Exception as err:
            print("oEmbed fallback inspection error:", err)

    # 3. Fallback: If the user entered a search query instead of a URL
    if not is_real_playlist and not is_radio and not vid and len(url) > 2 and not url.startswith("http"):
        try:
            search_items = search_youtube(url, limit=25)
            if search_items:
                tracks = []
                for s in search_items:
                    s_id = s.get("id")
                    s_title = s.get("title")
                    if s_id and s_title:
                        tracks.append({
                            "id": s_id,
                            "title": s_title,
                            "uploader": s.get("channel") or "",
                            "duration": s.get("duration") or "",
                            "thumbnail": s.get("thumbnail") or f"https://i.ytimg.com/vi/{s_id}/hqdefault.jpg",
                            "url": s.get("url") or f"https://www.youtube.com/watch?v={s_id}",
                            "in_library": (s_id in completed_download_ids) or is_track_in_library(s_title)
                        })
                if tracks:
                    return {
                        "id": "search_results",
                        "title": f'Search Results: "{url}"',
                        "total": len(tracks),
                        "tracks": tracks
                    }
        except Exception as err:
            print("Search fallback inspection error:", err)

    return None

HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no, viewport-fit=cover">
    <meta name="theme-color" content="#fbf8ff">
    <title>TubeSync - Navidrome Engine</title>
    
    <!-- Google Fonts: Plus Jakarta Sans & Material Symbols Outlined -->
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700;800&display=swap" rel="stylesheet">
    <link href="https://fonts.googleapis.com/css2?family=Material+Symbols+Outlined:opsz,wght,FILL,GRAD@20..48,100..700,0..1,-50..200" rel="stylesheet">
    
    <!-- Tailwind CSS -->
    <script src="https://cdn.tailwindcss.com"></script>
    <script>
        tailwind.config = {
            darkMode: 'class',
            theme: {
                extend: {
                    colors: {
                        "surface": "#fbf8ff",
                        "surface-bright": "#fbf8ff",
                        "surface-dim": "#dad9e3",
                        "surface-container-lowest": "#ffffff",
                        "surface-container-low": "#f4f2fd",
                        "surface-container": "#eeedf7",
                        "surface-container-high": "#e8e7f1",
                        "surface-container-highest": "#e3e1ec",
                        "on-surface": "#1a1b22",
                        "on-surface-variant": "#5d3f3f",
                        "outline": "#916e6e",
                        "outline-variant": "#e6bdbc",
                        "primary": "#ba002b",
                        "primary-container": "#e5183b",
                        "on-primary": "#ffffff",
                        "secondary": "#8234c6",
                        "secondary-container": "#b86dfd",
                        "on-secondary": "#ffffff",
                        "secondary-fixed": "#f1dbff",
                        "on-secondary-fixed": "#2d0050",
                        "tertiary": "#a5304c",
                        "tertiary-container": "#c54963",
                        "error": "#ba1a1a",
                        "error-container": "#ffdad6",
                        "on-error-container": "#93000a",
                        "inverse-surface": "#2f3038",
                        "inverse-on-surface": "#f1effa"
                    },
                    fontFamily: {
                        sans: ['"Plus Jakarta Sans"', 'sans-serif'],
                        mono: ['ui-monospace', 'SFMono-Regular', 'Menlo', 'Monaco', 'Consolas', 'monospace']
                    },
                    borderRadius: {
                        'DEFAULT': '1rem',
                        'lg': '1.5rem',
                        'xl': '2rem',
                        '2xl': '2.25rem',
                        'full': '9999px'
                    }
                }
            }
        };
    </script>
    
    <style>
        body {
            font-family: 'Plus Jakarta Sans', sans-serif;
            background-color: #fbf8ff;
            color: #1a1b22;
            -webkit-tap-highlight-color: transparent;
            overscroll-behavior-y: none;
        }
        .pb-safe { padding-bottom: max(env(safe-area-inset-bottom, 0px), 12px); }
        .pt-safe { padding-top: max(env(safe-area-inset-top, 0px), 8px); }
        .material-symbols-outlined {
            font-variation-settings: 'FILL' 0, 'wght' 400, 'GRAD' 0, 'opsz' 24;
            vertical-align: middle;
            line-height: 1;
        }
        .icon-fill {
            font-variation-settings: 'FILL' 1, 'wght' 500, 'GRAD' 0, 'opsz' 24;
        }
        ::-webkit-scrollbar { display: none; }
        .no-scrollbar { -ms-overflow-style: none; scrollbar-width: none; }
        
        @keyframes soundWave {
            0%, 100% { height: 4px; }
            50% { height: 16px; }
        }
        .animate-wave-1 { animation: soundWave 0.8s ease-in-out infinite; }
        .animate-wave-2 { animation: soundWave 0.8s ease-in-out 0.2s infinite; }
        .animate-wave-3 { animation: soundWave 0.8s ease-in-out 0.4s infinite; }
    </style>
</head>
<body class="bg-surface text-on-surface flex flex-col min-h-screen select-none antialiased">

    <!-- Toast Notification Banner -->
    <div id="toastBox" class="fixed top-20 inset-x-0 z-50 flex justify-center px-4 pointer-events-none transition-all duration-300 opacity-0 -translate-y-4"></div>

    <!-- Fixed Luminous Top Header -->
    <header class="fixed top-0 w-full z-40 bg-surface/85 backdrop-blur-xl border-b border-surface-container-high/80 pt-safe">
        <div class="h-16 px-4 sm:px-6 flex items-center justify-between">
            <div class="flex items-center gap-2.5">
                <div class="w-9 h-9 rounded-full bg-primary/10 flex items-center justify-center text-primary">
                    <span class="material-symbols-outlined text-[22px] icon-fill">graphic_eq</span>
                </div>
                <div class="flex flex-col">
                    <span class="text-base font-bold text-primary tracking-tight leading-none">TubeSync</span>
                    <div class="flex items-center gap-1.5 mt-0.5">
                        <span id="headerPulseDot" class="w-1.5 h-1.5 rounded-full {% if is_syncing %}bg-amber-500 animate-ping{% else %}bg-emerald-500 animate-pulse{% endif %}"></span>
                        <span id="headerStatusText" class="text-[10px] font-bold text-outline tracking-wider uppercase">{% if is_syncing %}Syncing Pipeline{% else %}Navidrome Online{% endif %}</span>
                    </div>
                </div>
            </div>

            <div class="flex items-center gap-2">
                <a href="http://{{ host }}:4533" target="_blank" rel="noreferrer"
                   class="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-full bg-surface-container text-on-surface hover:bg-surface-container-high active:scale-95 transition text-xs font-semibold shadow-sm">
                    <span class="material-symbols-outlined text-[16px] text-secondary">open_in_new</span>
                    <span class="hidden sm:inline">Navidrome</span>
                </a>
                <div class="w-8 h-8 rounded-full bg-surface-container flex items-center justify-center text-on-surface-variant">
                    <span class="material-symbols-outlined text-[18px]">cloud_done</span>
                </div>
            </div>
        </div>
    </header>

    <!-- Main Content Area -->
    <main class="flex-1 w-full max-w-2xl mx-auto px-4 sm:px-5 pt-20 pb-32">

        <!-- ================= TAB 1: SEARCH & DISCOVERY ================= -->
        <section id="tab-search" class="tab-content flex flex-col gap-4">
            <!-- Search Bar -->
            <div class="relative flex items-center w-full">
                <span class="material-symbols-outlined absolute left-4 text-primary text-[20px] pointer-events-none">search</span>
                <input type="text" id="searchInput" placeholder="Search songs, albums, or paste YouTube link..."
                    class="w-full h-12 pl-11 pr-11 bg-surface-container rounded-2xl text-sm font-medium text-on-surface placeholder:text-outline focus:outline-none focus:bg-surface-container-low focus:ring-2 focus:ring-primary/20 shadow-sm transition-all"
                    onkeydown="if(event.key === 'Enter') doSearch()">
                <button type="button" id="clearSearchBtn" onclick="clearSearchInput()"
                    class="absolute right-3.5 w-6 h-6 rounded-full bg-surface-container-highest flex items-center justify-center text-on-surface-variant hover:text-on-surface active:scale-90 transition opacity-0 pointer-events-none">
                    <span class="material-symbols-outlined text-[14px]">close</span>
                </button>
            </div>

            <!-- Category Filter Pills -->
            <div class="flex items-center gap-2 overflow-x-auto no-scrollbar pb-1">
                <button onclick="setSearchFilter('all')" id="filterBtn-all"
                    class="filter-chip px-4 py-1.5 rounded-full text-xs font-bold transition-all shadow-sm bg-primary text-on-primary shrink-0 active:scale-95 flex items-center gap-1.5">
                    <span class="material-symbols-outlined text-[15px] icon-fill">local_fire_department</span>
                    <span>All</span>
                </button>
                <button onclick="setSearchFilter('song')" id="filterBtn-song"
                    class="filter-chip px-4 py-1.5 rounded-full text-xs font-semibold transition-all bg-surface-container text-on-surface-variant hover:text-on-surface shrink-0 active:scale-95 flex items-center gap-1.5">
                    <span class="material-symbols-outlined text-[15px]">music_note</span>
                    <span>Songs</span>
                </button>
                <button onclick="setSearchFilter('playlist')" id="filterBtn-playlist"
                    class="filter-chip px-4 py-1.5 rounded-full text-xs font-semibold transition-all bg-surface-container text-on-surface-variant hover:text-on-surface shrink-0 active:scale-95 flex items-center gap-1.5">
                    <span class="material-symbols-outlined text-[15px]">queue_music</span>
                    <span>Playlists</span>
                </button>
                <button onclick="setSearchFilter('radio')" id="filterBtn-radio"
                    class="filter-chip px-4 py-1.5 rounded-full text-xs font-semibold transition-all bg-surface-container text-on-surface-variant hover:text-on-surface shrink-0 active:scale-95 flex items-center gap-1.5">
                    <span class="material-symbols-outlined text-[15px]">radio</span>
                    <span>Radios</span>
                </button>
            </div>

            <!-- Direct Pipeline Sync Banner -->
            <div class="relative w-full rounded-2xl overflow-hidden shadow-md bg-gradient-to-tr from-primary via-tertiary-container to-secondary-container p-5 text-on-primary">
                <div class="relative z-10 flex flex-col gap-2">
                    <div class="flex items-center justify-between">
                        <div class="flex items-center gap-1.5 px-2.5 py-1 rounded-full bg-surface-bright/20 backdrop-blur-md">
                            <span class="w-2 h-2 rounded-full bg-surface-bright animate-ping"></span>
                            <span class="text-[10px] font-bold uppercase tracking-wider text-surface-bright">Direct Pipeline Ready</span>
                        </div>
                        <span class="material-symbols-outlined text-surface-bright text-[22px]">cloud_sync</span>
                    </div>
                    <div class="mt-1">
                        <h2 class="text-xl font-bold tracking-tight text-on-primary">Direct Pipeline Sync</h2>
                        <p class="text-xs text-white/90 mt-1 leading-relaxed">
                            Search music or paste links to download YouTube audio directly into your high-res Navidrome server library with auto-synced lyrics.
                        </p>
                    </div>
                </div>
            </div>

            <!-- Search Results Section -->
            <div id="searchSection" class="flex flex-col gap-3">
                <div class="flex items-center justify-between px-1">
                    <h3 class="text-base font-bold text-on-surface tracking-tight" id="searchHeaderTitle">Explore Audio</h3>
                    <span class="text-xs text-outline font-medium" id="searchResultCountBadge"></span>
                </div>

                <div id="searchResultsList" class="grid grid-cols-1 sm:grid-cols-2 gap-3">
                    <div class="col-span-full p-10 text-center text-outline text-xs">
                        <span class="material-symbols-outlined text-primary text-3xl mb-2 block opacity-40">search</span>
                        Search any song, artist, album, or paste a YouTube link to sync.
                    </div>
                </div>

                <!-- Load More Button -->
                <div id="loadMoreContainer" class="hidden text-center pt-2">
                    <button onclick="renderBatch()" class="px-6 py-2.5 rounded-full bg-surface-container hover:bg-surface-container-high text-primary font-bold text-xs shadow-sm transition active:scale-95 inline-flex items-center gap-2">
                        <span class="material-symbols-outlined text-[18px]">expand_more</span>
                        <span id="loadMoreBtnText">Load More Results</span>
                    </button>
                </div>
            </div>
        </section>

        <!-- ================= TAB 2: SERVER MUSIC LIBRARY ================= -->
        <section id="tab-library" class="tab-content hidden flex flex-col gap-4">
            <!-- Header Section -->
            <div class="flex flex-col gap-1 pt-1">
                <div class="flex items-center justify-between">
                    <span class="text-[10px] font-bold uppercase tracking-widest text-primary">TubeSync Vault</span>
                    <div class="flex items-center gap-1.5 px-2.5 py-0.5 rounded-full bg-surface-container-high text-on-surface-variant text-[11px] font-semibold">
                        <span class="w-2 h-2 rounded-full bg-emerald-500"></span>
                        <span>Subsonic Live</span>
                    </div>
                </div>
                <h1 class="text-2xl font-bold text-on-surface tracking-tight">Library</h1>
                <p class="text-xs text-on-surface-variant flex items-center gap-1.5">
                    <span>Navidrome Vault</span>
                    <span class="w-1 h-1 rounded-full bg-outline-variant"></span>
                    <span class="font-semibold text-on-surface" id="libTotalTracksBadge">Scanning...</span>
                </p>
            </div>

            <!-- Storage & Index Card -->
            <div class="relative overflow-hidden rounded-2xl bg-surface-container-lowest p-4 shadow-sm border border-surface-container-high/60">
                <div class="flex flex-col gap-2 relative z-10">
                    <div class="flex items-center justify-between">
                        <div class="flex items-center gap-2">
                            <div class="w-7 h-7 rounded-full bg-secondary-fixed flex items-center justify-center text-secondary">
                                <span class="material-symbols-outlined text-[16px]">pie_chart</span>
                            </div>
                            <span class="text-xs font-semibold text-on-surface">Music Library Storage</span>
                        </div>
                        <span class="text-[11px] text-on-surface-variant" id="libSubfolderInfo">/home/ubuntu/music/library</span>
                    </div>

                    <div class="w-full h-2 rounded-full bg-surface-container overflow-hidden p-[1px] mt-1">
                        <div class="h-full rounded-full bg-gradient-to-r from-secondary to-primary w-full transition-all duration-700"></div>
                    </div>

                    <div class="flex items-center justify-between pt-1 text-[11px] text-on-surface-variant">
                        <button onclick="triggerRescan()" id="rescanLibraryBtn" class="text-primary hover:text-primary-container font-semibold flex items-center gap-1 active:scale-95 transition" title="Trigger Navidrome library scan">
                            <span class="material-symbols-outlined text-[15px]" id="rescanIcon">autorenew</span>
                            <span>Rescan Navidrome</span>
                        </button>
                        <button onclick="fetchLibrary()" class="text-outline hover:text-on-surface font-semibold flex items-center gap-1 active:scale-95 transition" title="Refresh local cache">
                            <span class="material-symbols-outlined text-[14px]">refresh</span>
                            <span>Refresh</span>
                        </button>
                    </div>
                </div>
            </div>

            <!-- Search & Filter Row -->
            <div class="flex items-center gap-2">
                <div class="relative flex-1">
                    <span class="material-symbols-outlined absolute left-3.5 top-1/2 -translate-y-1/2 text-on-surface-variant text-[18px]">search</span>
                    <input type="text" id="libSearchInput" placeholder="Filter library tracks..."
                        oninput="filterLibraryList(this.value)"
                        class="w-full h-11 pl-10 pr-4 rounded-full bg-surface-container-lowest text-on-surface placeholder:text-outline text-xs font-medium shadow-sm border border-surface-container-high/60 focus:outline-none focus:ring-2 focus:ring-primary/20">
                </div>
                <button onclick="toggleSelectAllMode()" id="selectionModeBtn"
                    class="h-11 px-4 rounded-full bg-surface-container-lowest text-on-surface border border-surface-container-high/60 flex items-center gap-1.5 shadow-sm text-xs font-semibold active:scale-95 transition">
                    <span class="material-symbols-outlined text-[18px] text-primary">checklist</span>
                    <span id="selectionModeText">Select</span>
                </button>
            </div>

            <!-- Batch Action Bar (Appears when selection mode is active) -->
            <div id="batchActionBar" class="hidden p-3 rounded-2xl bg-surface-container-low border border-primary/20 flex items-center justify-between gap-3 shadow-sm">
                <label class="flex items-center gap-2 text-xs font-semibold text-on-surface cursor-pointer select-none">
                    <input type="checkbox" id="selectAllCheckbox" onchange="toggleSelectAll(this.checked)"
                        class="rounded border-outline/40 text-primary focus:ring-0 w-4 h-4">
                    <span>Select All</span>
                </label>
                <div class="flex items-center gap-2">
                    <span id="selectedCountText" class="text-xs font-bold text-primary">0 selected</span>
                    <button onclick="deleteSelectedBatch()" id="batchDeleteBtn" disabled
                        class="px-3 py-1.5 rounded-full bg-error text-on-error font-bold text-xs disabled:opacity-40 disabled:pointer-events-none active:scale-95 transition shadow-sm flex items-center gap-1">
                        <span class="material-symbols-outlined text-[15px]">delete</span>
                        <span>Delete</span>
                    </button>
                </div>
            </div>

            <!-- Library Track Stream List -->
            <div id="libraryTracksList" class="flex flex-col gap-2">
                <div class="p-10 text-center text-outline text-xs">
                    <span class="material-symbols-outlined text-primary text-3xl mb-2 block opacity-40">library_music</span>
                    Loading library tracks...
                </div>
            </div>
        </section>

        <!-- ================= TAB 3: SOURCES & PLAYLISTS ================= -->
        <section id="tab-sources" class="tab-content hidden flex flex-col gap-4">
            <!-- Engine Status Pill -->
            <div class="flex items-center justify-between px-1 pt-1">
                <div class="inline-flex items-center gap-2 px-3 py-1.5 rounded-full bg-surface-container-low shadow-sm">
                    <span class="relative flex h-2 w-2">
                        <span class="animate-ping absolute inline-flex h-full w-full rounded-full bg-emerald-400 opacity-75"></span>
                        <span class="relative inline-flex rounded-full h-2 w-2 bg-emerald-500"></span>
                    </span>
                    <span class="text-xs text-on-surface-variant font-semibold tracking-wide">yt-dlp Engine · High-Res Audio</span>
                </div>
                <div class="flex items-center gap-1 px-2.5 py-1 rounded-full bg-surface-container text-xs text-on-surface-variant font-medium">
                    <span class="material-symbols-outlined text-[15px] text-primary icon-fill">speed</span>
                    <span>Ready</span>
                </div>
            </div>

            <!-- Add Pipeline Source Card -->
            <div class="bg-surface-container-lowest rounded-2xl p-4 shadow-sm border border-surface-container-high/60 flex flex-col gap-3">
                <div class="flex items-center justify-between">
                    <div class="flex items-center gap-2">
                        <div class="w-8 h-8 rounded-full bg-primary/10 flex items-center justify-center text-primary">
                            <span class="material-symbols-outlined text-[18px]">add_link</span>
                        </div>
                        <h2 class="text-sm font-bold text-on-surface tracking-tight">Add Pipeline Source</h2>
                    </div>
                    <span class="text-[10px] font-bold text-on-surface-variant uppercase tracking-wider">Fast Ingest</span>
                </div>

                <div class="relative flex items-center w-full">
                    <span class="material-symbols-outlined absolute left-3.5 text-outline text-[20px] pointer-events-none">smart_display</span>
                    <input type="text" id="directUrlInput" placeholder="Paste YouTube playlist, mix or video URL..."
                        class="w-full pl-10 pr-20 py-3 rounded-full bg-surface-container-low text-on-surface placeholder:text-outline text-xs font-medium focus:outline-none focus:ring-2 focus:ring-primary/20 shadow-inner">
                    <button type="button" onclick="pasteFromClipboard()"
                        class="absolute right-1.5 px-3 py-1.5 rounded-full bg-surface-container text-primary active:scale-95 transition text-xs font-bold flex items-center gap-1 shadow-sm">
                        <span class="material-symbols-outlined text-[15px]">content_paste</span>
                        <span>Paste</span>
                    </button>
                </div>

                <div class="flex items-center gap-2.5 pt-1">
                    <button type="button" onclick="submitDirectLink(event)" id="directAddBtn"
                        class="flex-1 py-2.5 px-4 rounded-full bg-primary text-on-primary text-xs font-bold flex items-center justify-center gap-1.5 shadow-[0_6px_20px_rgba(186,0,43,0.25)] active:scale-95 transition">
                        <span class="material-symbols-outlined text-[18px]">queue_music</span>
                        <span>+ Queue Directly</span>
                    </button>
                    <button type="button" onclick="inspectDirectUrl()"
                        class="px-5 py-2.5 rounded-full bg-surface-container text-on-surface text-xs font-bold flex items-center justify-center gap-1.5 active:scale-95 transition hover:bg-surface-container-high">
                        <span class="material-symbols-outlined text-[18px] text-primary">preview</span>
                        <span>Inspect Tracks</span>
                    </button>
                </div>
            </div>

            <!-- Tracked Playlists & Sources -->
            <div class="flex flex-col gap-3">
                <div class="flex items-center justify-between px-1">
                    <div>
                        <h3 class="text-base font-bold text-on-surface tracking-tight">Active Pipelines</h3>
                        <p class="text-xs text-on-surface-variant">Automated sources synced to Navidrome</p>
                    </div>
                </div>

                <div id="sourcesPlaylistsList" class="flex flex-col gap-2.5">
                    {% if playlists %}
                        {% for pl in playlists %}
                        <div class="bg-surface-container-lowest rounded-2xl p-3.5 shadow-sm border border-surface-container-high/60 flex items-center justify-between gap-3">
                            <div class="w-10 h-10 rounded-xl bg-surface-container flex items-center justify-center text-primary shrink-0">
                                <span class="material-symbols-outlined text-[20px]">queue_music</span>
                            </div>
                            <div class="min-w-0 flex-1">
                                <h4 class="text-xs font-bold text-on-surface truncate">{{ pl }}</h4>
                                <span class="text-[10px] text-outline font-medium">Tracked Source</span>
                            </div>
                            <div class="flex items-center gap-1.5 shrink-0">
                                <button onclick="openPlaylistModal('{{ pl }}', 'Tracked Playlist')" class="p-2 rounded-full hover:bg-surface-container text-primary transition" title="Inspect Tracks">
                                    <span class="material-symbols-outlined text-[18px]">visibility</span>
                                </button>
                                <button onclick="deletePlaylist('{{ pl }}')" class="p-2 rounded-full hover:bg-surface-container text-outline hover:text-error transition" title="Remove Link">
                                    <span class="material-symbols-outlined text-[18px]">delete</span>
                                </button>
                            </div>
                        </div>
                        {% endfor %}
                    {% else %}
                        <div class="p-10 text-center text-outline text-xs bg-surface-container-lowest rounded-2xl border border-surface-container-high/60">
                            <span class="material-symbols-outlined text-outline text-3xl mb-2 block opacity-40">podcasts</span>
                            No tracked sources yet. Paste a YouTube playlist above to add!
                        </div>
                    {% endif %}
                </div>
            </div>
        </section>

        <!-- ================= TAB 4: YOUTUBE AUTH & SECURITY ================= -->
        <section id="tab-cookies" class="tab-content hidden flex flex-col gap-4">
            <!-- Hero Apple-Style Security Card -->
            <div class="relative overflow-hidden rounded-2xl bg-surface-container-lowest p-5 shadow-sm border border-surface-container-high/60">
                <div class="relative z-10 flex flex-col gap-3">
                    <div class="flex items-start justify-between">
                        <div class="w-12 h-12 rounded-2xl bg-gradient-to-tr from-primary to-primary-container flex items-center justify-center text-on-primary shadow-md">
                            <span class="material-symbols-outlined text-[26px]">vpn_key</span>
                        </div>
                        {% if cookies_ok %}
                        <div class="flex items-center gap-1.5 px-3 py-1 rounded-full bg-emerald-50 text-emerald-700 shadow-sm">
                            <span class="w-2 h-2 rounded-full bg-emerald-500 animate-pulse"></span>
                            <span class="text-xs font-bold">Active · Authenticated</span>
                        </div>
                        {% else %}
                        <div class="flex items-center gap-1.5 px-3 py-1 rounded-full bg-amber-50 text-amber-700 border border-amber-200 shadow-sm">
                            <span class="w-2 h-2 rounded-full bg-amber-500 animate-ping"></span>
                            <span class="text-xs font-bold">Action Required</span>
                        </div>
                        {% endif %}
                    </div>
                    <div>
                        <h2 class="text-xl font-bold tracking-tight text-on-surface">YouTube Authentication</h2>
                        <p class="text-xs text-on-surface-variant mt-0.5">Cloud Node Session Bypass & Token Vault</p>
                    </div>

                    <!-- Telemetry Stats -->
                    <div class="grid grid-cols-3 gap-2 pt-1 text-center">
                        <div class="flex flex-col p-2.5 rounded-xl bg-surface-container-low">
                            <span class="text-[10px] text-outline uppercase font-semibold">Status</span>
                            <span class="text-xs text-on-surface font-bold mt-0.5">{% if cookies_ok %}Ready{% else %}Missing{% endif %}</span>
                        </div>
                        <div class="flex flex-col p-2.5 rounded-xl bg-surface-container-low">
                            <span class="text-[10px] text-outline uppercase font-semibold">Format</span>
                            <span class="text-xs text-on-surface font-bold mt-0.5">Netscape</span>
                        </div>
                        <div class="flex flex-col p-2.5 rounded-xl bg-surface-container-low">
                            <span class="text-[10px] text-outline uppercase font-semibold">Bypass</span>
                            <span class="text-xs text-primary font-bold mt-0.5">Active</span>
                        </div>
                    </div>
                </div>
            </div>

            <!-- Educational Callout -->
            <div class="relative overflow-hidden rounded-2xl bg-error-container/40 p-4 shadow-sm border border-error/20">
                <div class="flex items-start gap-3">
                    <div class="w-8 h-8 rounded-full bg-error-container flex items-center justify-center shrink-0 text-on-error-container mt-0.5">
                        <span class="material-symbols-outlined text-[20px]">info</span>
                    </div>
                    <div class="flex flex-col text-on-surface">
                        <span class="text-xs font-bold text-on-surface">Why cookies are needed:</span>
                        <p class="text-[11px] text-on-surface-variant mt-1 leading-relaxed">
                            YouTube blocks cloud datacenter IPs with <b class="text-on-surface">"Sign in to confirm you're not a bot"</b>. Pasting cookies from your browser bypasses bot detection and unlocks high-speed audio streams.
                        </p>
                    </div>
                </div>
            </div>

            <!-- Verification Checklist -->
            <div class="flex flex-col rounded-2xl bg-surface-container-lowest p-4 shadow-sm border border-surface-container-high/60 gap-2">
                <div class="flex items-center justify-between pb-1">
                    <span class="text-[10px] uppercase tracking-wider text-outline font-bold">Node Security Checklist</span>
                    <span class="text-xs font-bold {% if cookies_ok %}text-emerald-600{% else %}text-amber-600{% endif %}">{% if cookies_ok %}✓ Verified{% else %}Needs Update{% endif %}</span>
                </div>
                <div class="space-y-1.5">
                    <div class="flex items-center gap-2.5 p-2 rounded-xl bg-surface-container-low text-xs">
                        <span class="material-symbols-outlined text-emerald-600 text-[18px] icon-fill">check_circle</span>
                        <span class="text-on-surface font-medium">Logged in on <b class="underline">https://www.youtube.com</b></span>
                    </div>
                    <div class="flex items-center gap-2.5 p-2 rounded-xl bg-surface-container-low text-xs">
                        <span class="material-symbols-outlined {% if cookies_ok %}text-emerald-600 icon-fill{% else %}text-amber-500{% endif %} text-[18px]">{% if cookies_ok %}check_circle{% else %}radio_button_unchecked{% endif %}</span>
                        <span class="text-on-surface font-medium">Payload contains <code>LOGIN_INFO</code> & <code>SAPISID</code></span>
                    </div>
                    <div class="flex items-center gap-2.5 p-2 rounded-xl bg-surface-container-low text-xs">
                        <span class="material-symbols-outlined text-emerald-600 text-[18px] icon-fill">check_circle</span>
                        <span class="text-on-surface font-medium">yt-dlp stream pipeline linked</span>
                    </div>
                </div>
            </div>

            <!-- Cookies Input Form -->
            <div class="bg-surface-container-lowest rounded-2xl p-4 shadow-sm border border-surface-container-high/60">
                <form action="/cookies" method="POST" enctype="multipart/form-data" class="space-y-3">
                    <div>
                        <label class="text-xs font-bold text-on-surface block mb-1.5">Netscape Cookies Buffer</label>
                        <textarea name="cookies_text" rows="4" placeholder="Paste your Netscape format cookies text here..."
                            class="w-full px-3 py-2 bg-surface-container-low border border-surface-container-high rounded-xl text-xs font-mono focus:outline-none focus:ring-2 focus:ring-primary/20 text-on-surface placeholder:text-outline"></textarea>
                    </div>
                    <div class="flex flex-col sm:flex-row sm:items-center justify-between gap-3">
                        <div class="text-xs text-on-surface-variant flex items-center gap-1.5">
                            <span>Or upload:</span>
                            <input type="file" name="cookies_file" accept=".txt" class="text-xs file:mr-2 file:py-1 file:px-3 file:rounded-full file:border-0 file:text-xs file:bg-surface-container-high file:text-on-surface hover:file:bg-surface-container-highest cursor-pointer">
                        </div>
                        <button type="submit" class="px-5 py-2 bg-primary hover:bg-primary-container text-on-primary font-bold text-xs rounded-full shadow-md active:scale-95 transition">
                            Save Cookies
                        </button>
                    </div>
                </form>
            </div>
        </section>

        <!-- ================= TAB 5: LIVE SYNC CONSOLE ================= -->
        <section id="tab-logs" class="tab-content hidden flex flex-col gap-4">
            <!-- Telemetry Header -->
            <div class="flex items-center justify-between px-1 pt-1">
                <div class="flex items-center gap-1.5">
                    <span class="text-[10px] text-outline uppercase tracking-wider font-semibold">Telemetry & Ops</span>
                    <span class="text-outline/40">•</span>
                    <span class="text-[10px] text-primary font-bold uppercase">Subsonic Engine</span>
                </div>
                <div class="flex items-center gap-1.5 px-2.5 py-1 rounded-full bg-surface-container-high shadow-sm">
                    <span class="w-2 h-2 rounded-full {% if is_syncing %}bg-amber-500 animate-ping{% else %}bg-emerald-500 animate-pulse{% endif %}"></span>
                    <span class="text-xs text-on-surface font-semibold">{% if is_syncing %}Active Ingest{% else %}Idle Pipeline{% endif %}</span>
                </div>
            </div>

            <!-- Daemon Master Status Card -->
            <div class="relative overflow-hidden rounded-2xl bg-surface-container-lowest shadow-sm p-4 border border-surface-container-high/60">
                <div class="flex items-center justify-between">
                    <div class="flex items-center gap-2.5">
                        <div class="w-10 h-10 rounded-xl bg-surface-container flex items-center justify-center text-primary">
                            <span class="material-symbols-outlined text-[22px]">terminal</span>
                        </div>
                        <div class="flex flex-col">
                            <span class="text-sm font-bold text-on-surface">Sync Daemon Core</span>
                            <span class="text-[11px] text-on-surface-variant font-mono">yt-dlp v2026.10.21</span>
                        </div>
                    </div>
                    <button onclick="stopDownload()" id="logsStopBtn"
                        class="hidden px-3 py-1 bg-error text-on-error rounded-full text-xs font-bold active:scale-95 transition shadow-sm flex items-center gap-1 animate-pulse">
                        <span class="material-symbols-outlined text-[14px]">stop</span>
                        <span>Cancel Sync</span>
                    </button>
                </div>
            </div>

            <!-- Terminal Window -->
            <div class="rounded-2xl bg-[#0f111a] p-4 shadow-xl border border-slate-800">
                <div class="flex items-center justify-between pb-2 mb-2 border-b border-slate-800 text-xs">
                    <div class="flex items-center gap-2 text-slate-400 font-mono">
                        <span class="w-2.5 h-2.5 rounded-full bg-rose-500 inline-block"></span>
                        <span class="w-2.5 h-2.5 rounded-full bg-amber-500 inline-block"></span>
                        <span class="w-2.5 h-2.5 rounded-full bg-emerald-500 inline-block"></span>
                        <span class="ml-2 text-slate-300">sync.log</span>
                    </div>
                    <div class="flex items-center gap-3">
                        <label class="flex items-center gap-1.5 text-xs text-slate-400 cursor-pointer select-none">
                            <input type="checkbox" id="autoRefreshLogs" checked class="rounded border-slate-700 text-primary focus:ring-0">
                            <span>Auto</span>
                        </label>
                        <button onclick="fetchLogs()" class="text-slate-400 hover:text-slate-200 transition">
                            <span class="material-symbols-outlined text-[16px]">refresh</span>
                        </button>
                    </div>
                </div>
                <pre id="logConsole" class="text-xs font-mono text-emerald-400 overflow-x-auto max-h-80 whitespace-pre-wrap leading-relaxed">{{ logs }}</pre>
            </div>
        </section>

    </main>

    <!-- Floating Miniplayer / Download Dock -->
    <div id="floatingDock" class="fixed inset-x-0 bottom-[4.75rem] sm:bottom-20 z-40 px-4 max-w-xl mx-auto pointer-events-none transition-all duration-300 transform translate-y-32 opacity-0">
        <div id="floatingDockBar" class="glass-panel p-3 rounded-2xl shadow-2xl border border-surface-container-high bg-surface-container-lowest/95 backdrop-blur-xl flex items-center justify-between gap-3 pointer-events-auto">
            <!-- Left: Wave Bars Equalizer -->
            <div id="dockEqWrap" class="w-9 h-9 rounded-xl bg-primary/10 flex items-center justify-center text-primary shrink-0">
                <div class="flex items-end gap-0.5 h-4">
                    <span class="w-1 bg-primary rounded-full animate-wave-1"></span>
                    <span class="w-1 bg-primary rounded-full animate-wave-2"></span>
                    <span class="w-1 bg-primary rounded-full animate-wave-3"></span>
                </div>
            </div>

            <!-- Center: Title & Progress Bar -->
            <div class="min-w-0 flex-1">
                <div class="flex items-center justify-between gap-2">
                    <p id="dockTitle" class="text-xs font-bold text-on-surface truncate">Downloading...</p>
                    <span id="dockSpeed" class="text-[10px] font-mono font-bold text-primary shrink-0">0%</span>
                </div>
                <div class="w-full bg-surface-container rounded-full h-1.5 mt-1.5 overflow-hidden">
                    <div id="dockProgressBar" class="bg-gradient-to-r from-primary to-secondary h-full rounded-full transition-all duration-300" style="width: 5%;"></div>
                </div>
            </div>

            <!-- Right: Action Buttons -->
            <div id="dockActionWrap" class="flex items-center gap-1 shrink-0">
                <button onclick="stopDownload()" class="w-7 h-7 rounded-full bg-surface-container hover:bg-error/10 hover:text-error text-on-surface-variant flex items-center justify-center transition" title="Cancel">
                    <span class="material-symbols-outlined text-[16px]">close</span>
                </button>
            </div>
        </div>
    </div>

    <!-- Fixed Luminous Bottom Navigation Bar -->
    <nav class="fixed bottom-0 w-full z-40 pb-safe bg-surface/85 backdrop-blur-xl border-t border-surface-container-high/80 shadow-[0_-1px_12px_rgba(0,0,0,0.04)]">
        <div class="h-16 px-4 flex items-center justify-around">
            <button onclick="switchTab('search')" id="tabBtn-search" class="bottom-nav-btn flex flex-col items-center justify-center gap-0.5 min-w-[56px] min-h-[44px] transition text-primary font-bold">
                <span class="material-symbols-outlined text-[24px]">search</span>
                <span class="text-[11px]">Search</span>
                <span class="tab-indicator w-1 h-1 bg-primary rounded-full mt-0.5"></span>
            </button>

            <button onclick="switchTab('library')" id="tabBtn-library" class="bottom-nav-btn flex flex-col items-center justify-center gap-0.5 min-w-[56px] min-h-[44px] transition text-on-surface-variant hover:text-on-surface">
                <span class="material-symbols-outlined text-[24px]">library_music</span>
                <span class="text-[11px]">Library</span>
                <span class="tab-indicator w-1 h-1 bg-primary rounded-full mt-0.5 hidden"></span>
            </button>

            <button onclick="switchTab('sources')" id="tabBtn-sources" class="bottom-nav-btn flex flex-col items-center justify-center gap-0.5 min-w-[56px] min-h-[44px] transition text-on-surface-variant hover:text-on-surface">
                <span class="material-symbols-outlined text-[24px]">podcasts</span>
                <span class="text-[11px]">Sources</span>
                <span class="tab-indicator w-1 h-1 bg-primary rounded-full mt-0.5 hidden"></span>
            </button>

            <button onclick="switchTab('cookies')" id="tabBtn-cookies" class="bottom-nav-btn flex flex-col items-center justify-center gap-0.5 min-w-[56px] min-h-[44px] transition text-on-surface-variant hover:text-on-surface relative">
                <span class="material-symbols-outlined text-[24px]">vpn_key</span>
                <span class="text-[11px]">Auth</span>
                <span class="tab-indicator w-1 h-1 bg-primary rounded-full mt-0.5 hidden"></span>
                {% if not cookies_ok %}
                <span class="w-2 h-2 rounded-full bg-amber-500 absolute top-1 right-3 animate-ping"></span>
                <span class="w-2 h-2 rounded-full bg-amber-500 absolute top-1 right-3"></span>
                {% endif %}
            </button>

            <button onclick="switchTab('logs')" id="tabBtn-logs" class="bottom-nav-btn flex flex-col items-center justify-center gap-0.5 min-w-[56px] min-h-[44px] transition text-on-surface-variant hover:text-on-surface">
                <span class="material-symbols-outlined text-[24px]">terminal</span>
                <span class="text-[11px]">Console</span>
                <span class="tab-indicator w-1 h-1 bg-primary rounded-full mt-0.5 hidden"></span>
            </button>
        </div>
    </nav>

    <!-- Playlist Track Selector Modal / Bottom Sheet -->
    <div id="playlistModal" class="fixed inset-0 z-50 bg-black/60 backdrop-blur-md flex items-end sm:items-center justify-center p-0 sm:p-4 hidden transition-all duration-300">
        <div class="bg-surface-container-lowest w-full max-w-xl rounded-t-3xl sm:rounded-3xl border border-surface-container-high shadow-2xl flex flex-col max-h-[85vh] overflow-hidden">
            <!-- Modal Header -->
            <div class="p-4 border-b border-surface-container-high flex items-center justify-between">
                <div class="min-w-0 flex-1 pr-3">
                    <div class="flex items-center gap-2">
                        <span id="plModalTypeBadge" class="px-2 py-0.5 rounded-full text-[10px] font-bold bg-primary/10 text-primary">Playlist</span>
                        <span id="plModalTotalBadge" class="text-xs text-outline font-medium">Loading tracks...</span>
                    </div>
                    <h3 id="plModalTitle" class="text-sm font-bold text-on-surface truncate mt-1">Playlist Tracks</h3>
                </div>
                <button onclick="closePlaylistModal()" class="w-8 h-8 rounded-full bg-surface-container hover:bg-surface-container-high text-on-surface flex items-center justify-center transition shrink-0">
                    <span class="material-symbols-outlined text-[18px]">close</span>
                </button>
            </div>

            <!-- Modal Selection & Search Toolbar -->
            <div class="px-4 py-2 bg-surface-container-low border-b border-surface-container-high flex items-center justify-between text-xs">
                <label class="flex items-center gap-2 text-on-surface font-semibold cursor-pointer select-none">
                    <input type="checkbox" id="plSelectAllCheck" onchange="toggleSelectAllPlaylist(this.checked)" checked class="rounded border-outline/40 text-primary focus:ring-0">
                    <span id="plSelectionSummary">Select All</span>
                </label>
                <span id="plSelectedCountText" class="text-primary font-bold">0 selected</span>
            </div>

            <!-- Modal Scrollable Track List -->
            <div id="plModalTracksList" class="p-3 space-y-2 overflow-y-auto flex-1 min-h-[220px]">
                <div class="p-8 text-center text-outline text-xs">
                    <span class="material-symbols-outlined text-primary text-2xl animate-spin mb-2 block">autorenew</span>
                    Scanning playlist tracks...
                </div>
            </div>

            <!-- Modal Bottom Action Bar -->
            <div class="p-3 bg-surface-container-lowest border-t border-surface-container-high flex items-center justify-between gap-3">
                <button onclick="closePlaylistModal()" class="px-4 py-2 rounded-full bg-surface-container hover:bg-surface-container-high text-on-surface font-semibold text-xs active:scale-95 transition">
                    Cancel
                </button>
                <button id="plDownloadBtn" onclick="downloadSelectedPlaylistTracks()"
                    class="flex-1 py-2.5 px-4 rounded-full bg-primary hover:bg-primary-container text-on-primary font-bold text-xs shadow-md active:scale-95 transition flex items-center justify-center gap-1.5 disabled:opacity-40 disabled:pointer-events-none">
                    <span class="material-symbols-outlined text-[16px]">download</span>
                    <span id="plDownloadBtnText">Download Selected Tracks</span>
                </button>
            </div>
        </div>
    </div>

    <!-- Application JavaScript Logic -->
    <script>
        // State
        let allSearchResults = [];
        let displayedCount = 0;
        let currentFilter = 'all';
        const BATCH_SIZE = 8;
        
        let allLibrarySongs = [];
        let isSelectionMode = false;
        let activeDownloadingId = null;
        let downloadedIds = new Set();
        let lastNotifiedId = null;
        let lastNotifiedStatus = null;
        let currentInspectedPlaylist = null;

        function escapeHtml(text) {
            return (text || '').replace(/'/g, "\\'").replace(/"/g, '&quot;');
        }

        // Tab Switching
        function switchTab(tabName) {
            document.querySelectorAll('.tab-content').forEach(el => el.classList.add('hidden'));
            document.querySelectorAll('.bottom-nav-btn').forEach(btn => {
                btn.classList.remove('text-primary', 'font-bold');
                btn.classList.add('text-on-surface-variant');
                const ind = btn.querySelector('.tab-indicator');
                if (ind) ind.classList.add('hidden');
            });

            const activeTab = document.getElementById(`tab-${tabName}`);
            const activeBtn = document.getElementById(`tabBtn-${tabName}`);
            if (activeTab && activeBtn) {
                activeTab.classList.remove('hidden');
                activeBtn.classList.add('text-primary', 'font-bold');
                activeBtn.classList.remove('text-on-surface-variant');
                const ind = activeBtn.querySelector('.tab-indicator');
                if (ind) ind.classList.remove('hidden');
            }

            if (tabName === 'logs') fetchLogs();
            if (tabName === 'library') fetchLibrary();
        }

        // Toast Messages
        function showToast(message, isError = false) {
            const toastBox = document.getElementById('toastBox');
            toastBox.innerHTML = `
                <div class="px-4 py-2.5 rounded-full shadow-xl flex items-center gap-2 border ${isError ? 'bg-error text-on-error border-error-container' : 'bg-surface-container-lowest text-on-surface border-surface-container-high'} pointer-events-auto">
                    <span class="material-symbols-outlined text-[18px] ${isError ? 'text-white' : 'text-emerald-500'}">${isError ? 'error' : 'check_circle'}</span>
                    <span class="text-xs font-semibold">${message}</span>
                </div>
            `;
            toastBox.classList.remove('opacity-0', '-translate-y-4');
            toastBox.classList.add('opacity-100', 'translate-y-0');
            setTimeout(() => {
                toastBox.classList.add('opacity-0', '-translate-y-4');
                toastBox.classList.remove('opacity-100', 'translate-y-0');
            }, 3500);
        }

        // Search Handlers
        function clearSearchInput() {
            const input = document.getElementById('searchInput');
            input.value = '';
            document.getElementById('clearSearchBtn').classList.add('opacity-0', 'pointer-events-none');
            input.focus();
        }

        document.getElementById('searchInput').addEventListener('input', (e) => {
            const clearBtn = document.getElementById('clearSearchBtn');
            if (e.target.value.length > 0) {
                clearBtn.classList.remove('opacity-0', 'pointer-events-none');
            } else {
                clearBtn.classList.add('opacity-0', 'pointer-events-none');
            }
        });

        async function doSearch() {
            const query = document.getElementById('searchInput').value.trim();
            if (!query) return;

            const list = document.getElementById('searchResultsList');
            const loadMore = document.getElementById('loadMoreContainer');
            const countBadge = document.getElementById('searchResultCountBadge');

            list.innerHTML = `
                <div class="col-span-full p-12 text-center text-outline text-xs">
                    <span class="material-symbols-outlined text-primary text-3xl animate-spin mb-2 block">autorenew</span>
                    Searching YouTube pipeline...
                </div>
            `;
            loadMore.classList.add('hidden');
            countBadge.innerText = '';

            try {
                const res = await fetch(`/api/search?q=${encodeURIComponent(query)}`);
                const data = await res.json();
                allSearchResults = data.results || [];
                displayedCount = 0;
                list.innerHTML = '';

                if (allSearchResults.length === 0) {
                    list.innerHTML = `
                        <div class="col-span-full p-10 text-center text-outline text-xs">
                            No tracks found for "${escapeHtml(query)}". Try another query or paste a direct link.
                        </div>
                    `;
                    return;
                }

                countBadge.innerText = `${allSearchResults.length} found`;
                renderBatch();
            } catch (err) {
                list.innerHTML = `
                    <div class="col-span-full p-10 text-center text-error text-xs">
                        Search failed: ${escapeHtml(err.message)}
                    </div>
                `;
            }
        }

        function setSearchFilter(cat) {
            currentFilter = cat;
            document.querySelectorAll('.filter-chip').forEach(btn => {
                btn.className = "filter-chip px-4 py-1.5 rounded-full text-xs font-semibold transition-all bg-surface-container text-on-surface-variant hover:text-on-surface shrink-0 active:scale-95 flex items-center gap-1.5";
            });
            const activeChip = document.getElementById(`filterBtn-${cat}`);
            if (activeChip) {
                activeChip.className = "filter-chip px-4 py-1.5 rounded-full text-xs font-bold transition-all shadow-sm bg-primary text-on-primary shrink-0 active:scale-95 flex items-center gap-1.5";
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

        function renderBatch() {
            const list = document.getElementById('searchResultsList');
            const loadMore = document.getElementById('loadMoreContainer');
            const filtered = getFilteredResults();
            const nextBatch = filtered.slice(displayedCount, displayedCount + BATCH_SIZE);
            displayedCount += nextBatch.length;

            const html = nextBatch.map(item => {
                const isCompleted = downloadedIds.has(item.id) || item.in_library;
                const isDownloadingThis = activeDownloadingId && (activeDownloadingId === item.id || activeDownloadingId === item.url);
                const isPlaylist = item.category === 'playlist' || item.type === 'playlist' || item.category === 'radio' || item.type === 'radio';

                let actionHtml = '';
                if (isCompleted) {
                    actionHtml = `
                        <span class="px-2.5 py-1 rounded-full bg-emerald-50 text-emerald-700 text-[10px] font-bold border border-emerald-200 flex items-center gap-1">
                            <span class="material-symbols-outlined text-[13px] icon-fill">check_circle</span>
                            <span>Saved</span>
                        </span>
                    `;
                } else if (isDownloadingThis) {
                    actionHtml = renderInCardProgress(item);
                } else if (isPlaylist) {
                    actionHtml = `
                        <button onclick="openPlaylistModal('${item.url}', '${escapeHtml(item.title)}')"
                            class="px-3 py-1.5 rounded-full bg-surface-container hover:bg-surface-container-high text-primary font-bold text-xs flex items-center gap-1 active:scale-95 transition shadow-sm">
                            <span class="material-symbols-outlined text-[15px]">queue_music</span>
                            <span>Tracks</span>
                        </button>
                    `;
                } else {
                    actionHtml = `
                        <button onclick="downloadTrack('${item.url}', '${item.id}', '${escapeHtml(item.title)}', this)"
                            class="w-9 h-9 rounded-full bg-primary hover:bg-primary-container text-on-primary flex items-center justify-center shadow-md active:scale-95 transition" title="Download Audio">
                            <span class="material-symbols-outlined text-[20px]">add</span>
                        </button>
                    `;
                }

                return `
                <div class="bg-surface-container-lowest rounded-2xl p-3 shadow-sm border border-surface-container-high/60 flex items-center justify-between gap-3 hover:border-surface-container-highest transition" id="search-card-${item.id}">
                    <div class="relative w-14 h-14 rounded-xl overflow-hidden shrink-0 bg-surface-container shadow-sm">
                        <img src="${item.thumbnail}" alt="" class="w-full h-full object-cover" loading="lazy">
                        ${item.duration ? `<span class="absolute bottom-1 right-1 bg-black/80 text-white text-[9px] px-1 rounded font-mono font-medium">${item.duration}</span>` : ''}
                    </div>

                    <div class="min-w-0 flex-1">
                        <h4 class="text-xs font-bold text-on-surface line-clamp-2 leading-snug">${item.title}</h4>
                        <div class="text-[10px] text-outline mt-1 flex items-center gap-1.5 flex-wrap">
                            <span class="truncate max-w-[120px]">${item.channel}</span>
                            <span class="px-1.5 py-0.2 rounded font-semibold ${item.category === 'song' ? 'bg-primary/10 text-primary' : 'bg-secondary-fixed text-on-secondary-fixed'}">
                                ${item.category === 'song' ? 'Song' : item.category === 'playlist' ? 'Playlist' : 'Mix'}
                            </span>
                        </div>
                    </div>

                    <div id="action-wrap-${item.id}" class="shrink-0">
                        ${actionHtml}
                    </div>
                </div>
                `;
            }).join('');

            list.insertAdjacentHTML('beforeend', html);

            if (displayedCount < filtered.length) {
                loadMore.classList.remove('hidden');
            } else {
                loadMore.classList.add('hidden');
            }
        }

        function renderInCardProgress(item) {
            return `
                <div class="flex items-center gap-1.5 px-2.5 py-1 rounded-full bg-primary/10 text-primary border border-primary/20 text-[10px] font-bold animate-pulse">
                    <span class="material-symbols-outlined text-[14px] animate-spin">autorenew</span>
                    <span>Syncing...</span>
                </div>
            `;
        }

        function markCardAsCompleted(id) {
            if (!id) return;
            downloadedIds.add(id);
            const wrap = document.getElementById(`action-wrap-${id}`);
            if (wrap) {
                wrap.innerHTML = `
                    <span class="done-badge px-2.5 py-1 rounded-full bg-emerald-50 text-emerald-700 text-[10px] font-bold border border-emerald-200 flex items-center gap-1">
                        <span class="material-symbols-outlined text-[13px] icon-fill">check_circle</span>
                        <span>Saved</span>
                    </span>
                `;
            }
        }

        function markCardAsFailed(id, error) {
            if (!id) return;
            const wrap = document.getElementById(`action-wrap-${id}`);
            if (wrap) {
                wrap.innerHTML = `
                    <button onclick="downloadTrack('', '${id}', 'Retry', this)" class="px-2.5 py-1 rounded-full bg-error/10 text-error text-[10px] font-bold border border-error/20 flex items-center gap-1 hover:bg-error hover:text-white transition" title="Retry download">
                        <span class="material-symbols-outlined text-[13px]">refresh</span>
                        <span>Retry</span>
                    </button>
                `;
            }
        }

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
                showToast("Failed to queue download", true);
            }
        }

        // ================= PLAYLIST INSPECTOR & SELECTIVE DOWNLOAD =================
        async function openPlaylistModal(url, title = 'Playlist Tracks') {
            const modal = document.getElementById('playlistModal');
            const titleEl = document.getElementById('plModalTitle');
            const totalBadge = document.getElementById('plModalTotalBadge');
            const list = document.getElementById('plModalTracksList');
            const selectAllCheck = document.getElementById('plSelectAllCheck');
            const btn = document.getElementById('plDownloadBtn');

            titleEl.innerText = title;
            totalBadge.innerText = "Inspecting tracks...";
            modal.classList.remove('hidden');
            selectAllCheck.checked = true;
            btn.disabled = false;

            list.innerHTML = `
                <div class="p-8 text-center text-outline text-xs">
                    <span class="material-symbols-outlined text-primary text-2xl animate-spin mb-2 block">autorenew</span>
                    Scanning tracks...
                </div>
            `;

            try {
                const res = await fetch(`/api/playlist/inspect?url=${encodeURIComponent(url)}`);
                const data = await res.json();

                if (!res.ok || !data || !data.tracks || data.tracks.length === 0) {
                    const errorMsg = (data && data.error) ? data.error : "Could not load playlist tracks. Make sure playlist is public or unlisted.";
                    list.innerHTML = `<div class="p-6 text-center text-error text-xs">${escapeHtml(errorMsg)}</div>`;
                    totalBadge.innerText = "0 tracks";
                    return;
                }

                currentInspectedPlaylist = data;
                titleEl.innerText = data.title;
                totalBadge.innerText = `${data.total} track${data.total === 1 ? '' : 's'} total`;

                const typeBadge = document.getElementById('plModalTypeBadge');
                if (typeBadge) {
                    if (data.total === 1) typeBadge.innerText = "Single Track";
                    else if (data.title.toLowerCase().includes('mix') || (data.id && data.id.startsWith('RD'))) typeBadge.innerText = "YouTube Mix";
                    else if (data.id === 'search_results') typeBadge.innerText = "Search Result";
                    else typeBadge.innerText = "Playlist";
                }

                renderPlaylistTracks(data.tracks);
                updatePlaylistSelectionCount();

            } catch (err) {
                list.innerHTML = `<div class="p-6 text-center text-error text-xs">Error inspecting tracks: ${escapeHtml(err.message)}</div>`;
            }
        }

        function renderPlaylistTracks(tracks) {
            const list = document.getElementById('plModalTracksList');
            list.innerHTML = tracks.map((tr, idx) => {
                const isSaved = downloadedIds.has(tr.id) || tr.in_library;
                const isChecked = !isSaved;

                return `
                <div class="p-2.5 rounded-xl bg-surface-container-low flex items-center justify-between gap-2.5 hover:bg-surface-container transition" id="pl-track-row-${tr.id}">
                    <input type="checkbox" data-track-id="${tr.id}" data-idx="${idx}" ${isChecked ? 'checked' : ''}
                        onchange="updatePlaylistSelectionCount()" class="pl-track-check rounded border-outline/40 text-primary focus:ring-0 shrink-0 w-4 h-4">

                    <div class="relative w-11 h-11 rounded-lg overflow-hidden shrink-0 bg-surface-container shadow-sm">
                        <img src="${tr.thumbnail}" alt="" class="w-full h-full object-cover">
                        ${tr.duration ? `<span class="absolute bottom-0.5 right-0.5 bg-black/80 text-white text-[8px] px-1 rounded font-mono">${tr.duration}</span>` : ''}
                    </div>

                    <div class="min-w-0 flex-1">
                        <p class="text-xs font-bold text-on-surface line-clamp-1 leading-snug">${tr.title}</p>
                        <div class="text-[10px] text-outline mt-0.5 flex items-center gap-1.5">
                            <span class="truncate max-w-[120px]">${tr.uploader}</span>
                            ${isSaved ? '<span class="px-1.5 py-0.2 rounded text-emerald-700 bg-emerald-100 font-bold text-[9px]">✓ In Library</span>' : ''}
                        </div>
                    </div>
                </div>
                `;
            }).join('');
        }

        function toggleSelectAllPlaylist(checked) {
            document.querySelectorAll('.pl-track-check').forEach(cb => cb.checked = checked);
            updatePlaylistSelectionCount();
        }

        function updatePlaylistSelectionCount() {
            const checks = document.querySelectorAll('.pl-track-check:checked');
            const total = document.querySelectorAll('.pl-track-check').length;
            const count = checks.length;

            const selectedCountText = document.getElementById('plSelectedCountText');
            const actionBtnText = document.getElementById('plDownloadBtnText');
            const btn = document.getElementById('plDownloadBtn');

            if (selectedCountText) selectedCountText.innerText = `${count} of ${total} selected`;
            if (actionBtnText) actionBtnText.innerText = `Download Selected (${count} track${count === 1 ? '' : 's'})`;
            if (btn) btn.disabled = (count === 0);
        }

        async function downloadSelectedPlaylistTracks() {
            if (!currentInspectedPlaylist || !currentInspectedPlaylist.tracks) return;

            const selectedChecks = Array.from(document.querySelectorAll('.pl-track-check:checked'));
            if (selectedChecks.length === 0) {
                showToast("Please select at least 1 track", true);
                return;
            }

            const selectedTracks = selectedChecks.map(cb => {
                const idx = parseInt(cb.getAttribute('data-idx'));
                return currentInspectedPlaylist.tracks[idx];
            });

            const btn = document.getElementById('plDownloadBtn');
            btn.disabled = true;
            btn.innerHTML = `<span class="material-symbols-outlined text-[16px] animate-spin">autorenew</span><span>Starting batch (${selectedTracks.length} tracks)...</span>`;

            try {
                const res = await fetch('/api/playlist/download_selected', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        playlist_title: currentInspectedPlaylist.title,
                        tracks: selectedTracks
                    })
                });
                await res.json();
                showToast(`Queue started for ${selectedTracks.length} tracks!`);
                closePlaylistModal();
            } catch (err) {
                showToast("Failed to queue playlist tracks", true);
                btn.disabled = false;
                btn.innerHTML = `<span class="material-symbols-outlined text-[16px]">download</span><span>Download Selected Tracks</span>`;
            }
        }

        function closePlaylistModal() {
            document.getElementById('playlistModal').classList.add('hidden');
        }

        // Direct Link Ingest from Sources Tab
        function inspectDirectUrl() {
            const input = document.getElementById('directUrlInput');
            const url = input.value.trim();
            if (!url) {
                showToast("Please paste a YouTube URL first", true);
                return;
            }
            openPlaylistModal(url, "Inspecting Link");
        }

        async function submitDirectLink(e) {
            e.preventDefault();
            const input = document.getElementById('directUrlInput');
            const btn = document.getElementById('directAddBtn');
            const url = input.value.trim();
            if (!url) return;

            btn.disabled = true;
            btn.innerHTML = '<span class="material-symbols-outlined text-[16px] animate-spin">autorenew</span><span>Adding...</span>';

            try {
                const formData = new FormData();
                formData.append('url', url);
                formData.append('sync_now', 'true');

                await fetch('/add', { method: 'POST', body: formData });
                showToast("Added and sync triggered!");
                input.value = '';
                btn.disabled = false;
                btn.innerHTML = '<span class="material-symbols-outlined text-[18px]">queue_music</span><span>+ Queue Directly</span>';
                setTimeout(() => location.reload(), 1500);
            } catch (err) {
                showToast("Failed to add source", true);
                btn.disabled = false;
                btn.innerHTML = '<span class="material-symbols-outlined text-[18px]">queue_music</span><span>+ Queue Directly</span>';
            }
        }

        async function pasteFromClipboard() {
            try {
                const text = await navigator.clipboard.readText();
                if (text) {
                    document.getElementById('directUrlInput').value = text;
                    showToast("Pasted from clipboard!");
                }
            } catch (err) {
                showToast("Could not access clipboard", true);
            }
        }

        async function deletePlaylist(url) {
            if (!confirm("Are you sure you want to remove this source?")) return;
            const formData = new FormData();
            formData.append('url', url);
            await fetch('/delete', { method: 'POST', body: formData });
            location.reload();
        }

        // ================= LIBRARY MANAGEMENT =================
        async function fetchLibrary() {
            const list = document.getElementById('libraryTracksList');
            const countBadge = document.getElementById('libTotalTracksBadge');
            list.innerHTML = `
                <div class="p-8 text-center text-outline text-xs">
                    <span class="material-symbols-outlined text-primary text-2xl animate-spin mb-2 block">autorenew</span>
                    Reading library files...
                </div>
            `;

            try {
                const res = await fetch('/api/library');
                const data = await res.json();
                allLibrarySongs = data.tracks || [];

                countBadge.innerText = `${allLibrarySongs.length} track${allLibrarySongs.length === 1 ? '' : 's'}`;
                renderLibraryList(allLibrarySongs);
            } catch (err) {
                list.innerHTML = `<div class="p-8 text-center text-error text-xs">Failed to load library: ${err.message}</div>`;
            }
        }

        function filterLibraryList(q) {
            const term = (q || '').toLowerCase();
            const filtered = allLibrarySongs.filter(t => 
                (t.title || '').toLowerCase().includes(term) ||
                (t.artist || '').toLowerCase().includes(term) ||
                (t.album || '').toLowerCase().includes(term)
            );
            renderLibraryList(filtered);
        }

        // --- Navidrome Library Rescan ---
        async function triggerRescan() {
            const icon = document.getElementById('rescanIcon');
            if (icon) icon.classList.add('animate-spin');
            showToast("Rescanning Navidrome library...");
            try {
                const res = await fetch('/api/library/rescan', { method: 'POST' });
                const data = await res.json();
                if (res.ok) {
                    showToast("✓ Navidrome library scan completed!");
                    fetchLibrary();
                } else {
                    showToast(`Rescan failed: ${data.message || 'Error'}`, true);
                }
            } catch (e) {
                showToast(`Rescan error: ${e.message}`, true);
            } finally {
                if (icon) icon.classList.remove('animate-spin');
            }
        }

        // --- Audio Preview Player ---
        let activeAudio = null;
        let activeAudioPath = null;

        function toggleAudioPreview(path, title, artist) {
            if (activeAudioPath === path && activeAudio) {
                if (activeAudio.paused) {
                    activeAudio.play();
                    setPreviewIcon(path, 'pause_circle');
                    updateAudioPreviewDock(title, artist, false);
                } else {
                    activeAudio.pause();
                    setPreviewIcon(path, 'play_circle');
                    updateAudioPreviewDock(title, artist, true);
                }
                return;
            }

            if (activeAudio) {
                activeAudio.pause();
                if (activeAudioPath) setPreviewIcon(activeAudioPath, 'play_circle');
                activeAudio = null;
            }

            activeAudioPath = path;
            activeAudio = new Audio(`/api/library/stream?path=${encodeURIComponent(path)}`);
            setPreviewIcon(path, 'pause_circle');
            updateAudioPreviewDock(title, artist, false);

            activeAudio.ontimeupdate = () => {
                if (!activeAudio || activeDownloadingId) return;
                const pct = (activeAudio.currentTime / (activeAudio.duration || 1)) * 100;
                const curMin = Math.floor(activeAudio.currentTime / 60);
                const curSec = Math.floor(activeAudio.currentTime % 60).toString().padStart(2, '0');
                const durMin = Math.floor((activeAudio.duration || 0) / 60);
                const durSec = Math.floor((activeAudio.duration || 0) % 60).toString().padStart(2, '0');
                const timeStr = `${curMin}:${curSec} / ${durMin}:${durSec}`;

                const progress = document.getElementById('dockProgressBar');
                const speed = document.getElementById('dockSpeed');
                if (progress) progress.style.width = `${pct}%`;
                if (speed) speed.innerText = timeStr;
            };

            activeAudio.onended = () => {
                stopAudioPreview();
            };

            activeAudio.onerror = () => {
                showToast('Audio preview stream failed', true);
                stopAudioPreview();
            };

            activeAudio.play().catch(e => {
                console.warn("Audio playback blocked:", e);
            });
        }

        function setPreviewIcon(path, iconName) {
            const btn = document.getElementById(`preview-btn-${path}`);
            if (btn) btn.innerHTML = `<span class="material-symbols-outlined text-[22px]">${iconName}</span>`;
        }

        function updateAudioPreviewDock(title, artist, isPaused) {
            const dock = document.getElementById('floatingDock');
            const titleEl = document.getElementById('dockTitle');
            const speedEl = document.getElementById('dockSpeed');
            const progress = document.getElementById('dockProgressBar');
            const actionWrap = document.getElementById('dockActionWrap');
            const eqWrap = document.getElementById('dockEqWrap');

            dock.classList.remove('opacity-0', 'translate-y-32', 'pointer-events-none');
            dock.classList.add('opacity-100', 'translate-y-0');

            titleEl.innerText = title;
            speedEl.innerText = artist || "Preview";
            progress.className = "bg-primary h-full rounded-full transition-all duration-200";

            if (isPaused) {
                eqWrap.innerHTML = '<span class="material-symbols-outlined text-outline text-[20px]">pause</span>';
            } else {
                eqWrap.innerHTML = `
                    <div class="flex items-end gap-0.5 h-4">
                        <span class="w-1 bg-primary rounded-full animate-wave-1"></span>
                        <span class="w-1 bg-primary rounded-full animate-wave-2"></span>
                        <span class="w-1 bg-primary rounded-full animate-wave-3"></span>
                    </div>
                `;
            }

            actionWrap.innerHTML = `
                <button onclick="toggleAudioPreview('${escapeHtml(activeAudioPath)}', '${escapeHtml(title)}', '${escapeHtml(artist)}')"
                    class="w-7 h-7 rounded-full bg-primary text-white flex items-center justify-center active:scale-95 transition" title="${isPaused ? 'Resume' : 'Pause'}">
                    <span class="material-symbols-outlined text-[18px]">${isPaused ? 'play_arrow' : 'pause'}</span>
                </button>
                <button onclick="stopAudioPreview()" class="w-6 h-6 rounded-full flex items-center justify-center text-outline hover:text-on-surface" title="Close Preview">
                    <span class="material-symbols-outlined text-[16px]">close</span>
                </button>
            `;
        }

        function stopAudioPreview() {
            if (activeAudio) {
                activeAudio.pause();
                activeAudio = null;
            }
            if (activeAudioPath) {
                setPreviewIcon(activeAudioPath, 'play_circle');
                activeAudioPath = null;
            }
            if (!activeDownloadingId) {
                hideDownloadDock();
            }
        }

        function renderLibraryList(tracks) {
            const list = document.getElementById('libraryTracksList');
            if (tracks.length === 0) {
                list.innerHTML = `
                    <div class="p-8 text-center text-outline text-xs bg-surface-container-lowest rounded-2xl border border-surface-container-high/60">
                        No songs found in your library.
                    </div>
                `;
                return;
            }

            list.innerHTML = tracks.map(tr => `
                <div class="p-3 rounded-2xl bg-surface-container-lowest shadow-sm border border-surface-container-high/60 flex items-center justify-between gap-3 hover:border-surface-container-highest transition" id="lib-row-${escapeHtml(tr.path)}">
                    <!-- Checkbox (Visible in Selection Mode) -->
                    <input type="checkbox" data-path="${escapeHtml(tr.path)}"
                        onchange="updateBatchCount()"
                        class="lib-select-check ${isSelectionMode ? '' : 'hidden'} rounded border-outline/40 text-primary focus:ring-0 w-4 h-4 shrink-0">

                    <!-- Thumbnail / Play trigger -->
                    <div onclick="toggleAudioPreview('${escapeHtml(tr.path)}', '${escapeHtml(tr.title)}', '${escapeHtml(tr.artist || 'Unknown Artist')}')"
                        class="relative w-11 h-11 rounded-xl overflow-hidden shrink-0 bg-surface-container shadow-sm flex items-center justify-center text-primary cursor-pointer group">
                        <span class="material-symbols-outlined text-[20px] group-hover:scale-110 transition">music_note</span>
                        <div class="absolute inset-0 bg-black/25 flex items-center justify-center opacity-0 group-hover:opacity-100 transition-opacity">
                            <span class="material-symbols-outlined text-white text-[18px]">play_arrow</span>
                        </div>
                    </div>

                    <!-- Meta -->
                    <div class="min-w-0 flex-1">
                        <h4 class="text-xs font-bold text-on-surface truncate">${escapeHtml(tr.title)}</h4>
                        <div class="text-[10px] text-outline mt-0.5 flex items-center gap-1.5 flex-wrap">
                            <span class="truncate max-w-[130px]">${escapeHtml(tr.artist || 'Unknown Artist')}</span>
                            <span class="px-1.5 py-0.2 rounded bg-surface-container-high text-on-surface-variant font-semibold">320k MP3</span>
                        </div>
                    </div>

                    <!-- Actions -->
                    <div class="shrink-0 flex items-center gap-1">
                        <button id="preview-btn-${escapeHtml(tr.path)}"
                            onclick="toggleAudioPreview('${escapeHtml(tr.path)}', '${escapeHtml(tr.title)}', '${escapeHtml(tr.artist || 'Unknown Artist')}')"
                            class="w-8 h-8 rounded-full flex items-center justify-center text-primary hover:bg-surface-container transition active:scale-95" title="Preview Audio">
                            <span class="material-symbols-outlined text-[22px]">play_circle</span>
                        </button>
                        <button onclick="deleteTrack('${escapeHtml(tr.path)}', '${escapeHtml(tr.title)}')"
                            class="w-8 h-8 rounded-full flex items-center justify-center text-outline hover:text-error hover:bg-surface-container transition active:scale-95" title="Delete Song">
                            <span class="material-symbols-outlined text-[18px]">delete</span>
                        </button>
                    </div>
                </div>
            `).join('');
            updateBatchCount();
        }

        function toggleSelectAllMode() {
            isSelectionMode = !isSelectionMode;
            const bar = document.getElementById('batchActionBar');
            const btnText = document.getElementById('selectionModeText');
            if (isSelectionMode) {
                bar.classList.remove('hidden');
                btnText.innerText = 'Cancel';
            } else {
                bar.classList.add('hidden');
                btnText.innerText = 'Select';
                document.querySelectorAll('.lib-select-check').forEach(cb => cb.checked = false);
            }
            document.querySelectorAll('.lib-select-check').forEach(cb => {
                if (isSelectionMode) cb.classList.remove('hidden');
                else cb.classList.add('hidden');
            });
            updateBatchCount();
        }

        function toggleSelectAll(checked) {
            document.querySelectorAll('.lib-select-check').forEach(cb => cb.checked = checked);
            updateBatchCount();
        }

        function updateBatchCount() {
            const selected = document.querySelectorAll('.lib-select-check:checked');
            const count = selected.length;
            const countText = document.getElementById('selectedCountText');
            const deleteBtn = document.getElementById('batchDeleteBtn');
            if (countText) countText.innerText = `${count} selected`;
            if (deleteBtn) deleteBtn.disabled = (count === 0);
        }

        async function deleteTrack(path, title) {
            if (!confirm(`Delete "${title}" from server?`)) return;
            try {
                const formData = new FormData();
                formData.append('track_path', path);
                const res = await fetch('/api/library/delete', { method: 'POST', body: formData });
                const data = await res.json();
                if (data.status === 'deleted') {
                    showToast(`Deleted: ${title}`);
                    fetchLibrary();
                } else {
                    showToast(data.error || "Failed to delete", true);
                }
            } catch (e) {
                showToast("Delete failed", true);
            }
        }

        async function deleteSelectedBatch() {
            const checks = Array.from(document.querySelectorAll('.lib-select-check:checked'));
            if (checks.length === 0) return;
            if (!confirm(`Delete ${checks.length} selected tracks from the server library?`)) return;

            const paths = checks.map(cb => cb.getAttribute('data-path'));
            try {
                const res = await fetch('/api/library/delete_batch', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ paths: paths })
                });
                const data = await res.json();
                showToast(`Deleted ${data.deleted_count} tracks!`);
                toggleSelectAllMode();
                fetchLibrary();
            } catch (e) {
                showToast("Batch delete failed", true);
            }
        }

        // ================= CONSOLE & TELEMETRY =================
        async function fetchLogs() {
            try {
                const res = await fetch('/api/logs');
                const data = await res.json();
                document.getElementById('logConsole').innerText = data.logs;
                updateStatusBadge(data.is_syncing);
            } catch (e) {}
        }

        async function stopDownload() {
            try {
                await fetch('/api/stop', { method: 'POST' });
                showToast("Download cancelled", true);
                hideDownloadDock();
            } catch (e) {
                showToast("Failed to stop download", true);
            }
        }

        function updateStatusBadge(isSyncing) {
            const pulseDot = document.getElementById('headerPulseDot');
            const statusText = document.getElementById('headerStatusText');
            const logsStopBtn = document.getElementById('logsStopBtn');

            if (isSyncing) {
                if (pulseDot) pulseDot.className = "w-1.5 h-1.5 rounded-full bg-amber-500 animate-ping";
                if (statusText) {
                    statusText.className = "text-[10px] font-bold text-amber-600 tracking-wider uppercase";
                    statusText.innerText = "Syncing Pipeline";
                }
                if (logsStopBtn) logsStopBtn.classList.remove('hidden');
            } else {
                if (pulseDot) pulseDot.className = "w-1.5 h-1.5 rounded-full bg-emerald-500 animate-pulse";
                if (statusText) {
                    statusText.className = "text-[10px] font-bold text-outline tracking-wider uppercase";
                    statusText.innerText = "Navidrome Online";
                }
                if (logsStopBtn) logsStopBtn.classList.add('hidden');
            }
        }

        // Floating Miniplayer / Download Dock Controls
        function updateDownloadDock(dl) {
            const dock = document.getElementById('floatingDock');
            const title = document.getElementById('dockTitle');
            const speed = document.getElementById('dockSpeed');
            const progress = document.getElementById('dockProgressBar');
            const actionWrap = document.getElementById('dockActionWrap');
            const eqWrap = document.getElementById('dockEqWrap');

            const isDone = dl.status === 'completed';
            const isFailed = dl.status === 'failed';
            const isCancelled = dl.status === 'cancelled';

            dock.classList.remove('opacity-0', 'translate-y-32', 'pointer-events-none');
            dock.classList.add('opacity-100', 'translate-y-0');

            if (isFailed) {
                title.innerText = dl.error ? `Failed: ${dl.error}` : "Download Failed";
                speed.innerText = "Error";
                speed.className = "text-[10px] font-mono font-bold text-error shrink-0";
                progress.style.width = "100%";
                progress.className = "bg-error h-full rounded-full";
                actionWrap.innerHTML = `
                    <button onclick="switchTab('cookies')" class="px-2.5 py-1 bg-amber-500 text-white rounded-full text-[10px] font-bold">Auth</button>
                    <button onclick="hideDownloadDock()" class="w-6 h-6 rounded-full flex items-center justify-center text-outline"><span class="material-symbols-outlined text-[16px]">close</span></button>
                `;
            } else if (isDone) {
                eqWrap.innerHTML = '<span class="material-symbols-outlined text-emerald-600 text-[20px] icon-fill">check_circle</span>';
                title.innerText = dl.title || "Track Downloaded";
                speed.innerText = "✓ 100%";
                speed.className = "text-[10px] font-mono font-bold text-emerald-600 shrink-0";
                progress.style.width = "100%";
                progress.className = "bg-emerald-500 h-full rounded-full";
                actionWrap.innerHTML = `
                    <button onclick="hideDownloadDock()" class="w-6 h-6 rounded-full flex items-center justify-center text-outline"><span class="material-symbols-outlined text-[16px]">close</span></button>
                `;
            } else {
                title.innerText = dl.title || "Downloading audio...";
                const pct = Math.round(dl.percent || 0);
                speed.innerText = `${pct}% · ${dl.speed || ''}`;
                speed.className = "text-[10px] font-mono font-bold text-primary shrink-0";
                progress.style.width = `${Math.max(5, pct)}%`;
                progress.className = "bg-gradient-to-r from-primary to-secondary h-full rounded-full";
                actionWrap.innerHTML = `
                    <button onclick="stopDownload()" class="w-7 h-7 rounded-full bg-surface-container hover:bg-error/10 hover:text-error text-on-surface-variant flex items-center justify-center transition" title="Cancel">
                        <span class="material-symbols-outlined text-[16px]">close</span>
                    </button>
                `;
            }
        }

        function hideDownloadDock() {
            const dock = document.getElementById('floatingDock');
            dock.classList.add('opacity-0', 'translate-y-32', 'pointer-events-none');
            dock.classList.remove('opacity-100', 'translate-y-0');
        }

        // Periodic Background Status Poller (every 1.8s)
        setInterval(() => {
            const autoLogs = document.getElementById('autoRefreshLogs');
            const logsTab = document.getElementById('tab-logs');
            if (autoLogs && autoLogs.checked && logsTab && !logsTab.classList.contains('hidden')) {
                fetchLogs();
            }

            fetch('/api/status').then(r => r.json()).then(d => {
                updateStatusBadge(d.is_syncing);

                if (d.completed_ids) {
                    d.completed_ids.forEach(id => {
                        markCardAsCompleted(id);
                    });
                }

                const dl = d.current_download || d.current;
                if (dl && (dl.status !== 'idle' || d.is_syncing)) {
                    const isDone = dl.status === 'completed' || dl.percent >= 100;
                    const isFailed = dl.status === 'failed';

                    if (isDone) {
                        if (dl.id) markCardAsCompleted(dl.id);
                        updateDownloadDock(dl);
                        if (lastNotifiedId !== dl.id || lastNotifiedStatus !== 'completed') {
                            lastNotifiedId = dl.id;
                            lastNotifiedStatus = 'completed';
                            showToast(`✓ Download completed: ${dl.title}`);
                            fetchLibrary();
                        }
                    } else if (isFailed) {
                        if (dl.id) markCardAsFailed(dl.id, dl.error);
                        updateDownloadDock(dl);
                        if (lastNotifiedId !== dl.id || lastNotifiedStatus !== 'failed') {
                            lastNotifiedId = dl.id;
                            lastNotifiedStatus = 'failed';
                            showToast(`❌ Failed: ${dl.error || dl.title}`, true);
                        }
                    } else if (d.is_syncing) {
                        activeDownloadingId = dl.id || dl.url;
                        updateDownloadDock(dl);
                    }
                } else {
                    if (activeDownloadingId && !d.is_syncing && !activeAudio) {
                        hideDownloadDock();
                        activeDownloadingId = null;
                    }
                }
            }).catch(() => {});
        }, 1800);
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

@app.route("/api/playlist/inspect")
def api_playlist_inspect():
    url = request.args.get("url", "").strip()
    if not url:
        return jsonify({"error": "No URL provided"}), 400
    try:
        data = inspect_playlist(url)
        if data:
            return jsonify(data)
        return jsonify({"error": "Could not inspect tracks from this link. Make sure it is public or unlisted."}), 400
    except Exception as e:
        print("api_playlist_inspect error:", e)
        return jsonify({"error": f"Inspection failed: {str(e)}"}), 500

@app.route("/api/playlist/download_selected", methods=["POST"])
def api_playlist_download_selected():
    data = request.json or {}
    playlist_title = data.get("playlist_title", "Selected Playlist Tracks")
    tracks = data.get("tracks", [])
    if not tracks:
        return jsonify({"error": "No tracks provided"}), 400
    
    success = trigger_playlist_queue_async(tracks, playlist_title)
    if success:
        return jsonify({"status": "queued", "count": len(tracks)})
    return jsonify({"error": "A download is already in progress"}), 409

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

@app.route("/api/library/delete_batch", methods=["POST"])
def api_library_delete_batch():
    paths = []
    data = request.get_json(silent=True)
    if data and isinstance(data, dict) and "paths" in data:
        paths = data.get("paths", [])
    elif request.form.getlist("paths[]"):
        paths = request.form.getlist("paths[]")
    elif request.form.get("paths"):
        try:
            paths = json.loads(request.form.get("paths"))
        except Exception:
            paths = [request.form.get("paths")]
            
    if not paths:
        return jsonify({"error": "No paths provided"}), 400
        
    deleted_count = 0
    lib_root = os.path.realpath(LIBRARY_DIR)
    
    for rel_path in paths:
        full_path = os.path.realpath(os.path.join(LIBRARY_DIR, rel_path))
        if full_path.startswith(lib_root) and os.path.exists(full_path):
            try:
                os.remove(full_path)
                deleted_count += 1
                parent = os.path.dirname(full_path)
                while parent != lib_root and os.path.exists(parent):
                    if not os.listdir(parent):
                        os.rmdir(parent)
                        parent = os.path.dirname(parent)
                    else:
                        break
            except Exception:
                pass
                
    return jsonify({"status": "ok", "deleted_count": deleted_count})


@app.route("/api/library/rescan", methods=["POST"])
def api_library_rescan():
    nav_bin = "/opt/navidrome/navidrome"
    nav_cfg = "/var/lib/navidrome/navidrome.toml"
    try:
        if os.path.isfile(nav_bin) and os.path.isfile(nav_cfg):
            cmd = ["sudo", nav_bin, "scan", "--configfile", nav_cfg]
            res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=30)
            if res.returncode == 0:
                return jsonify({"status": "ok", "message": "Navidrome library scan completed successfully"})
            else:
                err_msg = res.stderr.strip() or res.stdout.strip()
                return jsonify({"status": "error", "message": f"Scan failed: {err_msg}"}), 500
        else:
            return jsonify({"status": "ok", "message": "Rescan simulated (Navidrome not on this host)"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

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
        pq_copy = dict(playlist_queue)
    return jsonify({
        "is_syncing": is_syncing,
        "current": dl_copy,
        "current_download": dl_copy,
        "playlist_queue": pq_copy,
        "completed_ids": list(completed_download_ids),
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
