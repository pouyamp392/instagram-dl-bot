import asyncio
import os
import logging
from pathlib import Path
from tempfile import TemporaryDirectory

from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, CallbackQuery, FSInputFile, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.filters import CommandStart
from aiogram.enums import ParseMode
from dotenv import load_dotenv
import yt_dlp

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN not found")

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# کیفیت‌های قابل انتخاب
QUALITIES = {
    "best": "بهترین کیفیت",
    "720": "۷۲۰p",
    "480": "۴۸۰p",
    "audio": "فقط صدا (MP3)"
}

def is_instagram_url(text: str) -> bool:
    return "instagram.com" in text.lower() or "instagr.am" in text.lower()

def get_quality_keyboard(url: str) -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton(text=title, callback_data=f"dl|{key}|{url}")]
        for key, title in QUALITIES.items()
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)

@dp.message(CommandStart())
async def start_handler(message: Message):
    await message.answer(
        "سلام 👋\n"
        "لینک ریلز یا پست اینستاگرام رو بفرست تا برات دانلود کنم.\n\n"
        "بعد از ارسال لینک، کیفیت مورد نظرت رو انتخاب کن."
    )

@dp.message(F.text)
async def url_handler(message: Message):
    url = message.text.strip()

    if not is_instagram_url(url):
        await message.answer("لطفاً فقط لینک معتبر اینستاگرام بفرست.")
        return

    await message.answer(
        "لینک دریافت شد ✅\nکیفیت مورد نظرت رو انتخاب کن:",
        reply_markup=get_quality_keyboard(url)
    )

@dp.callback_query(F.data.startswith("dl|"))
async def download_handler(callback: CallbackQuery):
    await callback.answer()

    try:
        _, quality, url = callback.data.split("|", 2)
    except ValueError:
        await callback.message.answer("خطا در پردازش درخواست.")
        return

    status_msg = await callback.message.answer("در حال آماده‌سازی دانلود... ⏳")

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

                # اگر صدا استخراج شده باشه، پسوند عوض می‌شه
                if quality == "audio":
                    filename = str(Path(filename).with_suffix(".mp3"))

            if not Path(filename).exists():
                # گاهی اسم فایل کمی فرق می‌کنه
                files = list(Path(tmpdir).glob("*"))
                if not files:
                    raise FileNotFoundError("فایل دانلود نشد")
                filename = str(files[0])

            file_size = Path(filename).stat().st_size
            if file_size > 49 * 1024 * 1024:  # محدودیت تقریبی تلگرام
                await status_msg.edit_text("حجم فایل بیشتر از حد مجاز تلگرام است (حدود ۵۰ مگابایت).")
                return

            await status_msg.edit_text("در حال ارسال فایل... 📤")

            media = FSInputFile(filename)

            if quality == "audio":
                await callback.message.answer_audio(media, caption="✅ دانلود انجام شد")
            else:
                await callback.message.answer_video(media, caption="✅ دانلود انجام شد")

            await status_msg.delete()

    except Exception as e:
        logger.exception("Download error")
        await status_msg.edit_text(f"❌ خطا در دانلود:\n`{str(e)[:200]}`", parse_mode=ParseMode.MARKDOWN)

async def main():
    logger.info("Bot started")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
