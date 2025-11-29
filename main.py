# main.py
import os
import re
import asyncio
import logging
import uuid
import shutil
from pathlib import Path
from dotenv import load_dotenv

from pyrogram import Client, filters
from pyrogram.types import Message
from yt_dlp import YoutubeDL
from mutagen.easyid3 import EasyID3
from mutagen.mp4 import MP4
from mutagen.oggvorbis import OggVorbis
from mutagen import File as MutagenFile

load_dotenv()
# -------- FIX SYNC TIME (Required for Render) --------
import time
time.sleep(1)
now = time.time()
print("Time synced:", now)
# ------------------------------------------------------
# ---------------- CONFIG ----------------
API_ID = int(os.environ.get("API_ID", "0"))
API_HASH = os.environ.get("API_HASH", "")
SESSION_STRING = os.environ.get("SESSION_STRING", "")  # required
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")            # optional for welcome bot
TARGET_CHANNEL = os.environ.get("TARGET_CHANNEL", "")  # e.g. @MyChannel or -1001234567890
WORKDIR = Path(os.environ.get("WORKDIR", "/tmp/music_forwarder"))
WORKDIR.mkdir(parents=True, exist_ok=True)
PORT = int(os.environ.get("PORT", "5000"))  # Render gives $PORT
# ----------------------------------------

if not SESSION_STRING:
    raise SystemExit("SESSION_STRING env is required. Create it locally (see README)")

# logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("music-forwarder")

# yt-dlp options (robust)
YDL_OPTS = {
    "format": "bestaudio/best",
    "outtmpl": str(WORKDIR / "%(id)s.%(ext)s"),
    "noplaylist": True,
    "quiet": True,
    "no_warnings": True,
    "retries": 3,
    "continuedl": True,
}

# URL regex
RE_SOUNDCLOUD = re.compile(r'https?://(soundcloud\.com|snd\.sc)/\S+', re.I)
RE_SPOTIFY = re.compile(r'(https?://open\.spotify\.com/track/\S+|spotify:track:\S+)', re.I)

# Pyrogram client (user session) - will do sniffing + posting
app = Client(
    name="user_session",
    session_string=SESSION_STRING,
    api_id=API_ID,
    api_hash=API_HASH,
    workdir="/tmp/pyro",
    plugins=dict(root="plugins")  # not used but reserved
)

# optionally a bot client for DMs (welcome/help). We'll create a simple one if BOT_TOKEN is present
bot = None
if BOT_TOKEN:
    bot = Client("bot_session", bot_token=BOT_TOKEN)

# -------- helpers --------
def cleanup_dir():
    import time
    now = time.time()
    for f in WORKDIR.iterdir():
        try:
            if f.is_file():
                if f.stat().st_mtime + 24*3600 < now:
                    f.unlink()
            elif f.is_dir():
                shutil.rmtree(f)
        except Exception:
            pass


def tag_audio_file(path: Path, artist: str, title: str, channel_tag: str):
    ext = path.suffix.lower()
    try:
        if ext == ".mp3":
            try:
                audio = EasyID3(path)
            except Exception:
                from mutagen.mp3 import MP3
                mp = MP3(path)
                mp.add_tags()
                audio = EasyID3(path)
            if artist:
                audio['artist'] = artist
            if title:
                audio['title'] = title
            audio['comment'] = f"From: {channel_tag}"
            audio.save()
        elif ext in (".m4a", ".mp4", ".aac"):
            audio = MP4(str(path))
            if artist: audio["\xa9ART"] = [artist]
            if title: audio["\xa9nam"] = [title]
            audio["desc"] = [f"From: {channel_tag}"]
            audio.save()
        elif ext in (".ogg", ".opus", ".oga"):
            audio = OggVorbis(str(path))
            if artist: audio["artist"] = [artist]
            if title: audio["title"] = [title]
            audio["comment"] = [f"From: {channel_tag}"]
            audio.save()
        else:
            fa = MutagenFile(str(path), easy=True)
            if fa is not None:
                try:
                    fa["comment"] = [f"From: {channel_tag}"]
                    if artist: fa["artist"] = [artist]
                    if title: fa["title"] = [title]
                    fa.save()
                except Exception:
                    logger.exception("Could not generic-tag file")
    except Exception:
        logger.exception("tag_audio_file failed for %s", path)

def extract_artist_title_from_text(text: str, filename: str):
    artist = ""
    title = ""
    if text:
        m = re.match(r'(.+?)\s*[-–—]\s*(.+)', text)
        if m:
            artist, title = m.group(1).strip(), m.group(2).strip()
            return artist, title
        # fallback: whole text as title
        title = text.strip()
        return artist, title
    # fallback: filename
    name = Path(filename).stem
    m = re.match(r'(.+?)\s*[-–—]\s*(.+)', name)
    if m:
        return m.group(1).strip(), m.group(2).strip()
    return "", name

async def download_via_yt_dlp(url: str):
    loop = asyncio.get_event_loop()
    def _sync_download(u):
        with YoutubeDL(YDL_OPTS) as ydl:
            info = ydl.extract_info(u, download=True)
            fn = ydl.prepare_filename(info)
            return fn, info
    return await loop.run_in_executor(None, _sync_download, url)

async def send_to_target(path: Path, artist: str, title: str):
    caption = ""
    if artist and title:
        caption = f"{artist} — {title}\n\nکانال: {TARGET_CHANNEL}"
    elif title:
        caption = f"{title}\n\nکانال: {TARGET_CHANNEL}"
    else:
        caption = f"کانال: {TARGET_CHANNEL}"
    # prefer send_audio to keep audio meta
    try:
        await app.send_audio(chat_id=TARGET_CHANNEL, audio=str(path), caption=caption)
    except Exception as e:
        logger.warning("send_audio failed (%s), trying send_document", e)
        await app.send_document(chat_id=TARGET_CHANNEL, document=str(path), caption=caption)

# -------- Handlers --------

# 1) Handle incoming private messages to the *user account* (someone fwd file/link to your user)
@app.on_message(filters.private & (filters.document | filters.audio | filters.voice | filters.text))
async def on_private_receive(c: Client, m: Message):
    # If link SoundCloud/Spotify -> download & post
    text = m.text or m.caption or ""
    if text:
        sc = RE_SOUNDCLOUD.search(text)
        sp = RE_SPOTIFY.search(text)
        if sc or sp:
            url = sc.group(0) if sc else sp.group(0)
            await m.reply_text("دانلود و آماده‌سازی...")
            try:
                fn, info = await download_via_yt_dlp(url)
                path = Path(fn)
                artist, title = extract_artist_title_from_text(m.caption or m.text or info.get('title',''), path.name)
                artist = artist or info.get('artist') or info.get('uploader') or ""
                title = title or info.get('title') or ""
                tag_audio_file(path, artist, title, TARGET_CHANNEL)
                await send_to_target(path, artist, title)
                await m.reply_text("فایل پست شد در کانال.")
                try: path.unlink()
                except: pass
            except Exception as e:
                logger.exception("yt-dlp failed")
                await m.reply_text(f"خطا در دانلود: {e}")
            return

    # If user forwarded audio/document/voice
    if m.audio or m.document or m.voice:
        await m.reply_text("دریافت و آماده‌سازی برای پست...")
        try:
            f = m.audio or m.document or m.voice
            file_path = await m.download(file_name=str(WORKDIR / (f.file_unique_id + "_" + (f.file_name or "file"))))
            artist, title = extract_artist_title_from_text(m.caption or m.text or "", file_path)
            tag_audio_file(Path(file_path), artist, title, TARGET_CHANNEL)
            await send_to_target(Path(file_path), artist, title)
            await m.reply_text("فایل با کپشن جدید در کانال منتشر شد.")
            try: Path(file_path).unlink()
            except: pass
        except Exception:
            logger.exception("Failed process incoming media")
            await m.reply_text("خطا در پردازش فایل.")
        return

# 2) Sniff/monitor source channels & groups (message handler for channels that user is member of)
#    This will automatically get channel_post events because our client is a user and is member.
@app.on_message(filters.channel)
async def on_channel_post(c: Client, m: Message):
    # ignore posts from our own target channel to avoid loops
    chat = m.chat
    if not chat:
        return
    try:
        # Avoid reposting if origin is target channel
        if str(chat.id) == str(TARGET_CHANNEL) or (chat.username and chat.username.lower() == str(TARGET_CHANNEL).lstrip("@").lower()):
            return

        # Cases:
        # - If post contains a link to SoundCloud/Spotify -> download and post (case 1)
        text = m.text or m.caption or ""
        sc = RE_SOUNDCLOUD.search(text) if text else None
        sp = RE_SPOTIFY.search(text) if text else None
        if sc or sp:
            url = sc.group(0) if sc else sp.group(0)
            logger.info("Found music link in channel %s: %s", chat.username or chat.title, url)
            try:
                fn, info = await download_via_yt_dlp(url)
                path = Path(fn)
                artist, title = extract_artist_title_from_text(m.caption or m.text or info.get('title',''), path.name)
                artist = artist or info.get('artist') or info.get('uploader') or ""
                title = title or info.get('title') or ""
                # tag and post
                tag_audio_file(path, artist, title, TARGET_CHANNEL)
                await send_to_target(path, artist, title)
                try: path.unlink()
                except: pass
            except Exception:
                logger.exception("Failed to download/link from channel post")
            return

        # - If post contains media (audio/document/voice/video with audio) -> download, retag, post
        if m.audio or m.document or m.voice or m.video:
            logger.info("Channel %s posted media; reposting to %s", chat.username or chat.title, TARGET_CHANNEL)
            try:
                media = m.audio or m.document or m.voice or m.video
                file_path = await m.download(file_name=str(WORKDIR / (media.file_unique_id + "_" + (media.file_name or "file"))))
                artist, title = extract_artist_title_from_text(m.caption or m.text or "", file_path)
                tag_audio_file(Path(file_path), artist, title, TARGET_CHANNEL)
                await send_to_target(Path(file_path), artist, title)
                try: Path(file_path).unlink()
                except: pass
            except Exception:
                logger.exception("Failed to download media from channel post")
            return

        # else ignore (text-only posts)
    except Exception:
        logger.exception("on_channel_post general error")

# 3) (Optional) a very small bot for welcome & help — respond when someone messages the bot (BOT_TOKEN must be set)
if bot:
    @bot.on_message(filters.private & filters.command("start"))
    async def bot_start(_, m: Message):
        txt = (
            "سلام 👋\n"
            "خوش اومدی به ربات رسمی کانال!\n\n"
            "راهنما:\n"
            "• لینک SoundCloud/Spotify بفرست تا دانلود و در کانال منتشر شود.\n"
            "• فایل صوتی را فوروارد کن به ربات (در دایرکت) تا در کانال منتشر شود.\n"
            "توجه: کانال مقصد توسط حساب کاربری شما ارسال می‌شه؛ برای مانیتور کانال‌ها، اکانت شما باید عضو کانال مبدا باشد."
        )
        await m.reply_text(txt)

# ---------- run ----------
async def main():
    # start both clients
    await app.start()
    logger.info("User session started (sniffer).")
    if bot:
        await bot.start()
        logger.info("Bot session started (for welcome/help).")
    # Keep running — run until manually stopped
    # Use simple aiohttp healthcheck server so Render sees a listening port
    from aiohttp import web

    async def handle(request):
        return web.Response(text="OK")

    web_app = web.Application()
    web_app.add_routes([web.get("/", handle)])
    runner = web.AppRunner(web_app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    logger.info("Health server started on port %s", PORT)

    # sleep forever
    try:
        while True:
            await asyncio.sleep(3600)
    finally:
        await app.stop()
        if bot:
            await bot.stop()
        await runner.cleanup()

if __name__ == "__main__":
    asyncio.run(main())
