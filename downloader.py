import static_ffmpeg
static_ffmpeg.add_paths()

import yt_dlp
from pathlib import Path

DOWNLOADS_DIR = Path("downloads")
DOWNLOADS_DIR.mkdir(exist_ok=True)

import os

def get_base_options() -> dict:
    options = {}
    
    # Bypass "The page needs to be reloaded" bot detection
    from yt_dlp.networking.impersonate import ImpersonateTarget
    options["impersonate"] = ImpersonateTarget.from_str("chrome")
    
    # Avoid clients that frequently cause UNPLAYABLE or PO Token errors
    options["extractor_args"] = {
        "youtube": ["player_client=default,-tv,-web_safari,-web_embedded,-mweb"]
    }
    
    # Enable NodeJS for solving YouTube's JS challenges (Deno is default)
    options["js_runtimes"] = "node"




    
    browser = os.getenv("COOKIES_BROWSER", "edge")
    if browser.lower() != "none":
        options["cookiesfrombrowser"] = (browser,)
    cookies_file = os.getenv("COOKIES_FILE")
    if cookies_file:
        options["cookiefile"] = cookies_file
    user_agent = os.getenv("USER_AGENT")
    if user_agent:
        options["http_headers"] = {"User-Agent": user_agent}
    return options

def get_video_info(url: str) -> dict:
    options = {
        **get_base_options(),
        "quiet": True,
        "noplaylist": True,
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

import re

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
                # e.g., "45.0%" -> 45.0
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
        "noplaylist": True,
        "quiet": True,
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
        "noplaylist": True,
        "quiet": True,
    }
    
    if progress_dict is not None:
        options["progress_hooks"] = [create_progress_hook(job_id, progress_dict)]
    
    with yt_dlp.YoutubeDL(options) as ydl:
        info = ydl.extract_info(url, download=True)
        original_filename = ydl.prepare_filename(info)
        filepath = Path(original_filename).with_suffix(".mp3")
        return filepath

def download_subtitles(url: str, job_id: str) -> Path:
    job_dir = DOWNLOADS_DIR / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    
    options = {
        **get_base_options(),
        "skip_download": True,
        "writesubtitles": True,
        "writeautomaticsub": True,
        "subtitleslangs": ["en", "ru"], # Prefers English and Russian
        "subtitlesformat": "srt/vtt/best",
        "outtmpl": str(job_dir / "%(title)s.%(ext)s"),
        "quiet": True,
    }
    
    with yt_dlp.YoutubeDL(options) as ydl:
        ydl.extract_info(url, download=True)
        
        # Find any subtitle file downloaded
        for file in job_dir.iterdir():
            if file.suffix in [".srt", ".vtt", ".vtt"]:
                return file
    return None
