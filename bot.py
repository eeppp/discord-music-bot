import discord
from discord.ext import commands
import yt_dlp
import random
import asyncio

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

queues = {}
is_looping = {}
guild_volumes = {}

# إعدادات yt-dlp المحسنة لانتزاع أسرع وأثبت رابط بث مباشر
ytdl_format_options = {
    'format': 'bestaudio/best',
    'noplaylist': True,
    'quiet': True,
    'default_search': 'auto',
    'source_address': '0.0.0.0',
    'nocheckcertificate': True,
    'ignoreerrors': False,
    'no_warnings': True,
    'extractor_args': {
        'youtube': {
            'player_client': ['android', 'web'],
        }
    }
}
ytdl = yt_dlp.YoutubeDL(ytdl_format_options)

class YTDLSource(discord.PCMVolumeTransformer):
    def __init__(self, source, *, data, volume=0.5):
        super().__init__(source, volume)
        self.data = data
        self.title = data.get('title')
        self.url = data.get('url')
        self.duration = data.get('duration', 0)

    @classmethod
    async def from_url(cls, query, *, loop=None, volume=0.5):
        loop = loop or asyncio.get_event_loop()
        search_query = query if query.startswith("http") else f"ytsearch:{query}"
        
        data = await loop.run_in_executor(None, lambda: ytdl.extract_info(search_query, download=False))
        
        if 'entries' in data:
            data = data['entries'][0]

        filename = data['url']
        
        # خيارات FFmpeg المحدثة لحل مشكلة تقطيع الصوت في ويندوز نهائياً
        ffmpeg_options = {
            'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5 -probesize 32M -analyzeduration 0',
            'options': '-re -vn -ar 48000 -ac 2 -b:a 192k -bufsize 512k'
        }
        
        audio_source = discord.FFmpegPCMAudio(filename, **ffmpeg_options)
        return cls(audio_source, data=data, volume=volume)

def get_progress_bar(current_time=0, total_time=100, length=20):
    if total_time == 0:
        return "🔴 ▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬▬ 0:00"
    percent = min(current_time / total_time, 1.0)
    index = int(round(percent * length))
    bar = "▬" * index + "🔘" + "▬" * (length - index)
    cur_mins, cur_secs = divmod(int(current_time), 60)
    tot_mins, tot_secs = divmod(int(total_time), 60)
    return f"🎶 [{bar}] {cur_mins}:{cur_secs:02d} / {tot_mins}:{tot_secs:02d}"

class MusicControlView(discord.ui.View):
    def __init__(self, ctx):
        super().__init__(timeout=None)
        self.ctx = ctx

    @discord.ui.button(label="تشغيل/إيقاف", style=discord.ButtonStyle.blurple, emoji="⏯️")
    async def pause_resume(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.ctx.voice_client:
            if self.ctx.voice_client.is_playing():
                self.ctx.voice_client.pause()
                await interaction.response.send_message("⏸️ تم إيقاف التشغيل مؤقتاً.", ephemeral=True)
            elif self.ctx.voice_client.is_paused():
                self.ctx.voice_client.resume()
                await interaction.response.send_message("▶️ تم استئناف التشغيل.", ephemeral=True)
            else:
                await interaction.response.send_message("⚠️ لا يوجد شيء شغال حالياً.", ephemeral=True)

    @discord.ui.button(label="خفي الصوت 🔉", style=discord.ButtonStyle.gray)
    async def volume_down(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild_id = self.ctx.guild.id
        current_vol = guild_volumes.get(guild_id, 0.5)
        new_vol = max(0.0, current_vol - 0.1)
        guild_volumes[guild_id] = new_vol
        if self.ctx.voice_client and self.ctx.voice_client.source:
            self.ctx.voice_client.source.volume = new_vol
        await interaction.response.send_message(f"🔉 تم خفض الصوت إلى: **{int(new_vol * 100)}%**", ephemeral=True)

    @discord.ui.button(label="علي الصوت 🔊", style=discord.ButtonStyle.gray)
    async def volume_up(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild_id = self.ctx.guild.id
        current_vol = guild_volumes.get(guild_id, 0.5)
        new_vol = min(2.0, current_vol + 0.1)
        guild_volumes[guild_id] = new_vol
        if self.ctx.voice_client and self.ctx.voice_client.source:
            self.ctx.voice_client.source.volume = new_vol
        await interaction.response.send_message(f"🔊 تم رفع الصوت إلى: **{int(new_vol * 100)}%**", ephemeral=True)

    @discord.ui.button(label="تخطي", style=discord.ButtonStyle.green, emoji="⏭️")
    async def skip_song(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.ctx.voice_client and self.ctx.voice_client.is_playing():
            self.ctx.voice_client.stop()
            await interaction.response.send_message("⏭️ تم تخطي الأغنية.", ephemeral=True)
        else:
            await interaction.response.send_message("⚠️ مافي شيء عشان أتخطاه!", ephemeral=True)

    @discord.ui.button(label="إيقاف", style=discord.ButtonStyle.red, emoji="⏹️")
    async def stop_bot(self, interaction: discord.Interaction, button: discord.ui.Button):
        guild_id = self.ctx.guild.id
        if guild_id in queues:
            queues[guild_id].clear()
        if self.ctx.voice_client:
            await self.ctx.voice_client.disconnect()
            await interaction.response.send_message("👋 تم إيقاف البوت ومغادرة الروم.", ephemeral=True)

@bot.event
async def on_ready():
    print(f"✅ تم تسجيل الدخول بنجاح باسم: {bot.user}")

async def play_next(ctx):
    guild_id = ctx.guild.id
    if guild_id in queues and len(queues[guild_id]) > 0:
        if is_looping.get(guild_id, False):
            player = queues[guild_id][0]
        else:
            player = queues[guild_id].pop(0)

        def after_playing(error):
            if error:
                print(f"خطأ في المشغل: {error}")
            fut = asyncio.run_coroutine_threadsafe(play_next(ctx), bot.loop)
            try:
                fut.result()
            except Exception as e:
                print(e)

        if ctx.voice_client and ctx.voice_client.is_connected():
            ctx.voice_client.play(player, after=after_playing)
            progress = get_progress_bar(0, player.duration)
            view = MusicControlView(ctx)
            await ctx.send(f"🎵 **شغال الحين:** {player.title}\n{progress}", view=view)
    else:
        if ctx.voice_client and ctx.voice_client.is_connected():
            await ctx.send("✅ خلصت جميع المقاطع في القائمة.")

@bot.command(name="ش", help="يشغل أغنية برابط يوتيوب أو بالاسم")
async def play(ctx, *, query):
    if not ctx.author.voice:
        await ctx.send("❌ لازم تكون داخل روم صوتي عشان تشغل البوت!")
        return

    channel = ctx.author.voice.channel
    guild_id = ctx.guild.id

    if not ctx.voice_client:
        try:
            await channel.connect(timeout=15.0, reconnect=True)
        except Exception as e:
            await ctx.send(f"⚠️ يتعذر الاتصال بالروم الصوتي: {e}")
            return
    elif ctx.voice_client.channel != channel:
        await ctx.voice_client.move_to(channel)

    msg = await ctx.send("🔍 جاري البحث...")

    if not query.startswith("http"):
        loop = asyncio.get_event_loop()
        try:
            info = await loop.run_in_executor(None, lambda: ytdl.extract_info(query, download=False))
        except Exception as e:
            await msg.edit(content=f"⚠️ حدث خطأ أثناء البحث: {e}")
            return

        if 'entries' not in info or len(info['entries']) == 0:
            await msg.edit(content="❌ لم يتم العثور على نتائج.")
            return

        entries = info['entries'][:5]
        desc = ""
        for i, entry in enumerate(entries, 1):
            desc += f"**{i}.** {entry.get('title')}\n"
        
        embed = discord.Embed(title="🔎 اختر رقم الأغنية (من 1 إلى 5):", description=desc, color=discord.Color.gold())
        await msg.edit(content=None, embed=embed)

        def check(m):
            return m.author == ctx.author and m.channel == ctx.channel and m.content.isdigit() and 1 <= int(m.content) <= len(entries)

        try:
            reply = await bot.wait_for('message', timeout=30.0, check=check)
            selected_index = int(reply.content) - 1
            url = entries[selected_index]['url']
        except asyncio.TimeoutError:
            await msg.edit(content="⏰ انتهى الوقت ولم تقم بالاختيار.")
            return
    else:
        url = query

    await msg.edit(content="🔍 جاري جلب البث المباشر...")

    async with ctx.typing():
        try:
            current_vol = guild_volumes.get(guild_id, 0.5)
            player = await YTDLSource.from_url(url, loop=bot.loop, volume=current_vol)
        except Exception as e:
            await msg.edit(content=f"⚠️ حدث خطأ أثناء جلب المقطع: {e}")
            return

        if guild_id not in queues:
            queues[guild_id] = []

        queues[guild_id].append(player)
        await msg.delete()

        if ctx.voice_client and ctx.voice_client.is_connected():
            if not ctx.voice_client.is_playing() and not ctx.voice_client.is_paused():
                await play_next(ctx)
            else:
                await ctx.send(f"➕ تمت الإضافة إلى قائمة الانتظار: **{player.title}**")

@bot.command(name="س", help="تخطي الأغنية الحالية")
async def skip(ctx):
    if ctx.voice_client and ctx.voice_client.is_playing():
        ctx.voice_client.stop()
        await ctx.send("⏭️ تم تخطي الأغنية الحالية.")
    else:
        await ctx.send("⚠️ لا يوجد شيء يتم تشغيله لتخطيه.")

@bot.command(name="ص", help="التحكم بمستوى الصوت مباشرة")
async def volume(ctx, vol: int):
    if not 0 <= vol <= 200:
        await ctx.send("⚠️ يرجى تحديد مستوى صوت بين `0` و `200`.")
        return
    
    guild_id = ctx.guild.id
    actual_vol = vol / 100.0
    guild_volumes[guild_id] = actual_vol
    
    if ctx.voice_client and ctx.voice_client.source:
        ctx.voice_client.source.volume = actual_vol
        
    await ctx.send(f"🔊 تم ضبط مستوى الصوت على: **{vol}%**")

@bot.command(name="قائمة", help="عرض الأغاني المنتظرة")
async def queue_list(ctx):
    guild_id = ctx.guild.id
    if guild_id not in queues or len(queues[guild_id]) == 0:
        await ctx.send("📋 قائمة الانتظار فارغة حالياً.")
        return

    desc = ""
    for i, song in enumerate(queues[guild_id][:10], 1):
        mins, secs = divmod(song.duration, 60)
        desc += f"**{i}.** {song.title} `({mins}:{secs:02d})`\n"
    
    embed = discord.Embed(title="🎶 قائمة الانتظار الحالية", description=desc, color=discord.Color.blue())
    await ctx.send(embed=embed)

@bot.command(name="كرر", help="تفعيل أو تعطيل التكرار")
async def loop(ctx):
    guild_id = ctx.guild.id
    is_looping[guild_id] = not is_looping.get(guild_id, False)
    status = "مفعلة 🔁" if is_looping[guild_id] else "معطلة ⏹️"
    await ctx.send(f"🔄 حالة التكرار أصبحت: **{status}**")

@bot.command(name="اخلط", help="خلط قائمة الانتظار عشوائياً")
async def shuffle(ctx):
    guild_id = ctx.guild.id
    if guild_id in queues and len(queues[guild_id]) > 1:
        random.shuffle(queues[guild_id])
        await ctx.send("🔀 تم خلط قائمة الانتظار بنجاح!")
    else:
        await ctx.send("⚠️ قائمة الانتظار قصيرة جداً للخلط.")

@bot.command(name="وقف", help="إيقاف البوت ومغادرة الروم")
async def stop(ctx):
    guild_id = ctx.guild.id
    if guild_id in queues:
        queues[guild_id].clear()
    if ctx.voice_client:
        await ctx.voice_client.disconnect()
        await ctx.send("👋 تم إيقاف البوت وخروجه من الروم.")

bot.run("MTU1Nzg0MDQzNTEzOTk3MzI0MQ.GOccIt.-UTJi04rG6UYZ1M8S2-56-PFmNxVlQiwcMzKkk")