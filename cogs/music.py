import asyncio
import random
import wavelink
from discord.ext import commands
import discord


class LavalinkPlayer(wavelink.Player):
    """Custom player to store queue + metadata."""
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.queue = asyncio.Queue()
        self.current = None

    async def play_next(self):
        if self.is_playing() or self.is_paused():
            return

        try:
            track = self.queue.get_nowait()
        except asyncio.QueueEmpty:
            self.current_track = None
            return

        self.current = track
        await self.play(track)
        

class Music(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

        # Start Lavalink node when bot is ready
        bot.loop.create_task(self.start_lavalink())

    async def start_lavalink(self):
        await self.bot.wait_until_ready()

        # Create Wavelink client if not already created
        if not hasattr(self.bot, "wavelink"):
            self.bot.wavelink = wavelink.Client(self.bot)

        node = wavelink.Node(
            uri="http://localhost:2333",
            password="youshallnotpass",
            secure=False,
        )

        await self.bot.wavelink.initiate_node(node)
        print("Lavalink node connected.")

    # ---------------------------
    # Voice helpers
    # ---------------------------
    async def ensure_voice(self, ctx):
        if ctx.author.voice is None:
            await ctx.send("Join a voice channel first.")
            return None

        vc: LavalinkPlayer = ctx.voice_client

        if vc is None:
            vc = await ctx.author.voice.channel.connect(cls=LavalinkPlayer)
        elif vc.channel != ctx.author.voice.channel:
            await vc.move_to(ctx.author.voice.channel)

        return vc

    # ---------------------------
    # Commands
    # ---------------------------
    @commands.command()
    async def join(self, ctx):
        vc = await self.ensure_voice(ctx)
        if vc:
            await ctx.send(f"Joined **{vc.channel.name}**")

    @commands.command()
    async def leave(self, ctx):
        vc = ctx.voice_client
        if vc:
            await vc.disconnect()
            await ctx.send("Left the voice channel.")
        else:
            await ctx.send("I'm not connected.")

    @commands.command()
    async def play(self, ctx, *, query: str):
        vc: LavalinkPlayer = await self.ensure_voice(ctx)
        if vc is None:
            return

        # Search YouTube (Lavalink handles extraction)
        tracks = await wavelink.YouTubeTrack.search(query=query)

        if not tracks:
            await ctx.send("No results found.")
            return

        track = tracks[0]
        await vc.queue.put(track)

        if not vc.is_playing() and not vc.is_paused():
            await vc.play_next()
            await ctx.send(f"▶️ Now playing: **{track.title}**")
        else:
            await ctx.send(f"➕ Queued: **{track.title}**")

    @commands.command(aliases=["q"])
    async def queue(self, ctx):
        vc: LavalinkPlayer = ctx.voice_client
        if vc is None:
            await ctx.send("Not connected.")
            return

        if vc.queue.empty():
            await ctx.send("📭 Queue is empty.")
            return

        items = list(vc.queue._queue)
        desc = "\n".join(
            f"`{i+1}.` **{t.title}** — {t.length // 1000}s"
            for i, t in enumerate(items[:10])
        )

        if len(items) > 10:
            desc += f"\n… and `{len(items) - 10}` more."

        embed = discord.Embed(title="Upcoming Queue", description=desc)
        await ctx.send(embed=embed)

    @commands.command()
    async def skip(self, ctx):
        vc: LavalinkPlayer = ctx.voice_client
        if vc is None:
            await ctx.send("Not connected.")
            return

        await vc.stop()
        await vc.play_next()
        await ctx.send("⏭️ Skipped.")

    @commands.command()
    async def stop(self, ctx):
        vc: LavalinkPlayer = ctx.voice_client
        if vc is None:
            await ctx.send("Not connected.")
            return

        vc.queue = asyncio.Queue()
        await vc.stop()
        await ctx.send("⏹️ Stopped and cleared queue.")

    @commands.command()
    async def pause(self, ctx):
        vc: LavalinkPlayer = ctx.voice_client
        if vc and vc.is_playing():
            await vc.pause()
            await ctx.send("⏸️ Paused.")
        else:
            await ctx.send("Nothing is playing.")

    @commands.command()
    async def resume(self, ctx):
        vc: LavalinkPlayer = ctx.voice_client
        if vc and vc.is_paused():
            await vc.resume()
            await ctx.send("▶️ Resumed.")
        else:
            await ctx.send("Not paused.")

    @commands.command()
    async def shuffle(self, ctx):
        vc: LavalinkPlayer = ctx.voice_client
        if vc is None:
            await ctx.send("Not connected.")
            return

        items = list(vc.queue._queue)
        if len(items) < 2:
            await ctx.send("Not enough items to shuffle.")
            return

        random.shuffle(items)
        vc.queue._queue = asyncio.collections.deque(items)
        await ctx.send("🔀 Shuffled queue.")

    @commands.command()
    async def remove(self, ctx, index: int):
        vc: LavalinkPlayer = ctx.voice_client
        if vc is None:
            await ctx.send("Not connected.")
            return

        items = list(vc.queue._queue)
        if 1 <= index <= len(items):
            removed = items.pop(index - 1)
            vc.queue._queue = asyncio.collections.deque(items)
            await ctx.send(f"❌ Removed: **{removed.title}**")
        else:
            await ctx.send("Index out of range.")

    @commands.command()
    async def clear(self, ctx):
        vc: LavalinkPlayer = ctx.voice_client
        if vc is None:
            await ctx.send("Not connected.")
            return

        vc.queue = asyncio.Queue()
        await ctx.send("🗑️ Cleared queue.")


async def setup(bot):
    await bot.add_cog(Music(bot))
