import os
import discord
import asyncio
import random
import logging
import requests
from discord import app_commands, Member, ui
from discord import Message
from discord.ext import commands
from keep_alive import keep_alive
from dotenv import load_dotenv
from typing import cast, Optional

load_dotenv()
logging.basicConfig(level=logging.INFO)

TOKEN = os.getenv("DISCORD_TOKEN")
if not TOKEN:
    raise RuntimeError("DISCORD_TOKEN env var not set")

keep_alive()

# Intents
intents = discord.Intents.default()
intents.message_content = True
intents.dm_messages = True
intents.reactions = True

# Initialize bot
bot = commands.Bot(command_prefix="!", intents=intents)

async def send_to_channel(interaction: discord.Interaction, content: str) -> discord.Message:
    """Safely send a message to the interaction's channel with fallbacks"""
    # First try sending directly to a text channel or thread
    if interaction.channel and isinstance(interaction.channel, (discord.TextChannel, discord.Thread)):
        return await interaction.channel.send(content)
    
    # If we haven't responded yet, defer first
    if not interaction.response.is_done():
        await interaction.response.defer(thinking=True)
    
    # Use followup with proper type handling
    followup_msg = await interaction.followup.send(content)
    if followup_msg is None:
        # Final fallback - try to get channel from original response
        try:
            if interaction.response.is_done():
                msg = await interaction.original_response()
                if isinstance(msg.channel, (discord.TextChannel, discord.Thread)):
                    return await msg.channel.send(content)
        except Exception as e:
            logging.error(f"Failed to send message: {e}")
    
    if followup_msg is None:
        raise RuntimeError("All message sending methods failed")
    return followup_msg

def is_guild_admin(interaction: discord.Interaction) -> bool:
    guild = interaction.guild
    if guild is None:
        return False
    member = cast(Member, interaction.user)
    return member.guild_permissions.administrator

EMOJI_TO_MOVE = {
    "🪨": "rock",
    "📄": "paper",
    "✂️": "scissors"
}
EMOJIS = list(EMOJI_TO_MOVE.keys())

class RPSView(ui.View):
    def __init__(self, player: discord.User):
        super().__init__(timeout=14400)
        self.player = player
        self.choice = None
    
    @ui.button(emoji="🪨", style=discord.ButtonStyle.secondary)
    async def rock(self, interaction: discord.Interaction, button: ui.Button):
        await self.handle_choice(interaction, "rock")
    
    @ui.button(emoji="📄", style=discord.ButtonStyle.secondary)
    async def paper(self, interaction: discord.Interaction, button: ui.Button):
        await self.handle_choice(interaction, "paper")
    
    @ui.button(emoji="✂️", style=discord.ButtonStyle.secondary)
    async def scissors(self, interaction: discord.Interaction, button: ui.Button):
        await self.handle_choice(interaction, "scissors")
    
    async def handle_choice(self, interaction: discord.Interaction, choice: str):
        if interaction.user.id != self.player.id:
            return await interaction.response.send_message("This isn't your game!", ephemeral=True)
        self.choice = choice
        await interaction.response.send_message(f"You chose {choice}!", ephemeral=True)
        self.stop()

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
    # Validation
    if player1.bot or player2.bot:
        return await interaction.response.send_message(
            "You can't include bots as players!", ephemeral=True
        )
    if wins < 1 or wins > 10:
        return await interaction.response.send_message(
            "Please choose a number of wins between 1 and 10", ephemeral=True
        )

    # Announce match
    await interaction.response.send_message(
        f"🎮 **RPS Match Started!**\n"
        f"Away: {player1.mention}  vs  Home: {player2.mention}\n"
        f"First to {wins} wins, or first to 7 total ties.\n"
        f"{f'**Match:** {desc}' if desc else ''}"
    )

    # Initialize tracking
    score = {player1.id: 0, player2.id: 0, "ties": 0}
    move_history = {player1.id: [], player2.id: []}  # Stores all moves (e.g., ["✊", "✋", "✌️"])
    last_move = {player1.id: "❔", player2.id: "❔"}
    round_num = 1
    scoreboard_message: Optional[discord.Message] = None

    async def make_summary(final=False) -> str:
        header = f"**{desc}**\n" if desc else ""
        moves_text = (
            f"**Moves:**\n"
    f"{player1.mention}: {', '.join(move_history[player1.id])}\n"
    f"{player2.mention}: {', '.join(move_history[player2.id])}\n\n"
        )
        score_text = (
            f"**Score:** {player1.mention}: {score[player1.id]} | "
            f"{player2.mention}: {score[player2.id]} | "
            f"Ties: {score['ties']}\n\n"
        )
        
        base = f"{header}{moves_text}{score_text}{result_text}"
        
        if final:
            if score["ties"] >= 7:  # If ties reached 7, it's an automatic draw
                base += "\n\n🤝 **Match ends in a draw due to too many ties!**"
            elif score[player1.id] > score[player2.id]:
                base += f"\n\n🎉 **{player1.mention} wins the match!**"
            elif score[player2.id] > score[player1.id]:
                base += f"\n\n🎉 **{player2.mention} wins the match!**"
            else:
                base += "\n\n🤝 **Match ends in a draw!**"
        return base

    # Main game loop
    while True:
        # Check win conditions
        if score[player1.id] >= wins or score[player2.id] >= wins or score["ties"] >= 7:
            break

        # Get player moves
        moves = {}
        view1 = RPSView(player1)
        view2 = RPSView(player2)
        
        # Send move requests
        try:
            dm1 = await player1.create_dm()
            dm_msg1 = await dm1.send(f"**Round {round_num}:** Select your move:", view=view1)
            
            dm2 = await player2.create_dm()
            dm_msg2 = await dm2.send(f"**Round {round_num}:** Select your move:", view=view2)
        except discord.Forbidden:
            await interaction.followup.send(
                f"⚠️ Couldn't DM players. Please enable DMs from server members.",
                ephemeral=True
            )
            return

        # Wait for moves
        try:
            await asyncio.wait_for(
                asyncio.gather(view1.wait(), view2.wait()),
                timeout=14400
            )
        except asyncio.TimeoutError:
            moves[player1.id] = None
            moves[player2.id] = None

        # Record moves
        moves[player1.id] = view1.choice
        moves[player2.id] = view2.choice
        
        # Update last move display
        for pid, move in moves.items():
            emoji = next(
                (emoji for emoji, name in EMOJI_TO_MOVE.items() if name == move),
                "❌" if move is None else "❔"
            )
            move_history[pid].append(emoji)  # Add to history
            last_move[pid] = emoji  # Still track latest move for round results

        # Determine round result
        m1, m2 = moves[player1.id], moves[player2.id]
        if m1 is None and m2 is None:
            result_text = "Both players timed out - round void."
        elif m1 is None:
            score[player2.id] += 1
            result_text = f"{player2.mention} wins by forfeit (timeout)."
        elif m2 is None:
            score[player1.id] += 1
            result_text = f"{player1.mention} wins by forfeit (timeout)."
        else:
            winner = determine_winner(m1, m2)
            if winner == 1:
                score[player1.id] += 1
                result_text = f"{player1.mention} wins the round!"
            elif winner == 2:
                score[player2.id] += 1
                result_text = f"{player2.mention} wins the round!"
            else:
                score["ties"] += 1
                result_text = "Round is a tie."

        # Update scoreboard with proper message handling
        summary = await make_summary()
        try:
            if scoreboard_message is None:
                scoreboard_message = await send_to_channel(interaction, summary)
            else:
                await scoreboard_message.edit(content=summary)
        except (discord.NotFound, discord.HTTPException):
            # If message was deleted or edit failed, send new one
            scoreboard_message = await send_to_channel(interaction, summary)

        # Send updates to players
        for p in (player1, player2):
            try:
                dm = await p.create_dm()
                await dm.send(
                    f"**Round {round_num} Update**\n"
                    f"{summary}\n\n"
                    f"Next round starting soon..."
                )
            except discord.Forbidden:
                continue

        round_num += 1

    # Final update with error handling
    final_summary = await make_summary(final=True)
    try:
        if scoreboard_message is not None:
            await scoreboard_message.edit(content=final_summary)
        else:
            scoreboard_message = await send_to_channel(interaction, final_summary)
    except (discord.NotFound, discord.HTTPException):
        scoreboard_message = await send_to_channel(interaction, final_summary)
    
    # Notify players of final result
    for p in (player1, player2):
        try:
            dm = await p.create_dm()
            await dm.send(f"**Match Complete!**\n{final_summary}")
        except discord.Forbidden:
            continue

@bot.tree.command(name="update", description="Pull latest from GitHub and redeploy on Render")
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

@bot.tree.command(name="ping", description="Check if the bot is up and see its latency.")
async def ping(interaction: discord.Interaction):
    latency_ms = round(bot.latency * 1000)
    await interaction.response.send_message(f"Pong! 🏓 Latency: {latency_ms}ms", ephemeral=True)

bot.run(TOKEN)