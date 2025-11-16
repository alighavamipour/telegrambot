# utils_soundcloud.py
import yt_dlp

def download_soundcloud(url):
    out = "data/sound.mp3"

    ydl_opts = {
        "format": "bestaudio/best",
        "outtmpl": out
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        ydl.download([url])

    return out
