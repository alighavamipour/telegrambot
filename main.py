import os, logging, time, re
import telebot
from telebot import types
from config import BOT_TOKEN, CHANNEL_ID, OWNER_ID, REQUIRED_CHANNELS, DOWNLOAD_PATH, DB_PATH
import database, utils

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN environment variable is required")

bot = telebot.TeleBot(BOT_TOKEN, parse_mode=None)

# init DB and folders
database.init_db()
os.makedirs(DOWNLOAD_PATH, exist_ok=True)

# decorator: require membership and prevent forwarded usage where necessary
from functools import wraps
def require_membership(func):
    @wraps(func)
    def wrapper(message, *args, **kwargs):
        uid = message.from_user.id
        # If user forwarded file (we accept but prefer direct), still allow — but warn if forwarded from a channel user doesn't own
        # Check membership
        if not utils.check_membership(bot, uid):
            kb = types.InlineKeyboardMarkup()
            kb.add(types.InlineKeyboardButton("عضویت در کانال", url=utils.make_channel_caption(CHANNEL_ID)))
            bot.reply_to(message, "برای استفاده از ربات باید عضو کانال شوید.", reply_markup=kb)
            return
        return func(message, *args, **kwargs)
    return wrapper

# main keyboard
def main_keyboard():
    kb = types.ReplyKeyboardMarkup(resize_keyboard=True)
    kb.row("🎵 آخرین آهنگ‌ها", "🎬 آخرین فیلم‌ها")
    kb.row("📥 دانلود از SoundCloud", "✍ ارسال برای انتشار")
    kb.row("📢 درخواست تبلیغات", "آمار")
    return kb

@bot.message_handler(commands=['start','menu'])
def cmd_start(m):
    user = m.from_user
    database.add_or_update_user(user.id, user.first_name or "", user.last_name or "", getattr(user, 'username', '') or "")
    bot.send_message(m.chat.id, "سلام! من ربات مدیریت مدیا هستم. از منو استفاده کن.", reply_markup=main_keyboard())

# -------- receive audio/video/document ----------
@bot.message_handler(content_types=['audio','video','document'])
@require_membership
def media_handler(message):
    user = message.from_user
    uid = user.id
    database.add_or_update_user(uid, user.first_name or "", user.last_name or "", getattr(user, 'username', '') or "")
    vip = database.is_vip(uid) or (uid == OWNER_ID)

    # identify file info and local save name
    if message.content_type == 'audio':
        file_id = message.audio.file_id
        file_name = message.audio.title or f"audio_{int(time.time())}.mp3"
        media_type = 'audio'
    elif message.content_type == 'video':
        file_id = message.video.file_id
        file_name = message.video.file_name or f"video_{int(time.time())}.mp4"
        media_type = 'video'
    else:
        file_id = message.document.file_id
        file_name = message.document.file_name or f"file_{int(time.time())}"
        media_type = 'document'

    # download file content locally
    try:
        finfo = bot.get_file(file_id)
        data = bot.download_file(finfo.file_path)
        safe_name = re.sub(r'[^A-Za-z0-9\\.\\-_\\u0600-\\u06FF ]', '_', file_name)  # slight sanitize, allow persian
        local_path = os.path.join(DOWNLOAD_PATH, safe_name)
        with open(local_path, 'wb') as f:
            f.write(data)
    except Exception as e:
        logger.exception("download error: %s", e)
        bot.reply_to(message, "خطا در دریافت فایل. دوباره تلاش کنید.")
        return

    # clean caption and prepare final caption (channel link + owner id)
    channel_link = utils.make_channel_caption(CHANNEL_ID)
    caption = f"{channel_link}\nID: {CHANNEL_ID}"

    uploader_name = utils.user_display_name(user)

    # write ID3 tag if mp3 and channel id requested
    if media_type == 'audio' and local_path.lower().endswith('.mp3'):
        try:
            utils.write_id3_channel_tag(local_path, CHANNEL_ID)
        except Exception as e:
            logger.exception("ID3 tagging failed: %s", e)

    # save post record (local_path saved)
    pid = database.add_post(local_path, file_id, file_name, media_type, "", uploader_name, uid)

    if vip:
        # owner/vip: post automatically to channel
        try:
            if media_type == 'audio':
                with open(local_path, 'rb') as fh:
                    sent = bot.send_audio(CHANNEL_ID, fh, caption=caption)
            elif media_type == 'video':
                with open(local_path, 'rb') as fh:
                    sent = bot.send_video(CHANNEL_ID, fh, caption=caption)
            else:
                with open(local_path, 'rb') as fh:
                    sent = bot.send_document(CHANNEL_ID, fh, caption=caption)
            database.mark_posted(pid, getattr(sent, 'message_id', None))
            bot.reply_to(message, "✅ فایل شما با موفقیت در کانال منتشر شد.")
        except Exception as e:
            logger.exception("post to channel error: %s", e)
            bot.reply_to(message, f"خطا در ارسال به کانال: {e}")
    else:
        # regular user: ask for display name attribution flow (we'll request the name and then send to owner for approval)
        msg = bot.reply_to(message, "آیا می‌خواهید این پست با نام خودتان در کانال منتشر شود؟ لطفاً نام نمایشی که می‌خواهید نمایش داده شود را ارسال کنید (مثال: علی رضایی). اگر نمی‌خواهید، بنویسید 'انصراف'.")
        # store awaiting state by using register_next_step_handler
        def ask_name_handler(reply):
            name = (reply.text or "").strip()
            if not name or name.lower() == 'انصراف':
                bot.send_message(reply.chat.id, "درخواست شما ثبت شد. فایل برای بررسی به ادمین ارسال می‌شود.")
                # send to owner for review
                try:
                    bot.send_message(OWNER_ID, f"فایل جدید از {uploader_name} برای بررسی (نام ارائه نشده).")
                    bot.send_document(OWNER_ID, open(local_path, 'rb'), caption=caption)
                except Exception as e:
                    logger.exception("notify owner error: %s", e)
                    bot.send_message(reply.chat.id, "خطا در ارسال فایل برای ادمین.")
                return
            # save chosen display name into DB by updating post title
            conn = database.get_conn()
            cur = conn.cursor()
            cur.execute("UPDATE posts SET title=? WHERE id=?", (name, pid))
            conn.commit(); conn.close()
            # notify owner with approve buttons and preview
            kb = types.InlineKeyboardMarkup()
            kb.add(types.InlineKeyboardButton("✅ تایید و پست", callback_data=f"approve_post|{pid}"),
                   types.InlineKeyboardButton("❌ رد", callback_data=f"reject_post|{pid}"))
            bot.send_message(OWNER_ID, f"درخواست انتشار از {uploader_name} — درخواست نام منتشرشده: {name}\nبرای مشاهده/تصمیم فایل، لطفا اقدام کنید.")
            bot.send_document(OWNER_ID, open(local_path, 'rb'), caption=f"پیشنمایش: {caption}\\nارسال‌کننده: {name}")
            bot.send_message(reply.chat.id, "درخواست شما ثبت شد. بعد از تایید مدیر منتشر خواهد شد.")
        bot.register_next_step_handler(msg, ask_name_handler)

# -------- SoundCloud / external link handler ----------
@bot.message_handler(func=lambda m: isinstance(m.text, str) and 'soundcloud.com' in m.text.lower())
@require_membership
def sc_handler(message):
    user = message.from_user
    uid = user.id
    database.add_or_update_user(uid, user.first_name or "", user.last_name or "", getattr(user, 'username', '') or "")
    text = message.text.strip()
    urls = re.findall(r'(https?://\\S+)', text)
    url = urls[0] if urls else text

    bot.reply_to(message, "⏳ در حال دانلود از SoundCloud (یا منبع مورد نظر)... لطفاً صبر کنید.")

    try:
        local_path, info = utils.download_with_ytdlp(url, outdir=DOWNLOAD_PATH, filename_prefix=f"{uid}_sc")
        # ensure mp3 tag
        if local_path.lower().endswith('.mp3'):
            try:
                utils.write_id3_channel_tag(local_path, CHANNEL_ID)
            except:
                pass

        # If owner: post directly
        if uid == OWNER_ID or database.is_vip(uid):
            # post directly
            with open(local_path, 'rb') as fh:
                sent = bot.send_audio(CHANNEL_ID, fh, caption=f"{utils.make_channel_caption(CHANNEL_ID)}\\nID: {CHANNEL_ID}")
            bot.reply_to(message, "✅ فایل از SoundCloud دانلود و در کانال منتشر شد.")
            database.add_post(local_path, None, os.path.basename(local_path), 'soundcloud', info.get('title',''), utils.user_display_name(user), uid)
            database.mark_posted(database.latest_posts(1)[0][0] if database.latest_posts(1) else None, getattr(sent, 'message_id', None))
        else:
            # send a copy to owner for review (PV)
            bot.send_message(OWNER_ID, f"📥 کاربر {utils.user_display_name(user)} لینک SoundCloud فرستاده — نسخه برای بررسی:")
            bot.send_document(OWNER_ID, open(local_path, 'rb'))
            pid = database.add_post(local_path, None, os.path.basename(local_path), 'soundcloud', info.get('title',''), utils.user_display_name(user), uid)
            bot.reply_to(message, "نسخه دانلود شده برای بررسی به ادمین ارسال شد. پس از تایید منتشر خواهد شد.")
    except Exception as e:
        logger.exception("soundcloud error: %s", e)
        bot.reply_to(message, f"خطا در دانلود SoundCloud: {e}")

# -------- submit text for approval ----------
@bot.message_handler(func=lambda m: isinstance(m.text, str) and m.text.startswith('ارسال:'))
@require_membership
def submit_text(m):
    user = m.from_user
    text = m.text[len('ارسال:'):].strip()
    if not text:
        bot.reply_to(m, "متن خالی است.")
        return
    pid = database.add_pending_text(user.id, utils.user_display_name(user), text)
    kb = types.InlineKeyboardMarkup()
    kb.add(types.InlineKeyboardButton('✅ تأیید', callback_data=f"approve_text|{pid}"),
           types.InlineKeyboardButton('❌ رد', callback_data=f"reject_text|{pid}"))
    bot.send_message(OWNER_ID, f"درخواست انتشار از {utils.user_display_name(user)}:\n\n{text}", reply_markup=kb)
    bot.reply_to(m, "پیام شما برای بررسی ارسال شد. پس از تایید منتشر خواهد شد.")

# -------- requests for ads ----------
@bot.message_handler(func=lambda m: isinstance(m.text, str) and m.text == '📢 درخواست تبلیغات')
@require_membership
def ad_request_start(m):
    bot.reply_to(m, "لطفاً متن درخواست تبلیغات خود را بفرستید. پس از ارسال، ادمین آن را بررسی می‌کند.")
    bot.register_next_step_handler(m, handle_ad_request)

def handle_ad_request(m):
    user = m.from_user
    txt = m.text or ""
    rid = database.add_request(user.id, 'ad', txt)
    bot.send_message(OWNER_ID, f"درخواست تبلیغ جدید از {utils.user_display_name(user)}:\n\n{txt}")
    bot.reply_to(m, "درخواست شما ثبت شد. ادمین ظرف زمان بررسی می‌کند.")

# -------- admin: view pending texts ----------
@bot.message_handler(commands=['pending'])
def view_pending(m):
    if m.from_user.id != OWNER_ID:
        return
    pending = database.get_pending_texts()
    if not pending:
        bot.send_message(OWNER_ID, "هیچ پیام منتظری وجود ندارد.")
        return
    for p in pending:
        pid, uid, uname, text, created = p
        kb = types.InlineKeyboardMarkup()
        kb.add(types.InlineKeyboardButton("تأیید", callback_data=f"approve_text|{pid}"),
               types.InlineKeyboardButton("رد", callback_data=f"reject_text|{pid}"))
        bot.send_message(OWNER_ID, f"{pid} — {uname}:\n\n{text}", reply_markup=kb)

# -------- callbacks (approve/reject posts/texts, download buttons) ----------
@bot.callback_query_handler(func=lambda c: True)
def cb_handler(call):
    data = call.data or ""
    try:
        if data.startswith('approve_text|'):
            pid = int(data.split('|')[1])
            # fetch pending text
            conn = database.get_conn(); cur = conn.cursor()
            cur.execute("SELECT user_name, text FROM pending_texts WHERE id=?", (pid,))
            row = cur.fetchone(); conn.close()
            if not row:
                bot.answer_callback_query(call.id, "یافت نشد")
                return
            uname, text = row
            bot.send_message(CHANNEL_ID, f"{text}\n\nارسال شده توسط: {uname}\n{utils.make_channel_caption(CHANNEL_ID)}\nID: {CHANNEL_ID}")
            database.set_pending_status(pid, 'approved')
            bot.answer_callback_query(call.id, "تأیید و منتشر شد")
        elif data.startswith('reject_text|'):
            pid = int(data.split('|')[1])
            database.set_pending_status(pid, 'rejected')
            bot.answer_callback_query(call.id, "رد شد")
        elif data.startswith('approve_post|'):
            pid = int(data.split('|')[1])
            post = database.get_post(pid)
            if not post:
                bot.answer_callback_query(call.id, "پست یافت نشد")
                return
            _, local_path, tg_file_id, file_name, media_type, title, uploader_name = post[0], post[1], post[2], post[3], post[4], post[5], post[6]
            # fetch title saved in DB
            # post to channel using local_path
            try:
                caption = f"{utils.make_channel_caption(CHANNEL_ID)}\\nارسال شده توسط: {title or uploader_name}\\nID: {CHANNEL_ID}"
                if media_type == 'audio':
                    with open(local_path, 'rb') as fh:
                        sent = bot.send_audio(CHANNEL_ID, fh, caption=caption)
                elif media_type == 'video':
                    with open(local_path, 'rb') as fh:
                        sent = bot.send_video(CHANNEL_ID, fh, caption=caption)
                else:
                    with open(local_path, 'rb') as fh:
                        sent = bot.send_document(CHANNEL_ID, fh, caption=caption)
                database.mark_posted(pid, getattr(sent, 'message_id', None))
                bot.answer_callback_query(call.id, "پست تایید و منتشر شد")
            except Exception as e:
                logger.exception("approve_post error: %s", e)
                bot.answer_callback_query(call.id, "خطا در انتشار")
        elif data.startswith('reject_post|'):
            pid = int(data.split('|')[1])
            # optionally mark rejected (not implemented here)
            bot.answer_callback_query(call.id, "پست رد شد")
        elif data.startswith('dl_post|'):
            pid = int(data.split('|')[1])
            post = database.get_post(pid)
            if not post:
                bot.answer_callback_query(call.id, "پست یافت نشد")
                return
            # post structure: (id, local_path, tg_file_id, file_name, media_type, title, uploader_name)
            _, local_path, tg_file_id, file_name, media_type, title, uploader_name = post
            try:
                if local_path and os.path.exists(local_path):
                    if media_type == 'audio':
                        with open(local_path, 'rb') as fh:
                            bot.send_audio(call.from_user.id, fh)
                    else:
                        with open(local_path, 'rb') as fh:
                            bot.send_document(call.from_user.id, fh)
                    bot.answer_callback_query(call.id, "فایل برای شما ارسال شد")
                else:
                    # fallback: if tg_file_id exists, send via id
                    if tg_file_id:
                        if media_type == 'audio':
                            bot.send_audio(call.from_user.id, tg_file_id)
                        else:
                            bot.send_document(call.from_user.id, tg_file_id)
                        bot.answer_callback_query(call.id, "فایل برای شما ارسال شد")
                    else:
                        bot.answer_callback_query(call.id, "فایل موجود نیست")
            except Exception as e:
                logger.exception("dl_post error: %s", e)
                bot.answer_callback_query(call.id, "خطا در ارسال فایل")
    except Exception as e:
        logger.exception("callback handler error: %s", e)
        try:
            bot.answer_callback_query(call.id, "خطایی پیش آمد")
        except:
            pass

# -------- list latest songs/videos ----------
@bot.message_handler(func=lambda m: m.text == '🎵 آخرین آهنگ‌ها')
@require_membership
def list_latest_songs(m):
    rows = database.latest_posts(10, media_type='audio')
    if not rows:
        bot.reply_to(m, "هنوز آهنگی موجود نیست.")
        return
    for r in rows:
        pid, local_path, tg_file_id, file_name, media_type, title, uploader_name, created = r
        kb = types.InlineKeyboardMarkup()
        kb.add(types.InlineKeyboardButton("⬇️ دانلود", callback_data=f"dl_post|{pid}"))
        try:
            # try to send preview audio by telegram file_id if exists, otherwise send title+button
            if tg_file_id:
                bot.send_audio(m.chat.id, tg_file_id, caption=f"{title}\\nارسال‌شده توسط: {uploader_name}\\nID: {CHANNEL_ID}", reply_markup=kb)
            else:
                bot.send_message(m.chat.id, f"{title or file_name} — ارسال‌شده توسط: {uploader_name}\\nID: {CHANNEL_ID}", reply_markup=kb)
        except Exception:
            bot.send_message(m.chat.id, f"{title or file_name} — ارسال‌شده توسط: {uploader_name}\\nID: {CHANNEL_ID}", reply_markup=kb)

@bot.message_handler(func=lambda m: m.text == '🎬 آخرین فیلم‌ها')
@require_membership
def list_latest_videos(m):
    rows = database.latest_posts(10, media_type='video')
    if not rows:
        bot.reply_to(m, "هیچ ویدیویی موجود نیست.")
        return
    for r in rows:
        pid, local_path, tg_file_id, file_name, media_type, title, uploader_name, created = r
        kb = types.InlineKeyboardMarkup()
        kb.add(types.InlineKeyboardButton("⬇️ دانلود", callback_data=f"dl_post|{pid}"))
        try:
            if tg_file_id:
                bot.send_document(m.chat.id, tg_file_id, caption=f"{title}\\nارسال‌شده توسط: {uploader_name}\\nID: {CHANNEL_ID}", reply_markup=kb)
            else:
                bot.send_message(m.chat.id, f"{title or file_name} — ارسال‌شده توسط: {uploader_name}\\nID: {CHANNEL_ID}", reply_markup=kb)
        except Exception:
            bot.send_message(m.chat.id, f"{title or file_name} — ارسال‌شده توسط: {uploader_name}\\nID: {CHANNEL_ID}", reply_markup=kb)

# -------- stats ----------
@bot.message_handler(func=lambda m: m.text == 'آمار')
def stats_cmd(m):
    if m.from_user.id != OWNER_ID:
        bot.reply_to(m, "فقط برای مدیر")
        return
    rows = database.latest_posts(50)
    bot.send_message(OWNER_ID, f"تعداد پست‌ها: {len(rows)}")

# -------- safe startup (remove webhook to avoid 409, then polling) ----------
if __name__ == '__main__':
    try:
        try:
            bot.remove_webhook()
        except Exception:
            pass
        logger.info("Webhook removed (if any). Starting polling...")
        bot.infinity_polling(timeout=60, long_polling_timeout=60)
    except Exception as e:
        logger.exception("Fatal bot error: %s", e)
        raise
