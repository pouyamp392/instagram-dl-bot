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
    BotCommand,
    BotCommandScopeDefault,
    BotCommandScopeAllPrivateChats,
    ReplyKeyboardMarkup,
    KeyboardButton,
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

BOT_USERNAME = "MewInstaBot"
BOT_LINK = f"https://t.me/{BOT_USERNAME}"

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

logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

logger = logging.getLogger(__name__)


# ==================== سرور سلامت Render ====================

class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(b"Bot is alive")

    def log_message(self, format, *args):
        pass


def start_health_server():
    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(("0.0.0.0", port), HealthHandler)
    logger.info("Health server started on port %s", port)
    server.serve_forever()


# ==================== منوی دستورات تلگرام ====================

async def setup_bot_commands(app: Application):
    commands = [
        BotCommand("start", "🚀 استارت"),
    ]

    await app.bot.set_my_commands(
        commands,
        scope=BotCommandScopeDefault(),
    )

    await app.bot.set_my_commands(
        commands,
        scope=BotCommandScopeAllPrivateChats(),
    )

    logger.info("Bot commands configured.")


# ==================== کیبورد کنار (Reply Keyboard) ====================

def get_main_reply_keyboard() -> ReplyKeyboardMarkup:
    keyboard = [
        [KeyboardButton("🚀 استارت")],
    ]
    return ReplyKeyboardMarkup(
        keyboard,
        resize_keyboard=True,
        is_persistent=True,
        input_field_placeholder="لینک اینستاگرام را بفرست یا استارت بزن...",
    )


# ==================== منوی شیشه‌ای (Inline Keyboard) ====================

def get_main_inline_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "🚀 استارت",
                callback_data="menu:start",
            )
        ],
    ])


# ==================== دکمه رفتن به ربات ====================

def get_bot_button() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                text="🤖 رفتن به ربات",
                url=BOT_LINK,
            )
        ]
    ])


# ==================== بررسی عضویت اجباری ====================

async def check_membership(user_id: int):
    """
    خروجی:
    (True, [])      کاربر مجاز است
    (False, list)   کاربر باید عضو کانال‌ها شود
    (None, [])      بررسی عضویت ناموفق بوده است
    """

    url = f"{ADMIN_API_URL}/api/check"
    headers = {"X-API-Key": API_KEY}
    params = {"user_id": user_id}
    timeout = aiohttp.ClientTimeout(total=20)

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

        if not isinstance(data, dict):
            logger.error("Invalid membership API response")
            return None, []

        if data.get("enabled") is False:
            return True, []

        missing = data.get("missing", [])

        if not isinstance(missing, list):
            logger.error("Invalid missing channels list")
            return None, []

        if data.get("ok") is True and not missing:
            return True, []

        if data.get("ok") is not False:
            logger.error("Membership API returned an uncertain result")
            return None, []

        return False, missing

    except Exception:
        logger.exception("Membership API request failed")
        return None, []


def membership_markup(missing: list) -> InlineKeyboardMarkup:
    rows = []

    for channel in missing:
        if not isinstance(channel, dict):
            continue

        link = channel.get("link")
        title = channel.get("title") or "کانال"

        if isinstance(link, str) and link.startswith(
            ("https://", "http://")
        ):
            rows.append([
                InlineKeyboardButton(
                    text=f"عضویت در {title[:40]}",
                    url=link,
                )
            ])

    rows.append([
        InlineKeyboardButton(
            text="✅ عضو شدم، بررسی مجدد",
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
        "🔒 برای استفاده از ربات، ابتدا در کانال‌های زیر عضو شو.\n\n"
        "بعد از عضویت، روی دکمه «عضو شدم، بررسی مجدد» بزن.",
        reply_markup=membership_markup(missing),
    )

    return False


async def recheck_membership(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    query = update.callback_query

    if not query:
        return

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

    if allowed is True:
        try:
            await query.edit_message_text(
                "✅ عضویتت تأیید شد!\n\n"
                "حالا لینک ریلز یا پست اینستاگرام رو بفرست."
            )
        except Exception:
            pass
        return

    try:
        await query.edit_message_text(
            "❌ هنوز عضویتت در همه کانال‌های لازم تأیید نشده.\n\n"
            "عضو شو و دوباره بررسی کن.",
            reply_markup=membership_markup(missing),
        )
    except Exception:
        pass


# ==================== تشخیص لینک اینستاگرام ====================

def is_instagram_url(text: str) -> bool:
    text = text.lower()

    return (
        "instagram.com/" in text
        or "instagr.am/" in text
    )


# ==================== دستور /start ====================

async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    if not await require_membership(update, context):
        return

    await update.effective_message.reply_text(
        "سلام 👋 به ربات دانلود اینستاگرام خوش اومدی!\n\n"
        "📥 لینک ریلز یا پست اینستاگرام رو بفرست.\n"
        "من ویدیو رو برات دانلود و ارسال می‌کنم. 🚀",
        reply_markup=get_main_reply_keyboard(),
    )

    # منوی شیشه‌ای (جای کیبورد)
    await update.effective_message.reply_text(
        "👇 از دکمه زیر هم می‌تونی استفاده کنی:",
        reply_markup=get_main_inline_keyboard(),
    )


# ==================== هندلر دکمه استارت از کیبورد پایین ====================

async def handle_reply_keyboard(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    message = update.effective_message
    if not message or not message.text:
        return

    if message.text.strip() == "🚀 استارت":
        await start(update, context)


# ==================== هندلر دکمه‌های شیشه‌ای منو ====================

async def menu_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    query = update.callback_query
    if not query:
        return

    await query.answer()

    if query.data == "menu:start":
        # بررسی عضویت
        allowed, missing = await check_membership(query.from_user.id)

        if allowed is True:
            await query.message.reply_text(
                "سلام 👋 به ربات دانلود اینستاگرام خوش اومدی!\n\n"
                "📥 لینک ریلز یا پست اینستاگرام رو بفرست.\n"
                "من ویدیو رو برات دانلود و ارسال می‌کنم. 🚀",
                reply_markup=get_main_reply_keyboard(),
            )
            return

        if allowed is None:
            await query.answer(
                "بررسی عضویت ناموفق بود؛ کمی بعد دوباره تلاش کن.",
                show_alert=True,
            )
            return

        await query.message.reply_text(
            "🔒 برای استفاده از ربات، ابتدا در کانال‌های زیر عضو شو.\n\n"
            "بعد از عضویت، روی دکمه «عضو شدم، بررسی مجدد» بزن.",
            reply_markup=membership_markup(missing),
        )


# ==================== دریافت و دانلود لینک ====================

async def handle_url(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
):
    message = update.effective_message

    if not message or not message.text:
        return

    if not await require_membership(update, context):
        return

    url = message.text.strip()

    if not is_instagram_url(url):
        await message.reply_text(
            "🔗 لطفاً یک لینک معتبر از پست یا ریلز اینستاگرام بفرست."
        )
        return

    status_msg = await message.reply_text(
        "⏳ لینک دریافت شد!\n"
        "در حال دانلود ویدیو..."
    )

    try:
        with TemporaryDirectory() as tmpdir:
            output_template = str(
                Path(tmpdir) / "video.%(ext)s"
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

            def download_video():
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    info = ydl.extract_info(
                        url,
                        download=True,
                    )
                    filename = ydl.prepare_filename(info)

                if not Path(filename).exists():
                    files = [
                        p for p in Path(tmpdir).glob("*")
                        if p.is_file()
                    ]

                    if not files:
                        raise FileNotFoundError(
                            "Downloaded file was not found"
                        )

                    filename = str(files[0])

                return filename

            filename = await asyncio.to_thread(download_video)

            if not Path(filename).exists():
                raise FileNotFoundError("Downloaded file not found")

            file_size = Path(filename).stat().st_size

            if file_size > 49 * 1024 * 1024:
                await status_msg.edit_text(
                    "❌ حجم ویدیو بیشتر از حد مجاز این ربات است."
                )
                return

            await status_msg.edit_text(
                "📤 دانلود با موفقیت انجام شد!\n"
                "در حال ارسال ویدیو..."
            )

            caption = "✅ دانلود شده توسط ربات @MewInstaBot"

            with open(filename, "rb") as video_file:
                await context.bot.send_video(
                    chat_id=message.chat_id,
                    video=video_file,
                    caption=caption,
                    reply_markup=get_bot_button(),
                    supports_streaming=True,
                )

            await status_msg.delete()

    except Exception:
        logger.exception("Download error")

        try:
            await status_msg.edit_text(
                "❌ دانلود انجام نشد.\n"
                "ممکنه لینک خصوصی یا نامعتبر باشه؛ لطفاً دوباره امتحان کن."
            )
        except Exception:
            pass


# ==================== مدیریت خطاها ====================

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
    health_thread = threading.Thread(
        target=start_health_server,
        daemon=True,
    )
    health_thread.start()

    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(setup_bot_commands)
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
        CallbackQueryHandler(
            menu_callback,
            pattern=r"^menu:",
        )
    )

    # هندلر دکمه استارت از کیبورد پایین (قبل از handle_url)
    app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND & filters.Regex(r"^🚀 استارت$"),
            handle_reply_keyboard,
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
