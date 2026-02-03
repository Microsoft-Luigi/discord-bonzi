# cogs/playerui.py
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Optional

import discord
from discord.ext import commands

# ---------- Small adapter so we can reuse Music helpers with interactions ----------
class _CtxAdapter:
    """Minimal adapter to call Music.build_resume_snapshot / resume_from_snapshot with an Interaction."""
    def __init__(self, bot: commands.Bot, guild: discord.Guild, user: discord.abc.User):
        self.bot = bot
        self.guild = guild
        self.author = user
        # match commands.Context attr name expected by your helper
        self.voice_client: Optional[discord.VoiceClient] = guild.voice_client


# ---------- The media controls view ----------
class MediaControls(discord.ui.View):
    def __init__(self, bot: commands.Bot, guild_id: int, *, timeout: float | None = None):
        # persistent view: keep the same custom_id values
        super().__init__(timeout=timeout)
        self.bot = bot
        self.guild_id = guild_id

    # Utility
    def _vc(self) -> Optional[discord.VoiceClient]:
        guild = self.bot.get_guild(self.guild_id)
        return guild.voice_client if guild else None

    def _music(self) -> Optional[commands.Cog]:
        return self.bot.get_cog("Music")

    async def _send_now_playing(self, interaction: discord.Interaction, *, edit_message: bool = True):
        """Build a simple Now Playing embed using the Music cog’s state."""
        music = self._music()
        guild = interaction.guild
        if not (music and guild):
            content = "Music cog not available."
            if interaction.response.is_done():
                await interaction.followup.send(content, ephemeral=True)
            else:
                await interaction.response.send_message(content, ephemeral=True)
            return

        # Access Music internal state safely
        meta = getattr(music, "_pb", {}).get(guild.id)
        if not meta:
            desc = "Nothing is playing."
        else:
            title = meta.get("title") or "Unknown title"
            duration = meta.get("duration")
            # Compute elapsed based on started_at/paused_at/seek_base (mirrors your snapshot logic)
            started_at = meta.get("started_at")
            paused_at = meta.get("paused_at")
            seek_base = float(meta.get("seek_base", 0.0))
            if started_at is None:
                elapsed = 0.0
            else:
                if paused_at is not None:
                    elapsed = max(0.0, paused_at - started_at)
                else:
                    import time
                    elapsed = max(0.0, time.monotonic() - started_at)
                elapsed += seek_base

            def fmt(t: Optional[float]) -> str:
                if t is None:
                    return "LIVE/Unknown"
                t = int(max(0, t))
                m, s = divmod(t, 60)
                h, m = divmod(m, 60)
                return f"{h:d}:{m:02d}:{s:02d}" if h else f"{m:d}:{s:02d}"

            if duration is None:
                desc = f"**{title}**\n`{fmt(elapsed)} / LIVE`"
            else:
                desc = f"**{title}**\n`{fmt(elapsed)} / {fmt(float(duration))}`"

        embed = discord.Embed(title="Now Playing", description=desc, color=discord.Color.blurple())

        if edit_message:
            try:
                await interaction.message.edit(embed=embed, view=self)
                if not interaction.response.is_done():
                    await interaction.response.defer()  # avoid 'already responded' errors
            except Exception:
                # Fallback: send ephemeral update
                if interaction.response.is_done():
                    await interaction.followup.send(embed=embed, ephemeral=True)
                else:
                    await interaction.response.send_message(embed=embed, ephemeral=True)
        else:
            if interaction.response.is_done():
                await interaction.followup.send(embed=embed, ephemeral=True)
            else:
                await interaction.response.send_message(embed=embed, ephemeral=True)

    # ------------- Buttons -------------

    @discord.ui.button(label="⏪ 10s", style=discord.ButtonStyle.secondary, custom_id="controls:rewind")
    async def rewind(self, interaction: discord.Interaction, button: discord.ui.Button):
        vc = self._vc()
        music = self._music()
        if not (vc and music):
            return await interaction.response.send_message("Not connected.", ephemeral=True)

        ctx_like = _CtxAdapter(self.bot, interaction.guild, interaction.user)
        snap = music.build_resume_snapshot(ctx_like)
        if not snap or not snap.get("resumable", False):
            return await interaction.response.send_message("Cannot seek for this source (live/HLS or unknown).", ephemeral=True)

        new_offset = max(0.0, float(snap["offset"]) - 10.0)
        snap["offset"] = new_offset
        # Stop current pipeline and resume at new offset
        if vc.is_playing() or vc.is_paused():
            vc.stop()
        await music.resume_from_snapshot(ctx_like, snap)
        await interaction.response.defer(ephemeral=True)
        await self._send_now_playing(interaction)

    @discord.ui.button(label="⏯ Play/Pause", style=discord.ButtonStyle.primary, custom_id="controls:toggle")
    async def toggle(self, interaction: discord.Interaction, button: discord.ui.Button):
        vc = self._vc()
        if not vc:
            return await interaction.response.send_message("Not connected.", ephemeral=True)
        # Toggle state
        if vc.is_playing():
            vc.pause()
            # mark paused_at in Music meta so future snapshots are correct
            music = self._music()
            meta = getattr(music, "_pb", {}).get(self.guild_id) if music else None
            if meta is not None:
                import time
                meta["paused_at"] = time.monotonic()
            await interaction.response.send_message("⏸️ Paused.", ephemeral=True)
        elif vc.is_paused():
            # adjust anchors same as your resume command
            music = self._music()
            if music:
                meta = getattr(music, "_pb", {}).get(self.guild_id)
                now = __import__("time").monotonic()
                if meta and meta.get("paused_at") is not None and meta.get("started_at") is not None:
                    paused_at = float(meta["paused_at"])
                    started_at = float(meta["started_at"])
                    already = max(0.0, paused_at - started_at)
                    meta["seek_base"] = float(meta.get("seek_base", 0.0)) + already
                    meta["started_at"] = now
                    meta["paused_at"] = None
            vc.resume()
            await interaction.response.send_message("▶️ Resumed.", ephemeral=True)
        else:
            await interaction.response.send_message("Nothing to toggle.", ephemeral=True)
        # Update panel embed
        try:
            await self._send_now_playing(interaction)
        except Exception:
            pass

    @discord.ui.button(label="⏹ Stop", style=discord.ButtonStyle.danger, custom_id="controls:stop")
    async def stop(self, interaction: discord.Interaction, button: discord.ui.Button):
        vc = self._vc()
        if not vc:
            return await interaction.response.send_message("Not connected.", ephemeral=True)
        if vc.is_playing() or vc.is_paused():
            vc.stop()
            await interaction.response.send_message("⏹️ Stopped.", ephemeral=True)
        else:
            await interaction.response.send_message("Nothing to stop.", ephemeral=True)
        await self._send_now_playing(interaction)

    @discord.ui.button(label="⏭ Skip", style=discord.ButtonStyle.secondary, custom_id="controls:skip")
    async def skip(self, interaction: discord.Interaction, button: discord.ui.Button):
        vc = self._vc()
        if not vc:
            return await interaction.response.send_message("Not connected.", ephemeral=True)
        if vc.is_playing() or vc.is_paused():
            vc.stop()  # with no queue, this just stops
            await interaction.response.send_message("⏭️ Skipped.", ephemeral=True)
        else:
            await interaction.response.send_message("Nothing to skip.", ephemeral=True)
        await self._send_now_playing(interaction)

    @discord.ui.button(label="⏩ 10s", style=discord.ButtonStyle.secondary, custom_id="controls:forward")
    async def forward(self, interaction: discord.Interaction, button: discord.ui.Button):
        vc = self._vc()
        music = self._music()
        if not (vc and music):
            return await interaction.response.send_message("Not connected.", ephemeral=True)

        ctx_like = _CtxAdapter(self.bot, interaction.guild, interaction.user)
        snap = music.build_resume_snapshot(ctx_like)
        if not snap or not snap.get("resumable", False):
            return await interaction.response.send_message("Cannot seek for this source (live/HLS or unknown).", ephemeral=True)

        duration = snap.get("duration")
        new_offset = float(snap["offset"]) + 10.0
        if duration is not None:
            new_offset = min(new_offset, float(duration) - 0.25)
        snap["offset"] = max(0.0, new_offset)

        if vc.is_playing() or vc.is_paused():
            vc.stop()
        await music.resume_from_snapshot(ctx_like, snap)
        await interaction.response.defer(ephemeral=True)
        await self._send_now_playing(interaction)

    @discord.ui.button(label="🔄 Refresh", style=discord.ButtonStyle.secondary, custom_id="controls:refresh")
    async def refresh(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._send_now_playing(interaction, edit_message=True)

    @discord.ui.button(label="📜 Queue", style=discord.ButtonStyle.secondary, custom_id="controls:queue")
    async def show_queue(self, interaction: discord.Interaction, button: discord.ui.Button):
        music: commands.Cog | None = self._music()
        if not music:
            return await interaction.response.send_message("Music cog not available.", ephemeral=True)

        q = getattr(music, "get_queue_copy")(self.guild_id)
        if not q:
            return await interaction.response.send_message("📭 Queue is empty.", ephemeral=True)

        # Build a neat queue embed (top 10)
        lines = []
        max_show = 10
        for i, t in enumerate(q[:max_show], start=1):
            dur = getattr(music, "_fmt_time")(t.duration)
            lines.append(f"`{i:>2}.` **{t.title}** — `{dur}`")
        more = len(q) - max_show
        desc = "\n".join(lines) + (f"\n… and `{more}` more." if more > 0 else "")
        embed = discord.Embed(title="Upcoming Queue", description=desc, color=discord.Color.blurple())

        await interaction.response.send_message(embed=embed, ephemeral=True)


# cogs/playerui.py (only the commands section shown)

class PlayerUI(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._persistent_loaded = False

    @commands.Cog.listener()
    async def on_ready(self):
        self._persistent_loaded = True

    # ❗ Remove any @commands.command(name="panel") you had before

    @commands.hybrid_command(
        name="musicpanel",                 # <— unique name avoids collisions
        description="Open the media player controls panel.",
        aliases=["mpanel"]                 # optional prefix aliases (don't use 'panel' here)
    )
    async def musicpanel(self, ctx: commands.Context):
        """Open the player controls panel (buttons)."""
        if ctx.guild is None:
            return await ctx.reply("Use this in a server.")

        view = MediaControls(self.bot, ctx.guild.id)
        # (Optional) Show a quick embed (will be refreshed by buttons)
        embed = discord.Embed(title="Now Playing", description="(initializing…)", color=discord.Color.blurple())
        await ctx.reply(embed=embed, view=view)


async def setup(client: commands.Bot):
    await client.add_cog(PlayerUI(client))