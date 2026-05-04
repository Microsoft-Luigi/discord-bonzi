import discord
from discord.ext import commands
import wavelink


class Music(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # ---------------------------
    # Lavalink startup
    # ---------------------------
    @commands.Cog.listener()
    async def on_ready(self):
        if wavelink.Pool.nodes:
            return  # already connected

        node = wavelink.Node(
            uri="http://127.0.0.1:2333",
            password="youshallnotpass",
        )

        await wavelink.Pool.connect(
            client=self.bot,
            nodes=[node],
        )

        print("✅ Lavalink connected")

    # ---------------------------
    # Voice helpers
    # ---------------------------
    async def ensure_voice(self, ctx: commands.Context) -> wavelink.Player | None:
        if ctx.author.voice is None:
            await ctx.send("Join a voice channel first.")
            return None

        player: wavelink.Player = ctx.voice_client

        if not player:
            player = await ctx.author.voice.channel.connect(cls=wavelink.Player)
        elif player.channel != ctx.author.voice.channel:
            await player.move_to(ctx.author.voice.channel)

        return player

    # ---------------------------
    # Auto-play next track
    # ---------------------------
    @commands.Cog.listener()
    async def on_wavelink_track_end(self, payload: wavelink.TrackEndEventPayload):
        player = payload.player

        if not player.queue.is_empty:
            await player.play(player.queue.get())

    # ---------------------------
    # Commands
    # ---------------------------
    @commands.command()
    async def join(self, ctx: commands.Context):
        player = await self.ensure_voice(ctx)
        if player:
            await ctx.send(f"✅ Joined **{player.channel.name}**")

    @commands.command()
    async def leave(self, ctx: commands.Context):
        if ctx.voice_client:
            await ctx.voice_client.disconnect()
            await ctx.send("👋 Disconnected.")
        else:
            await ctx.send("I'm not connected.")

    @commands.command()
    async def play(self, ctx, *, query: str):
        player = await self.ensure_voice(ctx)
        if not player:
            return
    
        try:
            tracks = await wavelink.Playable.search(query)
        except wavelink.LavalinkLoadException:
            await ctx.send(
                "⚠️ Failed to load this track.\n"
                "YouTube is currently broken. Try SoundCloud or another link."
            )
            return
    
        if not tracks:
            await ctx.send("No results found.")
            return
    
        track = tracks[0]
        player.queue.put(track)
    
        if not player.playing and not player.paused:
            await player.play(player.queue.get())
            await ctx.send(f"▶️ Now playing: **{track.title}**")
        else:
            await ctx.send(f"➕ Queued: **{track.title}**")

    @commands.command(aliases=["q"])
    async def queue(self, ctx: commands.Context):
        player: wavelink.Player = ctx.voice_client
        if not player:
            await ctx.send("Not connected.")
            return

        if player.queue.is_empty:
            await ctx.send("📭 Queue is empty.")
            return

        items = list(player.queue)[:10]
        description = "\n".join(
            f"`{i+1}.` **{track.title}** — {track.length // 1000}s"
            for i, track in enumerate(items)
        )

        embed = discord.Embed(
            title="🎶 Queue",
            description=description,
        )

        await ctx.send(embed=embed)

    @commands.command()
    async def skip(self, ctx: commands.Context):
        player: wavelink.Player = ctx.voice_client
        if not player or not player.playing:
            await ctx.send("Nothing to skip.")
            return

        await player.stop()
        await ctx.send("⏭️ Skipped.")

    @commands.command()
    async def stop(self, ctx: commands.Context):
        player: wavelink.Player = ctx.voice_client
        if not player:
            await ctx.send("Not connected.")
            return

        player.queue.clear()
        await player.stop()
        await ctx.send("⏹️ Stopped and cleared queue.")

    @commands.command()
    async def pause(self, ctx: commands.Context):
        player: wavelink.Player = ctx.voice_client
        if player and player.playing:
            await player.pause()
            await ctx.send("⏸️ Paused.")
        else:
            await ctx.send("Nothing is playing.")

    @commands.command()
    async def resume(self, ctx: commands.Context):
        player: wavelink.Player = ctx.voice_client
        if player and player.paused:
            await player.resume()
            await ctx.send("▶️ Resumed.")
        else:
            await ctx.send("Not paused.")

    @commands.command()
    async def shuffle(self, ctx: commands.Context):
        player: wavelink.Player = ctx.voice_client
        if not player or len(player.queue) < 2:
            await ctx.send("Not enough tracks to shuffle.")
            return

        player.queue.shuffle()
        await ctx.send("🔀 Shuffled queue.")

    @commands.command()
    async def remove(self, ctx: commands.Context, index: int):
        player: wavelink.Player = ctx.voice_client
        if not player:
            await ctx.send("Not connected.")
            return

        if 1 <= index <= len(player.queue):
            removed = player.queue.remove(index - 1)
            await ctx.send(f"❌ Removed **{removed.title}**")
        else:
            await ctx.send("Index out of range.")

    @commands.command()
    async def clear(self, ctx: commands.Context):
        player: wavelink.Player = ctx.voice_client
        if not player:
            await ctx.send("Not connected.")
            return

        player.queue.clear()
        await ctx.send("🗑️ Queue cleared.")


async def setup(bot: commands.Bot):
    await bot.add_cog(Music(bot))