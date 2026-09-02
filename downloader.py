import os
import re
from pathlib import Path

# Clean PM2 IPC channel variables that break Deno / child processes
os.environ.pop("NODE_CHANNEL_FD", None)

# Ensure Deno and local bin paths are accessible
home = os.path.expanduser("~")
deno_path = os.path.join(home, ".deno", "bin")
local_bin = os.path.join(home, ".local", "bin")
current_path = os.environ.get("PATH", "")
os.environ["PATH"] = f"{deno_path}:{local_bin}:{current_path}"

try:
    import static_ffmpeg
    static_ffmpeg.add_paths()
except Exception:
    pass

import json
import time
import urllib.request
import urllib.parse
import yt_dlp

DOWNLOADS_DIR = Path("downloads")
DOWNLOADS_DIR.mkdir(exist_ok=True)


def video_id_to_url(video_id: str) -> str:
    """Convert a YouTube video ID to a full URL."""
    return f"https://www.youtube.com/watch?v={video_id}"


def get_base_options() -> dict:
    options = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "concurrent_fragment_downloads": 4,
        "buffersize": 1024 * 64,
        "http_chunk_size": 10485760,
        "socket_timeout": 15,
    }

    cookies_file = os.getenv("COOKIES_FILE", "cookies.txt")
    if cookies_file and Path(cookies_file).exists():
        options["cookiefile"] = cookies_file

    browser = os.getenv("COOKIES_BROWSER", "none")
    if browser and browser.lower() != "none":
        options["cookiesfrombrowser"] = (browser,)

    user_agent = os.getenv("USER_AGENT")
    if user_agent:
        options["http_headers"] = {"User-Agent": user_agent}

    return options


def download_from_cloud_api(video_id: str, job_id: str, progress_dict: dict = None) -> Path | None:
    """
    Delegate the conversion to external Cloud MP3 API.
    VPS performs 0% CPU transcoding, just downloads the finished MP3 via HTTP.
    """
    job_dir = DOWNLOADS_DIR / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    out_file = job_dir / f"{job_id}.mp3"

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "application/json, text/plain, */*"
    }

    # Method 1: RapidAPI if configured
    rapidapi_key = os.getenv("RAPIDAPI_KEY")
    if rapidapi_key:
        try:
            if progress_dict is not None:
                progress_dict[job_id] = "⚡ Converting via RapidAPI..."
            rapid_url = f"https://coolguruji-youtube-to-mp3-download-v1.p.rapidapi.com/?id={video_id}"
            rapid_headers = {
                **headers,
                "x-rapidapi-host": "coolguruji-youtube-to-mp3-download-v1.p.rapidapi.com",
                "x-rapidapi-key": rapidapi_key
            }
            req = urllib.request.Request(rapid_url, headers=rapid_headers)
            with urllib.request.urlopen(req, timeout=20) as res:
                r_data = json.loads(res.read().decode("utf-8"))
                dl_url = r_data.get("link") or r_data.get("download") or r_data.get("url")
                if dl_url:
                    if progress_dict is not None:
                        progress_dict[job_id] = "📥 Fetching MP3 stream..."
                    dl_req = urllib.request.Request(dl_url, headers=headers)
                    with urllib.request.urlopen(dl_req, timeout=60) as dl_res:
                        with open(out_file, "wb") as f:
                            f.write(dl_res.read())
                    return out_file
        except Exception as e:
            print(f"[CloudAPI] RapidAPI failed: {e}")

    # Method 2: Free Cloud Converter API (savenow / loader.to infrastructure)
    try:
        if progress_dict is not None:
            progress_dict[job_id] = "☁️ Offloading conversion to Cloud API..."

        api_init_url = f"https://loader.to/ajax/download.php?format=mp3&url=https://www.youtube.com/watch?v={video_id}"
        req = urllib.request.Request(api_init_url, headers=headers)
        with urllib.request.urlopen(req, timeout=15) as res:
            init_data = json.loads(res.read().decode("utf-8"))

        p_url = init_data.get("progress_url")
        if not p_url and init_data.get("id"):
            p_url = f"https://p.oceansaver.in/ajax/progress.php?id={init_data['id']}"

        if p_url:
            download_url = None
            for i in range(25):
                time.sleep(1.2)
                try:
                    p_req = urllib.request.Request(p_url, headers=headers)
                    with urllib.request.urlopen(p_req, timeout=10) as p_res:
                        p_data = json.loads(p_res.read().decode("utf-8"))
                        
                        pct = p_data.get("progress", 0) // 10
                        if progress_dict is not None:
                            bar = '█' * min(10, max(0, pct // 10)) + '░' * (10 - min(10, max(0, pct // 10)))
                            progress_dict[job_id] = f"[{bar}] {min(100, pct)}%\n⚡ Cloud converting on remote API..."

                        if p_data.get("download_url"):
                            download_url = p_data["download_url"]
                            break
                        if p_data.get("success") == 1 and p_data.get("download_url"):
                            download_url = p_data["download_url"]
                            break
                except Exception:
                    pass

            if download_url:
                if progress_dict is not None:
                    progress_dict[job_id] = "📥 Downloading finished MP3 from CDN..."

                dl_req = urllib.request.Request(download_url, headers=headers)
                with urllib.request.urlopen(dl_req, timeout=90) as dl_res:
                    with open(out_file, "wb") as f:
                        f.write(dl_res.read())
                return out_file

    except Exception as e:
        print(f"[CloudAPI] Cloud converter failed: {e}")

    return None


def create_progress_hook(job_id: str, progress_dict: dict):
    def progress_hook(d):
        if progress_dict is not None and d['status'] == 'downloading':
            percent_str = d.get('_percent_str', '').strip()
            speed_str = d.get('_speed_str', '').strip()
            eta_str = d.get('_eta_str', '').strip()

            # Remove ANSI escape sequences (colors) from yt-dlp output
            ansi_escape = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')
            percent_str = ansi_escape.sub('', percent_str)
            speed_str = ansi_escape.sub('', speed_str)
            eta_str = ansi_escape.sub('', eta_str)

            # Create a simple progress bar
            try:
                pct = float(percent_str.replace('%', ''))
                filled = int(pct / 10)
                bar = '█' * filled + '░' * (10 - filled)
                progress_dict[job_id] = f"[{bar}] {percent_str}\n🚀 {speed_str} | ⏳ ETA: {eta_str}"
            except Exception:
                progress_dict[job_id] = f"{percent_str} ({speed_str}) ETA: {eta_str}"
    return progress_hook


def download_audio_fallback(url: str, job_id: str, progress_dict: dict = None) -> Path:
    """Local fallback if Cloud API fails."""
    job_dir = DOWNLOADS_DIR / job_id
    job_dir.mkdir(parents=True, exist_ok=True)

    options = {
        **get_base_options(),
        "format": "bestaudio/best",
        "writethumbnail": True,
        "postprocessors": [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "192",
            },
            {
                "key": "EmbedThumbnail",
            },
        ],
        "outtmpl": str(job_dir / f"{job_id}.%(ext)s"),
        "restrictfilenames": True,
        "windowsfilenames": True,
    }

    if progress_dict is not None:
        options["progress_hooks"] = [create_progress_hook(job_id, progress_dict)]

    with yt_dlp.YoutubeDL(options) as ydl:
        info = ydl.extract_info(url, download=True)
        original_filename = ydl.prepare_filename(info)
        filepath = Path(original_filename).with_suffix(".mp3")
        return filepath


def download_audio_by_id(video_id: str, job_id: str, progress_dict: dict = None) -> Path:
    """
    Main entry point:
    1. Delegates conversion to External Cloud API (0% VPS CPU load).
    2. Falls back to local engine only if Cloud API is unreachable.
    """
    # 1. Try Cloud API first
    cloud_result = download_from_cloud_api(video_id, job_id, progress_dict)
    if cloud_result and cloud_result.exists() and cloud_result.stat().st_size > 1000:
        return cloud_result

    # 2. Local Fallback
    url = video_id_to_url(video_id)
    return download_audio_fallback(url, job_id, progress_dict)
