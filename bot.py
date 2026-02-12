import os
import asyncio
import glob
import shutil
import time
import re
import requests
import pymongo
from pyrogram import Client, filters, idle

# --- 0. PRE-FLIGHT SETUP ---
# Create Aria2 config for speed (runs on bot start)
CONFIG_DIR = "/root/.config/yt-dlp"
if not os.path.exists(CONFIG_DIR):
    os.makedirs(CONFIG_DIR, exist_ok=True)
    config_content = """
--external-downloader aria2c
--external-downloader-args "-x 16 -k 1M"
--no-mtime
--buffer-size 16M
"""
    with open(f"{CONFIG_DIR}/config", "w") as f:
        f.write(config_content)
    print("🚀 Aria2 Config Injected.")

# --- 1. CONFIGURATION ---
# We use os.getenv for Koyeb env vars
API_ID = int(os.getenv('API_ID', 0))
API_HASH = os.getenv('API_HASH')
BOT_TOKEN = os.getenv('BOT_TOKEN')
DB_CHANNEL_ID = int(os.getenv('DB_CHANNEL_ID', 0))
MONGO_URL = os.getenv('MONGO_URL')

try:
    mongo_client = pymongo.MongoClient(MONGO_URL)
    mongo_db = mongo_client["TitanFactoryBot"]
    post_queue = mongo_db["post_queue"]
    print("✅ Connected to MongoDB.")
except Exception as e:
    print(f"❌ MongoDB Error: {e}")

ACTIVE_TASKS = {}
DOWNLOAD_DIR = "./downloads"
os.makedirs(DOWNLOAD_DIR, exist_ok=True)

app = Client("anime_factory", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

# --- 2. JIKAN API ---
def get_anime_details(query):
    try:
        url = f"https://api.jikan.moe/v4/anime?q={query}&limit=1"
        res = requests.get(url, timeout=10).json()
        if res['data']:
            data = res['data'][0]
            genres = ", ".join([g['name'] for g in data.get('genres', [])[:3]])
            syn = data.get('synopsis', 'N/A')
            if len(syn) > 250: syn = syn[:250] + "..."
            return {
                "title": data.get('title', query),
                "score": data.get('score', 'N/A'),
                "type": data.get('type', 'TV'),
                "genres": genres,
                "synopsis": syn,
                "poster": data['images']['jpg']['large_image_url']
            }
    except: pass
    return {"title": query, "score": "N/A", "type": "TV", "genres": "", "synopsis": "", "poster": None}

# --- 3. HELPERS ---
def parse_episodes(ep_string):
    episodes = []
    parts = ep_string.split(',')
    for part in parts:
        if '-' in part:
            s, e = map(int, part.split('-'))
            episodes.extend(range(s, e + 1))
        else: episodes.append(int(part))
    return sorted(list(set(episodes)))

def find_downloaded_file(episode):
    search_pattern = f"**/*Episode {episode}*.mp4"
    files = glob.glob(search_pattern, recursive=True)
    valid_files = [f for f in files if "_sub" not in f and "_dual" not in f and "[Sub]" not in f and "[Dual]" not in f]
    if not valid_files: return None
    return max(valid_files, key=os.path.getctime)

async def get_duration(filepath):
    try:
        cmd = f"ffprobe -v error -show_entries format=duration -of default=noprint_wrappers=1:nokey=1 \"{filepath}\""
        proc = await asyncio.create_subprocess_shell(cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        out, _ = await proc.communicate()
        return float(out.decode().strip())
    except:
        return 0.0

async def create_dual_audio(sub_file, dub_file, output_file):
    print(f"🛠 Muxing: {os.path.basename(sub_file)} + English Audio")
    cmd = (
        f'ffmpeg -y -i "{sub_file}" -i "{dub_file}" '
        f'-map 0:v -map 0:a -map 1:a '
        f'-c:v copy -c:a copy '
        f'-disposition:a:0 default '
        f'-metadata:s:a:0 language=jpn -metadata:s:a:0 title="Japanese" '
        f'-metadata:s:a:1 language=eng -metadata:s:a:1 title="English" '
        f'"{output_file}"'
    )
    proc = await asyncio.create_subprocess_shell(cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    await proc.communicate()
    return os.path.exists(output_file)

# --- 5. MAIN WORKER ---
@app.on_message(filters.command("batch"))
async def batch_dl(client, message):
    chat_id = message.chat.id
    if chat_id in ACTIVE_TASKS: return await message.reply("⚠️ Factory is busy.")

    txt = message.text[6:]
    if not txt: return await message.reply("Usage: `/batch -a Name -e 1-5 -r all -dual`")

    try:
        name_m = re.search(r'-a\s+["\']([^"\']+)["\']', txt)
        name = name_m.group(1) if name_m else "Anime"
        ep_m = re.search(r'-e\s+([\d,-]+)', txt)
        eps = parse_episodes(ep_m.group(1)) if ep_m else []
        if not eps: return await message.reply("❌ Missing Episodes (-e)")

        if "all" in txt: resolutions = ["360", "720", "1080"]
        else:
            r_m = re.search(r'-r\s+(\d+)', txt)
            resolutions = [r_m.group(1)] if r_m else ["720"]
 
        IS_DUAL = "-dual" in txt
    except Exception as e: return await message.reply(f"❌ Parse Error: {e}")

    ACTIVE_TASKS[chat_id] = True
    status = await message.reply(f"🏭 **Factory Started**\nGoal: {name}\nEps: {len(eps)}\nDual Audio: {IS_DUAL}")
    info = get_anime_details(name)

    # 🟢 CHANGED: SLOW MODE IMPLEMENTATION
    for i, res in enumerate(resolutions):
        if i > 0:
            await status.edit(f"⏳ **Cooldown: Waiting 60s before {res}p...**")
            await asyncio.sleep(60) # 1 Minute wait between qualities

        batch_ids = []
        await status.edit(f"📥 **Batch: {res}p**")

        for ep in eps:
            if chat_id not in ACTIVE_TASKS: break

            # 1. Download SUB
            cmd_sub = f"./animepahe-dl.sh -a \"{name}\" -e {ep} -r {res} -o jpn"
            proc = await asyncio.create_subprocess_shell(cmd_sub, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, preexec_fn=os.setsid)
            await proc.communicate()

            sub_file = find_downloaded_file(ep)
            if not sub_file:
                await client.send_message(chat_id, f"❌ Failed to DL Ep {ep} ({res}p)")
                continue

            # Rename SUB
            new_sub = sub_file.replace(".mp4", "_sub.mp4")
            os.rename(sub_file, new_sub)
            sub_file = new_sub
            final_file = sub_file
            is_dual_success = False

            # 2. Check Dub + SAFETY CHECK
            if IS_DUAL:
                await status.edit(f"📥 **Checking Dub: Ep {ep}**")
                cmd_dub = f"./animepahe-dl.sh -a \"{name}\" -e {ep} -r {res} -o eng"
                proc = await asyncio.create_subprocess_shell(cmd_dub, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, preexec_fn=os.setsid)
                await proc.communicate()

                dub_file = find_downloaded_file(ep)

                if dub_file and dub_file != sub_file:
                    dur_sub = await get_duration(sub_file)
                    dur_dub = await get_duration(dub_file)

                    if abs(dur_sub - dur_dub) < 2.0:
                        muxed_file = sub_file.replace("_sub.mp4", " [Dual].mkv")
                        success = await create_dual_audio(sub_file, dub_file, muxed_file)
                        if success:
                            final_file = muxed_file
                            is_dual_success = True
                            try:
                                os.remove(sub_file)
                                os.remove(dub_file)
                            except: pass
                        else:
                            await client.send_message(chat_id, f"⚠️ Mux Failed Ep {ep}. Using Sub.")
                    else:
                        diff = round(abs(dur_sub - dur_dub), 2)
                        await client.send_message(chat_id, f"⚠️ **Desync Risk:** Diff {diff}s. Uploading Sub.")
                        try: os.remove(dub_file)
                        except: pass
                else:
                    await client.send_message(chat_id, f"⚠️ No Dub found for Ep {ep}. Using Sub.")

            # 3. Final Rename (Sub Only)
            if not is_dual_success:
                clean_sub = sub_file.replace("_sub.mp4", " [Sub].mp4")
                try:
                    os.rename(sub_file, clean_sub)
                    final_file = clean_sub
                except: pass

            # 4. Upload
            if os.path.exists(final_file):
                await status.edit(f"🚀 **Uploading: Ep {ep} ({res}p)**")
                caption = f"{info['title']} - Episode {ep} [{res}p]"
                if is_dual_success: caption += " [Dual Audio]"

                try:
                    # Uploads to Archive Channel
                    msg = await client.send_document(chat_id=DB_CHANNEL_ID, document=final_file, caption=caption, force_document=True)
                    batch_ids.append(msg.id)
                    os.remove(final_file)
                except Exception as e:
                    await client.send_message(chat_id, f"❌ Upload Error: {e}")

        # 5. Save Job (This logs it to Mongo)
        if batch_ids:
            job_data = {
                "anime": info['title'],
                "poster": info['poster'],
                "synopsis": info['synopsis'],
                "genres": info['genres'],
                "score": info['score'],
                "type": info['type'],
                "resolution": res,
                "file_ids": batch_ids,
                "range_start": batch_ids[0],
                "range_end": batch_ids[-1],
                "dual_audio": IS_DUAL,
                "status": "pending_post",
                "timestamp": time.time()
            }
            post_queue.insert_one(job_data)
            await client.send_message(chat_id, f"✅ **Batch Done: {res}p**\nSaved {len(batch_ids)} files.")

    await status.edit("🎉 **Job Complete.**")
    del ACTIVE_TASKS[chat_id]
    try: shutil.rmtree(name)
    except: pass

@app.on_message(filters.command("cancel"))
async def cancel_task(client, message):
    if message.chat.id in ACTIVE_TASKS:
        del ACTIVE_TASKS[message.chat.id]
        await message.reply("🛑 Task Cancelled.")

if __name__ == "__main__":
    print("🏭 Factory Bot Online")
    app.run()
