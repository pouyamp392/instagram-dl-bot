import os
import logging
import asyncio
from pathlib import Path
from tempfile import TemporaryDirectory

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
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

BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN not found in environment variables")

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

QUALITIES = {
    "best": "بهترین کیفیت",
    "720": "۷۲۰p",
    "480": "۴۸۰p",
    "audio": "فقط صدا (MP3)"
}


def is_instagram_url(text: str) -> bool:
    text = text.lower()
    return "instagram.com" in text or "instagr.am" in text


def get_quality_keyboard(url: str) -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton(text=title, callback_data=f"dl|{key}|{url}")]
        for key, title in QUALITIES.items()
    ]
    return InlineKeyboardMarkup(buttons)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "سلام 👋\n"
        "لینک ریلز یا پست اینستاگرام رو بفرست تا برات دانلود کنم.\n\n"
        "بعد از ارسال لینک، کیفیت مورد نظرت رو انتخاب کن."
    )


async def handle_url(update: Update, context: ContextTypes.DEFAULT_TYPE):
    url = update.message.text.strip()

    if not is_instagram_url(url):
        await update.message.reply_text("لطفاً فقط لینک معتبر اینستاگرام بفرست.")
        return

    await update.message.reply_text(
        "لینک دریافت شد ✅\nکیفیت مورد نظرت رو انتخاب کن:",
        reply_markup=get_quality_keyboard(url)
    )


async def download_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    try:
        _, quality, url = query.data.split("|", 2)
    except ValueError:
        await query.message.reply_text("خطا در پردازش درخواست.")
        return

    status_msg = await query.message.reply_text("در حال آماده‌سازی دانلود... ⏳")

    try:
        with TemporaryDirectory() as tmpdir:
            output_template = str(Path(tmpdir) / "%(title).50s.%(ext)s")

            ydl_opts = {
                "outtmpl": output_template,
                "quiet": True,
                "no_warnings": True,
                "noplaylist": True,
            }

            if quality == "audio":
                ydl_opts.update({
                    "format": "bestaudio/best",
                    "postprocessors": [{
                        "key": "FFmpegExtractAudio",
                        "preferredcodec": "mp3",
                        "preferredquality": "192",
                    }],
                })
            elif quality == "best":
                ydl_opts["format"] = "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best"
            elif quality == "720":
                ydl_opts["format"] = "bestvideo[height<=720][ext=mp4]+bestaudio[ext=m4a]/best[height<=720][ext=mp4]/best"
            elif quality == "480":
                ydl_opts["format"] = "bestvideo[height<=480][ext=mp4]+bestaudio[ext=m4a]/best[height<=480][ext=mp4]/best"

            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=True)
                filename = ydl.prepare_filename(info)

                if quality == "audio":
                    filename = str(Path(filename).with_suffix(".mp3"))

            if not Path(filename).exists():
                files = list(Path(tmpdir).glob("*"))
                if not files:
                    raise FileNotFoundError("فایل دانلود نشد")
                filename = str(files[0])

            file_size = Path(filename).stat().st_size
            if file_size > 49 * 1024 * 1024:
                await status_msg.edit_text("حجم فایل بیشتر از حد مجاز تلگرام است (حدود ۵۰ مگابایت).")
                return

            await status_msg.edit_text("در حال ارسال فایل... 📤")

            with open(filename, "rb") as f:
                if quality == "audio":
                    await context.bot.send_audio(
                        chat_id=query.message.chat_id,
                        audio=f,
                        caption="✅ دانلود انجام شد"
                    )
                else:
                    await context.bot.send_video(
                        chat_id=query.message.chat_id,
                        video=f,
                        caption="✅ دانلود انجام شد"
                    )

            await status_msg.delete()

    except Exception as e:
        logger.exception("Download error")
        await status_msg.edit_text(f"❌ خطا در دانلود:\n{str(e)[:300]}")


def main():
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_url))
    app.add_handler(CallbackQueryHandler(download_callback, pattern=r"^dl\|"))

    logger.info("Bot is starting...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
