import re
import urllib.request
import urllib.parse
import json
from pathlib import Path

DOWNLOADS_DIR = Path("downloads")
DOWNLOADS_DIR.mkdir(exist_ok=True)

def extract_video_id(url: str) -> str | None:
    patterns = [
        r'(?:v=|\/)([0-9A-Za-z_-]{11}).*',
        r'(?:youtu\.be\/)([0-9A-Za-z_-]{11})',
        r'(?:embed\/)([0-9A-Za-z_-]{11})',
        r'(?:shorts\/)([0-9A-Za-z_-]{11})'
    ]
    for p in patterns:
        m = re.search(p, url)
        if m:
            return m.group(1)
    return None

def get_video_info(url: str) -> dict:
    video_id = extract_video_id(url)
    if not video_id:
        raise ValueError("Invalid YouTube URL: Could not extract Video ID.")
        
    title = "YouTube Video"
    channel = "YouTube Channel"
    thumbnail = f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg"
    
    # Try fetching metadata via official oEmbed API (instant, never blocked)
    try:
        oembed_url = f"https://www.youtube.com/oembed?url=https://www.youtube.com/watch?v={video_id}&format=json"
        req = urllib.request.Request(oembed_url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=5) as response:
            data = json.loads(response.read().decode("utf-8"))
            title = data.get("title", title)
            channel = data.get("author_name", channel)
            if data.get("thumbnail_url"):
                thumbnail = data.get("thumbnail_url")
    except Exception:
        # Fallback to noembed
        try:
            noembed_url = f"https://noembed.com/embed?url=https://www.youtube.com/watch?v={video_id}"
            req = urllib.request.Request(noembed_url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=5) as response:
                data = json.loads(response.read().decode("utf-8"))
                if data.get("title"):
                    title = data.get("title")
                if data.get("author_name"):
                    channel = data.get("author_name")
        except Exception:
            pass

    return {
        "video_id": video_id,
        "title": title,
        "channel": channel,
        "thumbnail": thumbnail,
        "mp3_url": f"https://apisyu.com/single/mp3/{video_id}?theme=dark",
        "mp4_url": f"https://apisyu.com/single/mp4/{video_id}?theme=dark",
        "widget_url": f"https://apisyu.com/widget/{video_id}?theme=dark",
        "button_mp3_url": f"https://apisyu.com/button/mp3/{video_id}?theme=dark",
        "button_mp4_url": f"https://apisyu.com/button/mp4/{video_id}?theme=dark"
    }

