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

def get_base_options() -> dict:
    options = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "concurrent_fragment_downloads": 5,  # Download video chunks in parallel (3x - 5x faster)
        "buffersize": 1024 * 64,             # 64KB read buffer
        "http_chunk_size": 10485760,         # 10MB chunk size for high-speed streaming
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


def get_video_info(url: str) -> dict:
    options = {
        **get_base_options(),
        "extract_flat": False,
    }
    with yt_dlp.YoutubeDL(options) as ydl:
        info = ydl.extract_info(url, download=False)
        
    title = info.get("title", "Unknown Title")
    channel = info.get("uploader", "Unknown Channel")
    thumbnail = info.get("thumbnail")
    
    formats_by_res = {}
    best_audio_size = 0
    
    # Estimate audio size
    for f in info.get("formats", []):
        if f.get("vcodec") == "none" and f.get("acodec") != "none":
            size = f.get("filesize") or f.get("filesize_approx") or 0
            if size > best_audio_size:
                best_audio_size = size
                
    # Estimate video size
    for f in info.get("formats", []):
        h = f.get("height")
        vcodec = f.get("vcodec")
        if not h or vcodec == "none":
            continue
            
        size = f.get("filesize") or f.get("filesize_approx") or 0
        if f.get("acodec") == "none":
            size += best_audio_size
            
        if size > 0:
            if h not in formats_by_res or formats_by_res[h] < size:
                formats_by_res[h] = size
                
    resolutions = []
    for h in sorted(formats_by_res.keys()):
        if h in [144, 240, 360, 480, 720, 1080]:
            size_mb = formats_by_res[h] / (1024 * 1024)
            resolutions.append({"height": h, "size_mb": size_mb})
            
    # Default fallback if no sizes could be determined
    if not resolutions:
        resolutions = [
            {"height": 360, "size_mb": 0},
            {"height": 720, "size_mb": 0},
        ]
            
    return {
        "title": title,
        "channel": channel,
        "thumbnail": thumbnail,
        "resolutions": resolutions,
        "audio_size_mb": best_audio_size / (1024 * 1024) if best_audio_size else 0
    }

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

def download_video(url: str, job_id: str, resolution: int = None, progress_dict: dict = None) -> Path:
    job_dir = DOWNLOADS_DIR / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    
    if resolution:
        format_str = f"bestvideo[height<={resolution}][ext=mp4]+bestaudio[ext=m4a]/bestvideo[height<={resolution}]+bestaudio/best[height<={resolution}]/best"
    else:
        format_str = "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best"
        
    options = {
        **get_base_options(),
        "format": format_str,
        "outtmpl": str(job_dir / "%(id)s.%(ext)s"),
        "restrictfilenames": True,
        "merge_output_format": "mp4",
    }
    
    if progress_dict is not None:
        options["progress_hooks"] = [create_progress_hook(job_id, progress_dict)]
    
    with yt_dlp.YoutubeDL(options) as ydl:
        info = ydl.extract_info(url, download=True)
        original = ydl.prepare_filename(info)
        merged = Path(original).with_suffix(".mp4")
        if merged.exists():
            return merged
        return Path(original)

def download_audio(url: str, job_id: str, progress_dict: dict = None) -> Path:
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



