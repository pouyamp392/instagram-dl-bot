import asyncio
import logging
import os
import re
import shutil
import tempfile
import threading
from pathlib import Path
from urllib.parse import urlparse

import httpx
import yt_dlp
from flask import Flask
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CallbackQueryHandler, CommandHandler,
    ContextTypes, MessageHandler, filters,
)

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_API_URL = os.getenv("ADMIN_API_URL", "https://admin-dl-bot.onrender.com").rstrip("/")
API_KEY = os.getenv("API_KEY", "")
GROUP_TRIGGER = "اینستا دانلودر"
BOT_USERNAME = os.getenv("BOT_USERNAME", "MewInstaBot")
BOT_LINK = f"https://t.me/{BOT_USERNAME}"
MAX_FILE_SIZE = 49 * 1024 * 1024
MAX_CONCURRENT = max(1, int(os.getenv("MAX_CONCURRENT", "2")))
DOWNLOAD_SEMAPHORE = asyncio.Semaphore(MAX_CONCURRENT)
URL_PATTERN = re.compile(r"https?://[^\s<>]+")

if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN environment variable is required")

logging.basicConfig(format="%(asctime)s - %(levelname)s - %(name)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

# Render health server
web_app = Flask(__name__)

@web_app.route("/")
def home():
    return "Instagram bot is running!", 200

@web_app.route("/health")
def health():
    return {"status": "ok"}, 200

def run_web_server():
    port = int(os.environ.get("PORT", "10000"))
    web_app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False, threaded=True)

# Instagram URL helpers
def extract_instagram_url(text: str):
    match = URL_PATTERN.search(text or "")
    if not match:
        return None
    url = match.group(0).rstrip(".,!?;:)]}\"'")
    try:
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()
        if parsed.scheme not in ("http", "https"):
            return None
        if host == "instagram.com" or host.endswith(".instagram.com"):
            return url
        if host == "instagr.am" or host.endswith(".instagr.am"):
            return url
    except ValueError:
        return None
    return None

# Central mandatory-join API
async def check_membership(user_id: int):
    """Return (allowed, missing_channels). Fail closed if API/key is unavailable."""
    if not API_KEY:
        logger.error("API_KEY is missing; blocking request for safety")
        return False, []
    try:
        async with httpx.AsyncClient(timeout=12, follow_redirects=True) as client:
            response = await client.get(
                f"{ADMIN_API_URL}/api/check", params={"user_id": user_id},
                headers={"X-API-Key": API_KEY},
            )
            response.raise_for_status()
            data = response.json()
        if data.get("enabled") is False:
            return True, []
        missing = data.get("missing") or []
        return bool(data.get("ok")) and not missing, missing
    except Exception:
        logger.exception("Central membership API request failed")
        return False, []

def channel_field(channel, *keys):
    if isinstance(channel, dict):
        for key in keys:
            value = channel.get(key)
            if value:
                return str(value)
    elif channel:
        return str(channel)
    return ""

async def membership_markup(missing):
    rows = []
    channels = missing if all(isinstance(item, dict) for item in missing) else []
    if missing and not channels:
        try:
            async with httpx.AsyncClient(timeout=12, follow_redirects=True) as client:
                response = await client.get(
                    f"{ADMIN_API_URL}/api/channels", headers={"X-API-Key": API_KEY}
                )
                response.raise_for_status()
                payload = response.json()
            all_channels = payload.get("channels", payload) if isinstance(payload, dict) else payload
            if isinstance(all_channels, list):
                wanted = {str(item).lstrip("@").lower() for item in missing}
                channels = [ch for ch in all_channels if isinstance(ch, dict) and (
                    str(ch.get("id", "")).lstrip("@").lower() in wanted
                    or str(ch.get("username", "")).lstrip("@").lower() in wanted
                    or str(ch.get("link", "")).lstrip("@").lower() in wanted
                )]
        except Exception:
            logger.exception("Could not fetch channel details")
    for channel in channels:
        link = channel_field(channel, "link", "url", "invite_link")
        title = channel_field(channel, "title", "name", "username") or "عضویت در کانال"
        if link.startswith("@"):
            link = f"https://t.me/{link[1:]}"
        elif link and not link.startswith(("https://", "http://")):
            link = f"https://t.me/{link.lstrip('/')}"
        if link.startswith(("https://", "http://")):
            rows.append([InlineKeyboardButton(f"عضویت در {title}", url=link)])
    rows.append([InlineKeyboardButton("✅ بررسی عضویت", callback_data="join:check")])
    return InlineKeyboardMarkup(rows)

async def require_membership(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user, message = update.effective_user, update.effective_message
    if not user or not message:
        return False
    allowed, missing = await check_membership(user.id)
    if allowed:
        return True
    if missing:
        text = "🔒 برای استفاده از ربات، ابتدا در کانال‌های زیر عضو شو و سپس روی «بررسی عضویت» بزن."
        markup = await membership_markup(missing)
    else:
        text = "⚠️ در حال حاضر امکان بررسی عضویت وجود ندارد. چند لحظه بعد دوباره امتحان کن."
        markup = None
    await message.reply_text(text, reply_markup=markup)
    return False

# Commands and callbacks
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message or not await require_membership(update, context):
        return
    await update.message.reply_text(
        "سلام! 👋\n\nلینک پست، ریلز یا ویدئوی عمومی اینستاگرام را بفرست تا دانلودش کنم.\n\n"
        "در گروه هم می‌توانی عبارت «اینستا دانلودر» را بنویسی."
    )

async def recheck_membership(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query:
        return
    allowed, missing = await check_membership(query.from_user.id)
    if allowed:
        await query.answer("عضویت تأیید شد ✅")
        try:
            await query.edit_message_text("✅ عضویتت تأیید شد. حالا لینک اینستاگرام را بفرست.")
        except Exception:
            pass
    else:
        await query.answer("هنوز عضویت تأیید نشده است.", show_alert=True)
        try:
            await query.edit_message_reply_markup(reply_markup=await membership_markup(missing) if missing else None)
        except Exception:
            pass

# One text handler supports private chats and groups
async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message, chat, user = update.effective_message, update.effective_chat, update.effective_user
    if not message or not message.text or not chat or not user:
        return
    text = message.text.strip()
    is_group = chat.type in ("group", "supergroup")
    trigger_used = GROUP_TRIGGER.casefold() in text.casefold()
    url = extract_instagram_url(text)

    # In groups, ignore unrelated messages; react to the trigger or an Instagram URL.
    if is_group and not trigger_used and not url:
        return
    if not await require_membership(update, context):
        return
    if not url:
        if is_group and trigger_used:
            await message.reply_text(
                "📥 برای دانلود، لینک پست یا ریلز اینستاگرام را هم بفرست.\n"
                "مثال: https://www.instagram.com/reel/..."
            )
        elif not is_group:
            await message.reply_text("لطفاً یک لینک معتبر از اینستاگرام بفرست.")
        return

    status = await message.reply_text("⏳ در حال دریافت محتوا از اینستاگرام...")
    folder = tempfile.mkdtemp(prefix="instagram_")
    try:
        async with DOWNLOAD_SEMAPHORE:
            options = {
                "outtmpl": str(Path(folder) / "%(id)s.%(ext)s"),
                "format": "best[ext=mp4]/best",
                "noplaylist": True,
                "max_filesize": MAX_FILE_SIZE,
                "socket_timeout": 30,
                "retries": 2,
                "fragment_retries": 2,
                "quiet": True,
                "no_warnings": True,
                "logger": logger,
            }
            def download():
                with yt_dlp.YoutubeDL(options) as downloader:
                    return downloader.extract_info(url, download=True)
            await asyncio.to_thread(download)
            files = [p for p in Path(folder).iterdir() if p.is_file() and not p.name.endswith((".part", ".ytdl")) and p.stat().st_size > 0]
            if not files:
                raise RuntimeError("No downloaded file was produced")
            file_path = max(files, key=lambda p: p.stat().st_size)
            if file_path.stat().st_size > MAX_FILE_SIZE:
                await status.edit_text("❌ حجم فایل از سقف ارسال ربات (حدود ۴۹ مگابایت) بیشتر است.")
                return
            ext = file_path.suffix.lower()
            with file_path.open("rb") as media:
                if ext in (".jpg", ".jpeg", ".png", ".webp"):
                    await message.reply_photo(photo=media, caption="✅ دانلود با موفقیت انجام شد")
                elif ext in (".mp4", ".mov", ".m4v", ".webm"):
                    await message.reply_video(video=media, caption="✅ دانلود با موفقیت انجام شد", supports_streaming=True, read_timeout=120, write_timeout=120, connect_timeout=30, pool_timeout=30)
                else:
                    await message.reply_document(document=media, filename=file_path.name, caption="✅ دانلود با موفقیت انجام شد", read_timeout=120, write_timeout=120, connect_timeout=30)
        try:
            await status.delete()
        except Exception:
            pass
    except Exception:
        logger.exception("Instagram download failed")
        try:
            await status.edit_text(
                "❌ دانلود انجام نشد. ممکن است پست خصوصی باشد، اینستاگرام دسترسی را محدود کرده باشد "
                "یا yt-dlp نیاز به به‌روزرسانی داشته باشد."
            )
        except Exception:
            pass
    finally:
        shutil.rmtree(folder, ignore_errors=True)

async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.warning("Bot error: %s", context.error)

def main():
    threading.Thread(target=run_web_server, daemon=True).start()
    application = Application.builder().token(BOT_TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(recheck_membership, pattern=r"^join:check$"))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    application.add_error_handler(on_error)
    logger.info("Instagram bot is starting...")
    application.run_polling()

if __name__ == "__main__":
    main()
