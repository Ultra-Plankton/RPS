import os
import discord
import asyncio
import random
import logging
import requests 
from discord import app_commands
from discord.ext import commands
from keep_alive import keep_alive
from dotenv import load_dotenv

load_dotenv()
logging.basicConfig(level=logging.INFO)

TOKEN = os.getenv("DISCORD_TOKEN")
if not TOKEN:
    raise RuntimeError("DISCORD_TOKEN env var not set")

keep_alive()

# Intents
intents = discord.Intents.default()
intents.messages = True
intents.message_content = True
intents.dm_messages = True
intents.reactions = True

# Initialize bot
bot = commands.Bot(command_prefix="!", intents=intents)

def is_guild_admin(interaction: discord.Interaction) -> bool:
    # must be used in a guild context
    return interaction.user.guild_permissions.administrator

EMOJI_TO_MOVE = {
    "🪨": "rock",
    "📄": "paper",
    "✂️": "scissors"
}
EMOJIS = list(EMOJI_TO_MOVE.keys())

def determine_winner(move1, move2):
    if move1 == move2:
        return 0
    if (move1 == "rock" and move2 == "scissors") or \
       (move1 == "scissors" and move2 == "paper") or \
       (move1 == "paper" and move2 == "rock"):
        return 1
    return 2

@bot.event
async def on_ready():
    logging.info(f"✅ Logged in as {bot.user}")
    try:
        synced = await bot.tree.sync()
        logging.info(f"✅ Synced {len(synced)} command(s).")
    except Exception as e:
        logging.error(f"❌ Error syncing commands: {e}")

@bot.tree.command(name="rps", description="Start a Rock Paper Scissors game between two users.")
@app_commands.describe(
    player1="Away Team player",
    player2="Home Team player",
    wins="Number of wins required to win the match",
    desc="Short description (e.g. 'Week 1 Game 1')"
)
async def rps(interaction: discord.Interaction, player1: discord.User, player2: discord.User, wins: int, desc: str = ""):

    if player1.bot or player2.bot:
        await interaction.response.send_message("You can't include bots as players!", ephemeral=True)
        return

    await interaction.response.send_message(
        f"🎮 A Rock Paper Scissors match has started!\n"
        f"**Away Team:** {player1.mention}\n**Home Team:** {player2.mention}\n"
        f"First to {wins} wins."
    )

    score = {player1.id: 0, player2.id: 0}
    round_num = 1

    while score[player1.id] < wins and score[player2.id] < wins:
        messages = {}
        for player in [player1, player2]:
            try:
                dm = await player.create_dm()
                message = await dm.send(f"**Round {round_num}**: React with your move (🪨, 📄, ✂️). You have 4 hours.")
                for emoji in EMOJIS:
                    await message.add_reaction(emoji)
                messages[player.id] = message
            except discord.Forbidden:
                await interaction.followup.send(f"Couldn't DM {player.mention}. Game cancelled.", ephemeral=True)
                return

        moves = {}
        response_event = asyncio.Event()

        async def wait_for_move(player, message):
            def check(payload: discord.RawReactionActionEvent):
                return (
                    payload.user_id == player.id and
                    payload.message_id == message.id and
                    str(payload.emoji.name) in EMOJI_TO_MOVE
                )

            try:
                payload = await bot.wait_for("raw_reaction_add", timeout=14400, check=check)
                moves[player.id] = EMOJI_TO_MOVE[str(payload.emoji.name)]
                if len(moves) == 2:
                    response_event.set()
            except asyncio.TimeoutError:
                moves[player.id] = None
                response_event.set()

        tasks = [
            asyncio.create_task(wait_for_move(player1, messages[player1.id])),
            asyncio.create_task(wait_for_move(player2, messages[player2.id]))
        ]

        try:
            await asyncio.wait_for(response_event.wait(), timeout=14400)
        except asyncio.TimeoutError:
            pass

        await asyncio.gather(*tasks)

        for msg in messages.values():
            try:
                await msg.delete()
            except discord.HTTPException:
                pass

        move1 = moves.get(player1.id)
        move2 = moves.get(player2.id)

        if move1 is None or move2 is None:
            for player in [player1, player2]:
                try:
                    await player.send("Game cancelled due to timeout.")
                except:
                    pass
            await interaction.followup.send("Game cancelled due to player inactivity.")
            return

        winner = determine_winner(move1, move2)
        ties = score.get("ties", 0)
        if winner == 0:
            ties += 1
        elif winner == 1:
            score[player1.id] += 1
        else:
            score[player2.id] += 1
        score["ties"] = ties

        def format_score(final=False):
            header = f"**{desc}**\n------------\n" if desc else ""
            base = (
                f"{header}**{player1.mention} (Away) vs {player2.mention} (Home)**\n"
                f"{score[player1.id]} win(s) - {score[player2.id]} win(s) - {score['ties']} tie(s)"
            )
            if final:
                overall_winner = player1 if score[player1.id] == wins else player2
                base += f"\n🎉 **{overall_winner.mention} won the game!**"
            return base

        if round_num == 1:
            scoreboard_message = await interaction.followup.send(format_score())
            await player1.send(format_score())
            await player2.send(format_score())
        else:
            await scoreboard_message.edit(content=format_score())
            await player1.send(format_score())
            await player2.send(format_score())

        round_num += 1

    overall_winner = player1 if score[player1.id] == wins else player2
    final_message = format_score(final=True)
    await scoreboard_message.edit(content=final_message)
    await player1.send(final_message)
    await player2.send(final_message)

@bot.tree.command(
    name="update",
    description="Pull latest from GitHub and redeploy on Render"

@app_commands.check(is_guild_admin)
async def update(interaction: discord.Interaction):
    hook_url = os.getenv("RENDER_DEPLOY_HOOK_URL")
    if not hook_url:
        return await interaction.response.send_message(
            "🚨 Render hook URL not configured!",
            ephemeral=True
        )

    try:
        resp = requests.post(hook_url, timeout=10)
    except Exception as e:
        return await interaction.response.send_message(
            f"❌ Error: {e}",
            ephemeral=True
        )

    if 200 <= resp.status_code < 300:
        await interaction.response.send_message(
            "✅ Redeploy triggered on Render!",
            ephemeral=True
        )
    else:
        await interaction.response.send_message(
            f"❌ Failed (HTTP {resp.status_code})",
            ephemeral=True
        )

bot.run(TOKEN)
