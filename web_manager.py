import os
import subprocess
import threading
import urllib.request
import urllib.parse
import json
import re
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
    return os.path.exists(COOKIES_FILE) and os.path.getsize(COOKIES_FILE) > 10

def search_youtube(query, limit=8):
    if not query.strip():
        return []
    url = f"https://www.youtube.com/results?search_query={urllib.parse.quote(query)}"
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept-Language": "en-US,en;q=0.9"
    })
    try:
        with urllib.request.urlopen(req, timeout=5) as response:
            html = response.read().decode('utf-8')
    except Exception as e:
        print("Search error:", e)
        return []

    match = re.search(r"var ytInitialData = ({.*?});</script>", html)
    if not match:
        match = re.search(r"ytInitialData = ({.*?});", html)
    if not match:
        return []

    try:
        data = json.loads(match.group(1))
    except Exception:
        return []

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
    return items

HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Music Downloader & Sync</title>
    <script src="https://cdn.tailwindcss.com"></script>
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
    <style>
        body { background-color: #0f172a; color: #f8fafc; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; }
    </style>
</head>
<body class="min-h-screen p-3 md:p-8 flex flex-col items-center">
    <div class="w-full max-w-2xl space-y-5">
        
        <!-- Header -->
        <div class="flex items-center justify-between bg-slate-800/80 p-4 rounded-2xl border border-slate-700/60 shadow-lg backdrop-blur">
            <div class="flex items-center space-x-3">
                <div class="w-10 h-10 rounded-xl bg-gradient-to-tr from-rose-500 to-amber-500 flex items-center justify-center text-white text-xl shadow-md">
                    <i class="fa-brands fa-youtube"></i>
                </div>
                <div>
                    <h1 class="text-xl font-bold tracking-tight">Music Sync Manager</h1>
                    <p class="text-xs text-slate-400">Search, Download & Sync to Navidrome</p>
                </div>
            </div>
            <a href="http://{{ host }}:4533" target="_blank" class="px-3.5 py-2 bg-indigo-600 hover:bg-indigo-500 text-xs font-semibold rounded-xl flex items-center space-x-1.5 transition shadow">
                <i class="fa-solid fa-music"></i>
                <span>Open Player</span>
            </a>
        </div>

        <!-- Search YouTube Box -->
        <div class="bg-gradient-to-b from-slate-800/90 to-slate-800/60 p-5 rounded-2xl border border-slate-700/80 shadow-xl space-y-4">
            <div class="flex items-center justify-between">
                <h2 class="text-sm font-semibold uppercase tracking-wider text-slate-300 flex items-center space-x-2">
                    <i class="fa-solid fa-magnifying-glass text-rose-500"></i>
                    <span>Search YouTube Directly</span>
                </h2>
                <span class="text-2xs text-slate-400">No URL copying needed</span>
            </div>

            <div class="flex space-x-2">
                <input type="text" id="searchInput" placeholder="Search by song name, artist, or album..."
                    class="w-full px-4 py-3 bg-slate-900 border border-slate-700 rounded-xl text-sm focus:outline-none focus:border-rose-500 transition text-slate-100 placeholder-slate-500"
                    onkeydown="if(event.key==='Enter') doSearch()">
                <button onclick="doSearch()" id="searchBtn"
                    class="px-5 py-3 bg-rose-600 hover:bg-rose-500 font-semibold text-xs rounded-xl shadow-md transition shrink-0 flex items-center space-x-2">
                    <i class="fa-solid fa-magnifying-glass"></i>
                    <span>Search</span>
                </button>
            </div>

            <!-- Search Results Container -->
            <div id="searchResults" class="hidden space-y-2 pt-2 border-t border-slate-700/50 max-h-96 overflow-y-auto pr-1"></div>
        </div>

        <!-- Add by URL Card -->
        <div class="bg-slate-800/60 p-5 rounded-2xl border border-slate-700/60 shadow-lg space-y-3">
            <details class="group">
                <summary class="cursor-pointer text-sm font-semibold uppercase tracking-wider text-slate-400 flex items-center justify-between">
                    <span class="flex items-center space-x-2">
                        <i class="fa-solid fa-link text-indigo-400"></i>
                        <span>Paste Direct Link (Playlists / Albums)</span>
                    </span>
                    <i class="fa-solid fa-chevron-down group-open:rotate-180 transition-transform text-xs"></i>
                </summary>
                <form action="/add" method="POST" class="space-y-3 mt-3">
                    <div>
                        <input type="url" name="url" placeholder="https://music.youtube.com/playlist?list=... or video URL" required
                            class="w-full px-4 py-2.5 bg-slate-900 border border-slate-700 rounded-xl text-xs focus:outline-none focus:border-indigo-500 transition text-slate-100 placeholder-slate-500">
                    </div>
                    <div class="flex items-center justify-between">
                        <label class="flex items-center space-x-2 text-xs text-slate-300">
                            <input type="checkbox" name="sync_now" value="true" checked class="rounded border-slate-700 text-indigo-500 focus:ring-0">
                            <span>Start download immediately</span>
                        </label>
                        <button type="submit" class="px-4 py-2 bg-indigo-600 hover:bg-indigo-500 font-semibold text-xs rounded-xl shadow transition">
                            Add Link
                        </button>
                    </div>
                </form>
            </details>
        </div>

        <!-- Cookies Card -->
        <div class="bg-slate-800/60 p-5 rounded-2xl border border-slate-700/60 shadow-lg space-y-3">
            <div class="flex items-center justify-between">
                <h2 class="text-sm font-semibold uppercase tracking-wider text-slate-400 flex items-center space-x-2">
                    <i class="fa-solid fa-cookie-bite text-amber-400"></i>
                    <span>YouTube Authentication Cookies</span>
                </h2>
                {% if cookies_ok %}
                <span class="px-2 py-0.5 rounded-full text-2xs bg-emerald-500/20 text-emerald-400 border border-emerald-500/30 font-medium">
                    <i class="fa-solid fa-check mr-1"></i>Cookies Configured
                </span>
                {% else %}
                <span class="px-2 py-0.5 rounded-full text-2xs bg-amber-500/20 text-amber-400 border border-amber-500/30 font-medium animate-pulse">
                    <i class="fa-solid fa-triangle-exclamation mr-1"></i>Required for Datacenter IP
                </span>
                {% endif %}
            </div>
            
            <details class="group bg-slate-900/60 rounded-xl border border-slate-700/50 p-3 text-xs text-slate-300">
                <summary class="cursor-pointer font-semibold text-amber-400 hover:text-amber-300 flex items-center justify-between">
                    <span>How to get your YouTube cookies (Takes 30 seconds)</span>
                    <i class="fa-solid fa-chevron-down group-open:rotate-180 transition-transform"></i>
                </summary>
                <div class="mt-3 space-y-2 text-slate-400 leading-relaxed pl-1">
                    <p>1. In Chrome / Firefox / Edge, install extension: <b class="text-slate-200">"Get cookies.txt LOCALLY"</b>.</p>
                    <p>2. Open <a href="https://www.youtube.com" target="_blank" class="text-indigo-400 underline">youtube.com</a> while signed in.</p>
                    <p>3. Click the extension icon and click <b>Export</b> (or copy all text).</p>
                    <p>4. Paste or upload the file below and click <b>Save Cookies</b>.</p>
                </div>
            </details>

            <form action="/cookies" method="POST" enctype="multipart/form-data" class="space-y-3 pt-1">
                <div>
                    <textarea name="cookies_text" rows="2" placeholder="Paste your Netscape-format cookies.txt text here..."
                        class="w-full px-3 py-2 bg-slate-900 border border-slate-700 rounded-xl text-xs font-mono focus:outline-none focus:border-amber-500 transition text-slate-200 placeholder-slate-600"></textarea>
                </div>
                <div class="flex items-center justify-between">
                    <div class="text-xs text-slate-400 flex items-center space-x-2">
                        <span>Or file:</span>
                        <input type="file" name="cookies_file" accept=".txt" class="text-xs text-slate-400 file:mr-2 file:py-1 file:px-2 file:rounded-lg file:border-0 file:text-xs file:bg-slate-700 file:text-slate-200 hover:file:bg-slate-600 cursor-pointer">
                    </div>
                    <button type="submit" class="px-4 py-1.5 bg-amber-600 hover:bg-amber-500 font-semibold text-xs rounded-xl shadow transition">
                        Save Cookies
                    </button>
                </div>
            </form>
        </div>

        <!-- Synced Playlists & Songs Card -->
        <div class="bg-slate-800/60 p-5 rounded-2xl border border-slate-700/60 shadow-lg space-y-4">
            <div class="flex items-center justify-between">
                <h2 class="text-sm font-semibold uppercase tracking-wider text-slate-400 flex items-center space-x-2">
                    <i class="fa-solid fa-list-check text-indigo-400"></i>
                    <span>Synced Playlists & Links ({{ playlists|length }})</span>
                </h2>
                <form action="/sync" method="POST">
                    <button type="submit" {% if is_syncing %}disabled{% endif %} class="px-3 py-1.5 {% if is_syncing %}bg-slate-700 text-slate-500{% else %}bg-slate-700 hover:bg-slate-600 text-slate-200{% endif %} text-xs font-semibold rounded-lg transition flex items-center space-x-1.5">
                        <i class="fa-solid fa-rotate {% if is_syncing %}fa-spin{% endif %}"></i>
                        <span>{% if is_syncing %}Syncing...{% else %}Sync All Now{% endif %}</span>
                    </button>
                </form>
            </div>

            {% if playlists %}
            <div class="divide-y divide-slate-700/60 max-h-56 overflow-y-auto pr-1">
                {% for pl in playlists %}
                <div class="py-2.5 flex items-center justify-between space-x-3">
                    <div class="truncate text-xs font-mono text-slate-300 flex items-center space-x-2">
                        <i class="fa-solid fa-link text-slate-500 text-2xs"></i>
                        <span class="truncate">{{ pl }}</span>
                    </div>
                    <form action="/delete" method="POST" class="shrink-0">
                        <input type="hidden" name="url" value="{{ pl }}">
                        <button type="submit" class="text-slate-500 hover:text-rose-400 p-1.5 transition text-xs" title="Remove playlist">
                            <i class="fa-solid fa-trash-can"></i>
                        </button>
                    </form>
                </div>
                {% endfor %}
            </div>
            {% else %}
            <p class="text-xs text-slate-500 italic text-center py-4">No music added yet. Use the search box above or paste a link!</p>
            {% endif %}
        </div>

        <!-- Logs & Status Card -->
        <div class="bg-slate-800/60 p-5 rounded-2xl border border-slate-700/60 shadow-lg space-y-3">
            <div class="flex items-center justify-between">
                <div class="flex items-center space-x-2">
                    <h2 class="text-sm font-semibold uppercase tracking-wider text-slate-400">Live Sync Logs</h2>
                    {% if is_syncing %}
                    <span class="px-2 py-0.5 rounded-full text-2xs bg-amber-500/20 text-amber-400 border border-amber-500/30 font-medium animate-pulse">Downloading...</span>
                    {% else %}
                    <span class="px-2 py-0.5 rounded-full text-2xs bg-emerald-500/20 text-emerald-400 border border-emerald-500/30 font-medium">Idle</span>
                    {% endif %}
                </div>
                <button onclick="location.reload()" class="text-xs text-slate-400 hover:text-slate-200">
                    <i class="fa-solid fa-arrows-rotate"></i> Refresh
                </button>
            </div>
            <pre class="bg-slate-950 p-3.5 rounded-xl text-2xs font-mono text-slate-400 overflow-x-auto max-h-48 border border-slate-800/80 whitespace-pre-wrap">{{ logs }}</pre>
        </div>

    </div>

    <script>
        async function doSearch() {
            const query = document.getElementById('searchInput').value.trim();
            if (!query) return;

            const btn = document.getElementById('searchBtn');
            const container = document.getElementById('searchResults');
            
            btn.innerHTML = '<i class="fa-solid fa-spinner fa-spin"></i><span>Searching...</span>';
            btn.disabled = true;
            container.classList.remove('hidden');
            container.innerHTML = '<p class="text-xs text-slate-400 text-center py-4"><i class="fa-solid fa-spinner fa-spin mr-2"></i>Searching YouTube...</p>';

            try {
                const res = await fetch(`/api/search?q=${encodeURIComponent(query)}`);
                const data = await res.json();
                
                if (!data.results || data.results.length === 0) {
                    container.innerHTML = '<p class="text-xs text-slate-400 text-center py-4">No results found.</p>';
                    return;
                }

                container.innerHTML = data.results.map(item => `
                    <div class="flex items-center justify-between bg-slate-900/80 p-2.5 rounded-xl border border-slate-700/50 hover:border-slate-600 transition space-x-3">
                        <img src="${item.thumbnail}" class="w-16 h-12 rounded-lg object-cover shrink-0 bg-slate-800">
                        <div class="min-w-0 flex-1">
                            <h3 class="text-xs font-semibold text-slate-100 truncate">${item.title}</h3>
                            <p class="text-2xs text-slate-400 truncate">${item.channel} • <span class="text-rose-400 font-medium">${item.duration}</span></p>
                        </div>
                        <button onclick="addAndDownload('${item.url}', this)"
                            class="px-3 py-1.5 bg-rose-600 hover:bg-rose-500 font-semibold text-xs rounded-lg shadow transition shrink-0 flex items-center space-x-1.5">
                            <i class="fa-solid fa-plus"></i>
                            <span>Download</span>
                        </button>
                    </div>
                `).join('');

            } catch (err) {
                container.innerHTML = `<p class="text-xs text-rose-400 text-center py-4">Error searching: ${err.message}</p>`;
            } finally {
                btn.innerHTML = '<i class="fa-solid fa-magnifying-glass"></i><span>Search</span>';
                btn.disabled = false;
            }
        }

        async function addAndDownload(url, btnElement) {
            btnElement.disabled = true;
            btnElement.className = "px-3 py-1.5 bg-emerald-600 font-semibold text-xs rounded-lg transition shrink-0 flex items-center space-x-1.5 text-white";
            btnElement.innerHTML = '<i class="fa-solid fa-check"></i><span>Downloading...</span>';

            const formData = new FormData();
            formData.append('url', url);
            formData.append('sync_now', 'true');

            await fetch('/add', {
                method: 'POST',
                body: formData
            });

            setTimeout(() => {
                location.reload();
            }, 1000);
        }
    </script>
</body>
</html>
"""

@app.route("/")
def index():
    playlists = get_playlists()
    logs = "No logs yet."
    if os.path.exists(LOG_FILE):
        try:
            with open(LOG_FILE, "r", encoding="utf-8", errors="ignore") as f:
                lines = f.readlines()
                logs = "".join(lines[-35:]) if lines else "Empty log."
        except Exception as e:
            logs = f"Error reading logs: {e}"
    host = request.host.split(":")[0]
    return render_template_string(HTML_TEMPLATE, playlists=playlists, logs=logs, is_syncing=is_syncing, host=host, cookies_ok=has_cookies())

@app.route("/api/search")
def api_search():
    query = request.args.get("q", "").strip()
    results = search_youtube(query)
    return jsonify({"results": results})

@app.route("/add", methods=["POST"])
def add():
    url = request.form.get("url", "").strip()
    sync_now = request.form.get("sync_now") == "true"
    if url:
        playlists = get_playlists()
        if url not in playlists:
            playlists.append(url)
            save_playlists(playlists)
        if sync_now and not is_syncing:
            threading.Thread(target=run_sync_thread, daemon=True).start()
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
    global is_syncing
    if not is_syncing:
        threading.Thread(target=run_sync_thread, daemon=True).start()
    return redirect(url_for("index"))

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=4534)
