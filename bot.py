import asyncio
import os
import shutil
import uuid
from pathlib import Path
from dotenv import load_dotenv

# Clean PM2 IPC channel variables that break child processes
os.environ.pop("NODE_CHANNEL_FD", None)

from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, FSInputFile

import downloader

load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")

if not BOT_TOKEN or BOT_TOKEN == "your_telegram_bot_token_here":
    raise ValueError("Please set your BOT_TOKEN in the .env file")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

YOUTUBE_DOMAINS = ["youtube.com", "youtu.be", "www.youtube.com", "m.youtube.com"]

# In-memory dictionary to store video urls for callbacks
url_store = {}

def is_youtube_url(text: str) -> bool:
    try:
        return any(domain in text.lower() for domain in YOUTUBE_DOMAINS)
    except Exception:
        return False

@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    if Path("hello.webm").exists():
        try:
            await message.answer_sticker(FSInputFile("hello.webm"))
        except Exception:
            pass
    await message.answer(
        "👋 **Welcome to YouTube Downloader Bot!** 🎬\n\n"
        "Send me a YouTube link, and I will download and send you the video or audio file directly here in Telegram!",
        parse_mode="Markdown"
    )

@dp.message(F.text)
async def handle_url(message: types.Message):
    url = message.text.strip()
    if not is_youtube_url(url):
        return
        
    status_msg = await message.reply("⏳ Fetching video information...")
    
    try:
        info = await asyncio.to_thread(downloader.get_video_info, url)
        
        # Build keyboard
        keyboard_buttons = []
        
        # Add video resolutions in pairs
        row = []
        for res in info.get("resolutions", []):
            text = f"🎬 {res['height']}p"
            if res['size_mb'] > 0:
                text += f" ({res['size_mb']:.1f}MB)"
            
            cb_data = f"dl_v_{res['height']}"
            row.append(InlineKeyboardButton(text=text, callback_data=cb_data))
            
            if len(row) == 2:
                keyboard_buttons.append(row)
                row = []
        if row:
            keyboard_buttons.append(row)
            
        # Add audio option
        audio_text = "🎵 Audio (MP3)"
        if info.get('audio_size_mb', 0) > 0:
            audio_text += f" (~{info['audio_size_mb']:.1f}MB)"
        
        # Add extra features row
        keyboard_buttons.append([
            InlineKeyboardButton(text=audio_text, callback_data="dl_audio")
        ])
        keyboard_buttons.append([
            InlineKeyboardButton(text="🖼️ Thumbnail", callback_data="dl_thumb"),
            InlineKeyboardButton(text="📝 Subtitles", callback_data="dl_subs")
        ])
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=keyboard_buttons)
        
        # Safely extract title and channel
        title = str(info.get('title', 'Unknown')).replace('*', '').replace('_', '')
        channel = str(info.get('channel', 'Unknown')).replace('*', '').replace('_', '')
        
        caption = f"🎬 *{title}*\n👤 {channel}\n\nSelect format to download directly:"
        
        photo_msg = await message.answer_photo(
            photo=info["thumbnail"],
            caption=caption,
            parse_mode="Markdown",
            reply_markup=keyboard,
            reply_to_message_id=message.message_id
        )
        url_store[photo_msg.message_id] = url
        
        await status_msg.delete()
        
    except Exception as e:
        await status_msg.edit_text(f"❌ Failed to fetch video info:\n{str(e)[:500]}")


@dp.callback_query(F.data.startswith("dl_"))
async def handle_download_callback(callback_query: types.CallbackQuery):
    action = callback_query.data
    
    # Retrieve URL from memory store
    msg_id = callback_query.message.message_id
    if msg_id not in url_store:
        await callback_query.answer("Session expired. Please send the link again.", show_alert=True)
        return
        
    url = url_store[msg_id]
    
    # Handle Thumbnail immediately (no heavy download needed)
    if action == "dl_thumb":
        await callback_query.answer("Sending thumbnail...")
        info = await asyncio.to_thread(downloader.get_video_info, url)
        if info.get("thumbnail"):
            await bot.send_document(
                chat_id=callback_query.message.chat.id,
                document=info["thumbnail"],
                caption=f"🖼️ Thumbnail for: *{info.get('title', 'Video')}*",
                parse_mode="Markdown",
                reply_to_message_id=callback_query.message.reply_to_message.message_id if callback_query.message.reply_to_message else None
            )
        else:
            await callback_query.answer("No thumbnail available.", show_alert=True)
        return

    original_caption = callback_query.message.caption or ""
    await callback_query.message.edit_caption(
        caption=f"{original_caption}\n\n⏳ **Starting download...**",
        parse_mode="Markdown"
    )
    
    # Send downloading sticker if available
    sticker_msg = None
    if Path("downloading.webm").exists():
        try:
            sticker_msg = await bot.send_sticker(
                chat_id=callback_query.message.chat.id,
                sticker=FSInputFile("downloading.webm")
            )
        except Exception:
            pass
    
    job_id = str(uuid.uuid4())
    job_dir = Path("downloads") / job_id
    progress_dict = {}
    
    try:
        if action == "dl_subs":
            dl_task = asyncio.create_task(
                asyncio.to_thread(downloader.download_subtitles, url, job_id)
            )
        elif action.startswith("dl_v_"):
            res = int(action.split("_")[2])
            dl_task = asyncio.create_task(
                asyncio.to_thread(downloader.download_video, url, job_id, res, progress_dict)
            )
        else:
            dl_task = asyncio.create_task(
                asyncio.to_thread(downloader.download_audio, url, job_id, progress_dict)
            )
            
        # Live Progress Updater Loop
        last_caption = ""
        while not dl_task.done():
            await asyncio.sleep(2)
            if job_id in progress_dict:
                progress_text = progress_dict[job_id]
                new_caption = f"{original_caption}\n\n⏳ **Downloading:**\n{progress_text}"
                if new_caption != last_caption:
                    try:
                        await callback_query.message.edit_caption(
                            caption=new_caption, 
                            parse_mode="Markdown"
                        )
                        last_caption = new_caption
                    except Exception:
                        pass
                        
        filepath = dl_task.result()
        
        if not filepath or not filepath.exists():
            await callback_query.message.edit_caption(caption=f"{original_caption}\n\n❌ Could not find the requested file.")
            return
            
        await callback_query.message.edit_caption(
            caption=f"{original_caption}\n\n📤 **Uploading to Telegram...**",
            parse_mode="Markdown"
        )
        
        file = FSInputFile(path=filepath)
        reply_id = callback_query.message.reply_to_message.message_id if callback_query.message.reply_to_message else None
        
        if action == "dl_subs":
            await bot.send_document(
                chat_id=callback_query.message.chat.id, 
                document=file,
                reply_to_message_id=reply_id
            )
        elif action.startswith("dl_v_"):
            await bot.send_video(
                chat_id=callback_query.message.chat.id, 
                video=file,
                reply_to_message_id=reply_id
            )
        else:
            await bot.send_audio(
                chat_id=callback_query.message.chat.id, 
                audio=file,
                reply_to_message_id=reply_id
            )
            
        # Clean up the menu message
        await callback_query.message.delete()
        
    except Exception as e:
        try:
            await callback_query.message.edit_caption(caption=f"❌ An error occurred:\n{str(e)[:500]}")
        except Exception:
            pass
    finally:
        if job_dir.exists():
            shutil.rmtree(job_dir, ignore_errors=True)
        if sticker_msg:
            try:
                await sticker_msg.delete()
            except Exception:
                pass
            
    await callback_query.answer()

async def main():
    print("Starting direct YouTube downloader bot...")
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())

