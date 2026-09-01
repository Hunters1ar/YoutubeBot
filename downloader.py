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
        "concurrent_fragment_downloads": 5,
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


def download_audio(url: str, job_id: str, progress_dict: dict = None) -> Path:
    """Download audio from a YouTube URL and convert to MP3."""
    job_dir = DOWNLOADS_DIR / job_id
    job_dir.mkdir(parents=True, exist_ok=True)

    options = {
        **get_base_options(),
        "format": "bestaudio/best",
        "postprocessors": [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": "mp3",
            "preferredquality": "192",
        }],
        "outtmpl": str(job_dir / "%(id)s.%(ext)s"),
        "restrictfilenames": True,
    }

    if progress_dict is not None:
        options["progress_hooks"] = [create_progress_hook(job_id, progress_dict)]

    with yt_dlp.YoutubeDL(options) as ydl:
        info = ydl.extract_info(url, download=True)
        original_filename = ydl.prepare_filename(info)
        filepath = Path(original_filename).with_suffix(".mp3")
        return filepath


def download_audio_by_id(video_id: str, job_id: str, progress_dict: dict = None) -> Path:
    """Download audio by YouTube video ID."""
    url = video_id_to_url(video_id)
    return download_audio(url, job_id, progress_dict)
