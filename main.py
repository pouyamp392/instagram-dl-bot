
import os
import logging
import asyncio
import threading
from pathlib import Path
from tempfile import TemporaryDirectory
from http.server import HTTPServer, BaseHTTPRequestHandler

import aiohttp
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)
from dotenv import load_dotenv
import yt_dlp

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
ADMIN_API_URL = os.getenv(
    "ADMIN_API_URL",
    "https://admin-dl-bot.onrender.com",
).strip().rstrip("/")
API_KEY = os.getenv("API_KEY", "").strip()

if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN not found")

if not API_KEY:
    raise ValueError("API_KEY not found")

if not ADMIN_API_URL:
    raise ValueError("ADMIN_API_URL not found")

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)

logger = logging.getLogger(__name__)


# ==================== سرور سلامت Render ====================

class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/plain")
        self.end_headers()
        self.wfile.write(b"Bot is alive")

    def log_message(self, format, *args):
        pass


def start_health_server():
    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(("0.0.0.0", port), HealthHandler)
    logger.info("Health server started on port %s", port)
    server.serve_forever()


# ==================== بررسی جوین اجباری ====================

async def check_membership(user_id: int):
    """
    خروجی:
    (True, [])           کاربر مجاز است
    (False, missing)     کاربر باید عضو کانال‌ها شود
    (None, [])           بررسی ناموفق بوده است
    """

    url = f"{ADMIN_API_URL}/api/check"

    headers = {
        "X-API-Key": API_KEY,
    }

    params = {
        "user_id": user_id,
    }

    timeout = aiohttp.ClientTimeout(total=15)

    try:
        async with aiohttp.ClientSession(
            timeout=timeout
        ) as session:
            async with session.get(
                url,
                headers=headers,
                params=params,
            ) as response:

                if response.status != 200:
                    logger.error(
                        "Membership API returned HTTP %s",
                        response.status,
                    )
                    return None, []

                data = await response.json()

        if not data.get("enabled", True):
            return True, []

        missing = data.get("missing", [])

        if not isinstance(missing, list):
            logger.error("Invalid membership API response")
            return None, []

        if data.get("ok") is True and not missing:
            return True, []

        return False, missing

    except Exception:
        logger.exception("Membership API request failed")
        return None, []


def membership_markup(missing: list) -> InlineKeyboardMarkup:
    rows = []

    for channel in missing:
        link = channel.get("link")
        title = channel.get("title") or "کانال"

        if link and isinstance(link, str):
            rows.append([
                InlineKeyboardButton(
                    f"عضویت در {title[:45]}",
                    url=link,
                )
            ])

    rows.append([
        InlineKeyboardButton(
            "✅ عضو شدم، بررسی مجدد",
            callback_data="join:check",
        )
    ])

    return InlineKeyboardMarkup(rows)


async def require_membership(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> bool:
    user = update.effective_user
    message = update.effective_message

    if not user or not message:
        return False

    allowed, missing = await check_membership(user.id)

    if allowed is True:
        return True

    if allowed is None:
        await message.reply_text(
            "⚠️ در حال حاضر امکان بررسی عضویت وجود ندارد.\n"
            "لطفاً چند لحظه بعد دوباره تلاش کن."
        )
        return False

    await message.reply_text(
        "🔒 برای استفاده از ربات، ابتدا در کانال‌های زیر عضو شو.\n"
        "بعد از عضویت روی «عضو شدم، بررسی مجدد» بزن.",
        reply_markup=membership_markup(missing),
    )

    return False


async def recheck_membership(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    query = update.callback_query

    await query.answer()

    allowed, missing = await check_membership(
        query.from_user.id
    )

    if allowed is None:
        await query.answer(
            "بررسی ناموفق بود؛ کمی بعد دوباره تلاش کن.",
            show_alert=True,
        )
        return

    if allowed:
        try:
            await query.edit_message_text(
                "✅ عضویت تأیید شد!\n"
                "حالا لینک اینستاگرام رو بفرست تا دانلود کنم."
            )
        except Exception:
            pass
        return

    try:
        await query.edit_message_text(
            "❌ هنوز عضویتت در همهٔ کانال‌های لازم تأیید نشده.\n"
            "عضو شو و دوباره بررسی کن.",
            reply_markup=membership_markup(missing),
        )
    except Exception:
        pass


# ==================== توابع ربات ====================

def is_instagram_url(text: str) -> bool:
    text = text.lower()

    return (
        "instagram.com" in text
        or "instagr.am" in text
    )


def get_bot_button() -> InlineKeyboardMarkup:
    button = InlineKeyboardButton(
        text="رفتن به ربات",
        url="https://t.me/mewInstaBot",
    )

    return InlineKeyboardMarkup([[button]])


async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not await require_membership(update, context):
        return

    await update.effective_message.reply_text(
        "سلام 👋\n"
        "لینک ریلز یا پست اینستاگرام رو بفرست "
        "تا برات دانلود کنم."
    )


async def handle_url(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    message = update.effective_message

    if not message or not message.text:
        return

    # قبل از هر دانلود، عضویت بررسی می‌شود.
    if not await require_membership(update, context):
        return

    url = message.text.strip()

    if not is_instagram_url(url):
        await message.reply_text(
            "لطفاً فقط لینک معتبر اینستاگرام بفرست."
        )
        return

    status_msg = await message.reply_text(
        "در حال دانلود... ⏳"
    )

    try:
        with TemporaryDirectory() as tmpdir:
            output_template = str(
                Path(tmpdir) / "%(title).50s.%(ext)s"
            )

            ydl_opts = {
                "outtmpl": output_template,
                "quiet": True,
                "no_warnings": True,
                "noplaylist": True,
                "format": (
                    "bestvideo[height<=480][ext=mp4]"
                    "+bestaudio[ext=m4a]/"
                    "best[height<=480][ext=mp4]/best"
                ),
            }

            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(
                    url,
                    download=True,
                )
                filename = ydl.prepare_filename(info)

            if not Path(filename).exists():
                files = list(Path(tmpdir).glob("*"))

                if not files:
                    raise FileNotFoundError(
                        "فایل دانلود نشد"
                    )

                filename = str(files[0])

            file_size = Path(filename).stat().st_size

            if file_size > 49 * 1024 * 1024:
                await status_msg.edit_text(
                    "حجم فایل بیشتر از حد مجاز تلگرام است."
                )
                return

            await status_msg.edit_text(
                "در حال ارسال... 📤"
            )

            caption = (
                "دانلود توسط ربات mewInstaBot انجام شد"
            )

            reply_markup = get_bot_button()

            with open(filename, "rb") as file:
                await context.bot.send_video(
                    chat_id=message.chat_id,
                    video=file,
                    caption=caption,
                    reply_markup=reply_markup,
                )

            await status_msg.delete()

    except Exception:
        logger.exception("Download error")

        try:
            await status_msg.edit_text(
                "❌ دانلود انجام نشد. لطفاً دوباره تلاش کن."
            )
        except Exception:
            pass


# ==================== خطاها ====================

async def on_error(
    update: object,
    context: ContextTypes.DEFAULT_TYPE,
):
    logger.error(
        "Bot error: %s",
        context.error,
    )


# ==================== اجرای ربات ====================

def main():
    # سازگاری با بعضی محیط‌های پایتون
    try:
        asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

    health_thread = threading.Thread(
        target=start_health_server,
        daemon=True,
    )
    health_thread.start()

    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .build()
    )

    app.add_handler(
        CommandHandler("start", start)
    )

    app.add_handler(
        CallbackQueryHandler(
            recheck_membership,
            pattern=r"^join:check$",
        )
    )

    app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            handle_url,
        )
    )

    app.add_error_handler(on_error)

    logger.info("Instagram bot is starting...")

    app.run_polling(
        allowed_updates=Update.ALL_TYPES
    )


if __name__ == "__main__":
    main()
