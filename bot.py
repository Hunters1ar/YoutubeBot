import asyncio
import os
import shutil
import uuid
from pathlib import Path
from dotenv import load_dotenv

from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, FSInputFile, InputMediaAnimation

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
    await message.answer_sticker(FSInputFile("hello.webm"))
    await message.answer(
        "Hello! I am a YouTube Downloader Bot. 🎬\n\n"
        "Send me a YouTube link, and I will download it for you."
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
        for res in info["resolutions"]:
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
        if info['audio_size_mb'] > 0:
            audio_text += f" (~{info['audio_size_mb']:.1f}MB)"
        
        # Add extra features row
        keyboard_buttons.append([
            InlineKeyboardButton(text="🖼️ Thumbnail", callback_data="dl_thumb"),
            InlineKeyboardButton(text="📝 Subtitles", callback_data="dl_subs")
        ])
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=keyboard_buttons)
        
        # Safely extract title and channel to prevent markdown parsing errors
        title = info.get('title', 'Unknown').replace('*', '').replace('_', '')
        channel = info.get('channel', 'Unknown').replace('*', '').replace('_', '')
        
        caption = f"*{title}*\n👤 {channel}\n\nSelect a format to download:"
        
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
        await callback_query.answer("Session expired or URL not found.", show_alert=True)
        return
        
    url = url_store[msg_id]
    
    # Update caption to show downloading
    original_caption = callback_query.message.caption or ""
    
    # Handle Thumbnail immediately (it doesn't need yt-dlp download)
    if action == "dl_thumb":
        await callback_query.answer("Sending thumbnail...")
        info = await asyncio.to_thread(downloader.get_video_info, url)
        if info.get("thumbnail"):
            # Send as a document to preserve high quality
            await bot.send_document(
                chat_id=callback_query.message.chat.id,
                document=info["thumbnail"],
                reply_to_message_id=callback_query.message.reply_to_message.message_id
            )
        else:
            await callback_query.answer("No thumbnail available.", show_alert=True)
        return

    try:
        await callback_query.message.edit_media(
            media=InputMediaAnimation(
                media=FSInputFile("downloading.webm"),
                caption=f"{original_caption}\n\n⏳ Starting download...",
                parse_mode="Markdown"
            )
        )
    except Exception:
        await callback_query.message.edit_caption(
            caption=f"{original_caption}\n\n⏳ Starting download..."
        )
    
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
            
        # Progress Updater Loop
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
                        pass # Ignore Telegram's "Message is not modified" error
                        
        filepath = dl_task.result()
        
        if not filepath or not filepath.exists():
            await callback_query.message.edit_caption(caption=f"{original_caption}\n\n❌ Could not find the requested file (subtitles might not exist).")
            return
            
        await callback_query.message.edit_caption(
            caption=f"{original_caption}\n\n📤 Uploading to Telegram..."
        )
        
        file = FSInputFile(path=filepath)
        
        if action == "dl_subs":
            await bot.send_document(
                chat_id=callback_query.message.chat.id, 
                document=file,
                reply_to_message_id=callback_query.message.reply_to_message.message_id
            )
        elif action.startswith("dl_v_"):
            await bot.send_video(
                chat_id=callback_query.message.chat.id, 
                video=file,
                reply_to_message_id=callback_query.message.reply_to_message.message_id
            )
        else:
            await bot.send_audio(
                chat_id=callback_query.message.chat.id, 
                audio=file,
                reply_to_message_id=callback_query.message.reply_to_message.message_id
            )
            
        # Clean up the menu message when done
        await callback_query.message.delete()
        
    except Exception as e:
        try:
            await callback_query.message.edit_caption(caption=f"❌ An error occurred:\n{str(e)[:500]}")
        except:
            pass
    finally:
        if job_dir.exists():
            shutil.rmtree(job_dir, ignore_errors=True)
            
    await callback_query.answer()

async def main():
    print("Starting bot...")
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
