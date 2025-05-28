import os
import discord
import asyncio
import random
import logging
import requests 
from discord import app_commands, Member
from discord import Message
from discord.ext import commands
from keep_alive import keep_alive
from dotenv import load_dotenv
from typing import cast

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
    guild = interaction.guild
    if guild is None:
        return False
    member = cast(Member, interaction.user)
    return member.guild_permissions.administrator
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
async def rps(
    interaction: discord.Interaction,
    player1: discord.User,
    player2: discord.User,
    wins: int,
    desc: str = ""
):
    if player1.bot or player2.bot:
        return await interaction.response.send_message("You can't include bots as players!", ephemeral=True)

    await interaction.response.send_message(
        f"🎮 A Rock Paper Scissors match has started!\n"
        f"**Away Team:** {player1.mention}\n**Home Team:** {player2.mention}\n"
        f"First to {wins} wins, or first to 7 total ties ends in a draw."
    )

    # Initialize scores and last-move emojis
    score: dict[int|str, int] = {
        player1.id: 0,
        player2.id: 0,
        "ties": 0,
    }
    last_move_emoji: dict[int, str] = {}
    round_num = 1
    scoreboard_message: discord.Message | None = None

    while True:
        # Check for match-end conditions
        if score[player1.id] >= wins or score[player2.id] >= wins:
            break
        if score["ties"] >= 7:
            # overall draw
            break

        # Send DM prompts
        messages = {}
        for p in (player1, player2):
            try:
                dm = await p.create_dm()
                msg = await dm.send(f"**Round {round_num}**: React with 🪨 📄 ✂️ within 4 hours.")
                for e in EMOJIS:
                    await msg.add_reaction(e)
                messages[p.id] = msg
            except discord.Forbidden:
                return await interaction.followup.send(f"Couldn't DM {p.mention}. Game cancelled.", ephemeral=True)

        # Wait for reactions or timeout
        moves: dict[int, str|None] = {}
        event = asyncio.Event()

        async def wait_move(p: discord.User, msg: discord.Message):
            def check(payload: discord.RawReactionActionEvent):
                return (payload.user_id == p.id
                        and payload.message_id == msg.id
                        and str(payload.emoji.name) in EMOJI_TO_MOVE)
            try:
                payload = await bot.wait_for("raw_reaction_add", timeout=14400, check=check)
                moves[p.id] = EMOJI_TO_MOVE[str(payload.emoji.name)]
            except asyncio.TimeoutError:
                moves[p.id] = None
            finally:
                # Once both keys exist, trigger
                if len(moves) == 2:
                    event.set()

        tasks = [
            asyncio.create_task(wait_move(player1, messages[player1.id])),
            asyncio.create_task(wait_move(player2, messages[player2.id]))
        ]
        await event.wait()
        await asyncio.gather(*tasks, return_exceptions=True)

        # Clean up DMs
        for m in messages.values():
            try: await m.delete()
            except: pass

        # Resolve round
        move1 = moves.get(player1.id)
        move2 = moves.get(player2.id)
        # Record emojis (use fallback if timed out)
        last_move_emoji[player1.id] = next((emo for emo,mv in EMOJI_TO_MOVE.items() if mv==move1), "❌")  # ❌ = no move
        last_move_emoji[player2.id] = next((emo for emo,mv in EMOJI_TO_MOVE.items() if mv==move2), "❌")

        # Determine round outcome
        if move1 is None and move2 is None:
            # Both timed out → round void
            outcome_text = "Both players timed out—round void."
        elif move1 is None:
            score[player2.id] += 1
            outcome_text = f"{player2.mention} wins by forfeit (🕒)."
        elif move2 is None:
            score[player1.id] += 1
            outcome_text = f"{player1.mention} wins by forfeit (🕒)."
        else:
            winner = determine_winner(move1, move2)
            if winner == 1:
                score[player1.id] += 1
                outcome_text = f"{player1.mention} wins the round!"
            elif winner == 2:
                score[player2.id] += 1
                outcome_text = f"{player2.mention} wins the round!"
            else:
                score["ties"] += 1
                outcome_text = "Round is a tie."

        # Build the scoreboard text (with last-move emojis)
        def format_score(final=False):
            header = f"**{desc}**\n----\n" if desc else ""
            moves_line = (
                f"{EMOJIS[0]} {last_move_emoji[player1.id]}   "
                f"{EMOJIS[1]} {last_move_emoji[player2.id]}"
            )
            base = (
                f"{header}"
                f"**{player1.mention} (Away):** {score[player1.id]}  "
                f"**{player2.mention} (Home):** {score[player2.id]}  "
                f"**Ties:** {score['ties']}\n"
                f"{moves_line}\n"
                f"{outcome_text}"
            )
            if final:
                if score[player1.id] > score[player2.id]:
                    champ = player1
                elif score[player2.id] > score[player1.id]:
                    champ = player2
                else:
                    champ = None
                if champ:
                    base += f"\n🎉 **{champ.mention} won the match!**"
                else:
                    base += "\n🤝 **Match ends in a draw!**"
            return base

        # Send or edit the scoreboard
        if round_num == 1:
            scoreboard_message = await interaction.followup.send(format_score())
        else:
            # safe-guard
            if scoreboard_message is None:
                raise RuntimeError("scoreboard_message not set")
            await scoreboard_message.edit(content=format_score())

        round_num += 1

    # Final score update
    if scoreboard_message is None:
        raise RuntimeError("scoreboard_message not set")
    await scoreboard_message.edit(content=format_score(final=True))

@bot.tree.command(
    name="update",
    description="Pull latest from GitHub and redeploy on Render"
)
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
