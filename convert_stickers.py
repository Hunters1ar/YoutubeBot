import os
import subprocess
from pathlib import Path
import static_ffmpeg

static_ffmpeg.add_paths()

videos = ["downloading.mp4", "hello.mp4"]

for video in videos:
    in_path = Path(video)
    if not in_path.exists():
        continue
        
    out_path = in_path.with_suffix('.webm')
    print(f"Processing {video} -> {out_path.name}")
    
    # FFmpeg command for Telegram Video Stickers:
    # -t 2.9 (Max 3 seconds)
    # -an (Remove audio)
    # -c:v libvpx-vp9 (VP9 codec)
    # -vf scale (One side 512, other side even and <= 512)
    # -r 30 (Max 30 fps)
    # -b:v 300k (Keep file size under 256KB)
    # -pix_fmt yuva420p (Alpha channel support, good for WebM)
    
    cmd = [
        "ffmpeg", "-y", 
        "-i", str(in_path),
        "-t", "2.9",
        "-an",
        "-c:v", "libvpx-vp9",
        "-vf", "scale='if(gt(iw,ih),512,-2)':'if(gt(iw,ih),-2,512)'",
        "-r", "30",
        "-b:v", "250k",
        "-maxrate", "250k",
        "-bufsize", "500k",
        "-pix_fmt", "yuva420p",
        str(out_path)
    ]
    
    subprocess.run(cmd)
    
    # Check output size
    if out_path.exists():
        size_kb = out_path.stat().st_size / 1024
        print(f"Done! Size: {size_kb:.1f} KB (Must be < 256 KB)")
    
