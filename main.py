import os
import logging
import asyncio
import threading
from pathlib import Path
from tempfile import TemporaryDirectory
from http.server import HTTPServer, BaseHTTPRequestHandler

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)
from dotenv import load_dotenv
import yt_dlp

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN not found in environment variables")

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)


# ==================== سرور سلامت برای Render ====================
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
    logger.info(f"Health server started on port {port}")
    server.serve_forever()


# ==================== توابع ربات ====================
def is_instagram_url(text: str) -> bool:
    text = text.lower()
    return "instagram.com" in text or "instagr.am" in text


def get_bot_button() -> InlineKeyboardMarkup:
    button = InlineKeyboardButton(
        text="رفت به ربات",
        url="https://t.me/mewInstaBot"
    )
    return InlineKeyboardMarkup([[button]])


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "سلام 👋\n"
        "لینک ریلز یا پست اینستاگرام رو بفرست تا برات دانلود کنم."
    )


async def handle_url(update: Update, context: ContextTypes.DEFAULT_TYPE):
    url = update.message.text.strip()

    if not is_instagram_url(url):
        await update.message.reply_text("لطفاً فقط لینک معتبر اینستاگرام بفرست.")
        return

    status_msg = await update.message.reply_text("در حال دانلود... ⏳")

    try:
        with TemporaryDirectory() as tmpdir:
            output_template = str(Path(tmpdir) / "%(title).50s.%(ext)s")

            ydl_opts = {
                "outtmpl": output_template,
                "quiet": True,
                "no_warnings": True,
                "noplaylist": True,
                # کیفیت پیش‌فرض: ۴۸۰p
                "format": "bestvideo[height<=480][ext=mp4]+bestaudio[ext=m4a]/best[height<=480][ext=mp4]/best",
            }

            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)
                filename = ydl.prepare_filename(info)

            if not Path(filename).exists():
                files = list(Path(tmpdir).glob("*"))
                if not files:
                    raise FileNotFoundError("فایل دانلود نشد")
                filename = str(files[0])

            file_size = Path(filename).stat().st_size
            if file_size > 49 * 1024 * 1024:
                await status_msg.edit_text("حجم فایل بیشتر از حد مجاز تلگرام است.")
                return

            await status_msg.edit_text("در حال ارسال... 📤")

            caption = "دانلود توسط ربات mewInstaBot انجام شد"
            reply_markup = get_bot_button()

            with open(filename, "rb") as f:
                await context.bot.send_video(
                    chat_id=update.message.chat_id,
                    video=f,
                    caption=caption,
                    reply_markup=reply_markup
                )

            await status_msg.delete()

    except Exception as e:
        logger.exception("Download error")
        await status_msg.edit_text(f"❌ خطا در دانلود:\n{str(e)[:300]}")


def main():
    # رفع مشکل Python 3.14
    try:
        asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

    # شروع سرور سلامت
    health_thread = threading.Thread(target=start_health_server, daemon=True)
    health_thread.start()

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_url))

    logger.info("Bot is starting...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
