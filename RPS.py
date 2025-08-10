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
from datetime import datetime

load_dotenv()
logging.basicConfig(level=logging.INFO)

TOKEN = os.getenv("DISCORD_TOKEN")
if not TOKEN:
    raise RuntimeError("DISCORD_TOKEN env var not set")

active_matches = {}  # Format: {channel_id: {"interaction": interaction_obj, "players": [id1, id2], "cancelled": False}}

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
    desc="Short description (e.g. 'Week 1 Game 1')",
    target_channel="Channel where the match will take place"
)
async def rps(
    interaction: discord.Interaction,
    player1: discord.User,
    player2: discord.User,
    wins: int,
    target_channel: discord.TextChannel,
    desc: str = ""
):
    # Only allow match creation in rps-start channel
    RPS_START_CHANNEL_ID = 1403628570013601893  # Replace with your rps-start channel ID
    if not interaction.channel or interaction.channel.id != RPS_START_CHANNEL_ID:
        return await interaction.response.send_message(
            "You can only start matches in the rps-start channel.", ephemeral=True
        )

    # Restriction: Only admins can start games in certain channels
    RESTRICTED_CHANNEL_IDS = [
        1403628570013601893,
        1403628687940784148,
        1403630668109320283,
        1403628617346322492,
        1403629262715617321
    ]
    if interaction.channel and interaction.channel.id in RESTRICTED_CHANNEL_IDS:
        member = None
        if interaction.guild:
            member = interaction.guild.get_member(interaction.user.id)
        if not member or not member.guild_permissions.administrator:
            return await interaction.response.send_message(
                "You must have Administrator permission to start games in this channel.", ephemeral=True
            )

    # Validation
    if player1.bot or player2.bot:
        return await interaction.response.send_message(
            "You can't include bots as players!", ephemeral=True
        )
    if wins < 1 or wins > 10:
        return await interaction.response.send_message(
            "Please choose a number of wins between 1 and 10", ephemeral=True
        )
    if not target_channel:
        return await interaction.response.send_message(
            "You must specify a target channel for the match.", ephemeral=True
        )

    # Track the active match at start
    active_matches[target_channel.id] = {
        "interaction": interaction,
        "players": [player1.id, player2.id],
        "start_time": datetime.now(),
        "message": None  # Will store the scoreboard message
    }

    # Announce match in target channel
    await target_channel.send(
        f"🎮 **RPS Match Started!**\n"
        f"Away: {player1.mention}  vs  Home: {player2.mention}\n"
        f"First to {wins} wins, first to 7 total ties ends in a draw.\n"
        f"{f'**Match:** {desc}' if desc else ''}"
    )
    await interaction.response.send_message(
        f"Match created in {target_channel.mention}", ephemeral=True
    )

    # Initialize score and move tracking
    score = {
        player1.id: 0,
        player2.id: 0,
        "ties": 0
    }
    move_history = {
        player1.id: [],
        player2.id: []
    }
    last_move = {player1.id: "❔", player2.id: "❔"}  # type: dict[int, str]
    round_num = 1

    # Create a scoreboard message
    def create_scoreboard_message(final=False):
        header = "📊 **Scoreboard:**\n"
        moves_text = "🔄 **Move History:**\n"
        for pid, history in move_history.items():
            user = bot.get_user(pid)
            mention = user.mention if user else f"User({pid})"
            moves_text += f"{mention}: {' '.join(history) or 'No moves yet'}\n"
        
        # Sort players by score, then by ties
        sorted_players = sorted(
            [player1, player2],
            key=lambda p: (score[p.id], -move_history[p.id].count("❌")),
            reverse=True
        )
        
        # Highlight the player in the lead
        if score[sorted_players[0].id] > score[sorted_players[1].id]:
            lead_emoji = "🏆"
        else:
            lead_emoji = "🤝"
        
        score_text = (
            f"**Score:** {sorted_players[0].mention}: {score[sorted_players[0].id]} | "
            f"{sorted_players[1].mention}: {score[sorted_players[1].id]} | "
            f"Ties: {score['ties']}\n\n"
        )
        
        result_text = ""
        if final:
            if score["ties"] >= 7:  # If ties reached 7, it's an automatic draw
                result_text = "\n\n🤝 **Match ends in a draw due to too many ties!**"
            elif score[player1.id] > score[player2.id]:
                result_text = f"\n\n🎉 **{player1.mention} wins the match!**"
            elif score[player2.id] > score[player1.id]:
                result_text = f"\n\n🎉 **{player2.mention} wins the match!**"
            else:
                result_text = "\n\n🤝 **Match ends in a draw!**"
        
        return f"{header}{moves_text}{score_text}{result_text}"

    # Initial scoreboard message
    scoreboard_message = await send_to_channel(interaction, create_scoreboard_message())

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
            dm_msg1 = await dm1.send(f"**Round {round_num}:** Select your move (You have 30 seconds):", view=view1)
            
            dm2 = await player2.create_dm()
            dm_msg2 = await dm2.send(f"**Round {round_num}:** Select your move (You have 30 seconds):", view=view2)
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
                timeout=30  # Timeout in seconds (set to 30 for testing)
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
            score["ties"] += 1
            result_text = "Both players timed out - round counted as tie."
        elif m1 is None:  # Only player1 timed out
            score[player2.id] = wins
            result_text = f"{player2.mention} wins the match! {player1.mention} timed out and forfeits the game."
            break
        elif m2 is None:  # Only player2 timed out
            score[player1.id] = wins
            result_text = f"{player1.mention} wins the match! {player2.mention} timed out and forfeits the game."
            break
        else:  # Both played normally
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
        summary = create_scoreboard_message()
        try:
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
    final_summary = create_scoreboard_message(final=True)
    try:
        await scoreboard_message.edit(content=final_summary)
    except (discord.NotFound, discord.HTTPException):
        scoreboard_message = await send_to_channel(interaction, final_summary)
    
    # Notify players of final result
    for p in (player1, player2):
        try:
            dm = await p.create_dm()
            await dm.send(f"**Match Complete!**\n{final_summary}")
        except discord.Forbidden:
            continue

        if interaction.channel and interaction.channel.id in active_matches:
            del active_matches[interaction.channel.id]

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


@bot.tree.command(name="rps_cancel", description="[Admin] Cancel an ongoing RPS match")
@app_commands.describe(
    channel="Channel where match is happening (defaults to current)",
    reason="Reason for cancellation"
)
@app_commands.default_permissions(manage_messages=True)
async def rps_cancel(
    interaction: discord.Interaction,
    channel: Optional[discord.TextChannel] = None,
    reason: str = "No reason provided"
):
    """Allows admins to cancel stuck RPS matches"""
    target_channel = channel or interaction.channel
    
    if not target_channel or not isinstance(target_channel, discord.TextChannel):
        return await interaction.response.send_message(
            "❌ This command only works in text channels!",
            ephemeral=True
        )

    match_data = active_matches.get(target_channel.id)
    
    if not match_data:
        return await interaction.response.send_message(
            f"❌ No active RPS match found in {target_channel.mention}",
            ephemeral=True
        )
    
    # Get player objects for the cancellation message
    try:
        player1 = await bot.fetch_user(match_data["players"][0])
        player2 = await bot.fetch_user(match_data["players"][1])
    except discord.NotFound:
        return await interaction.response.send_message(
            "❌ Couldn't find one or both players!",
            ephemeral=True
        )

    # Create cancellation embed
    embed = discord.Embed(
        title="⚠️ RPS Match Cancelled",
        description=(
            f"**Admin:** {interaction.user.mention}\n"
            f"**Reason:** {reason}\n\n"
            f"Match between {player1.mention} and {player2.mention} "
            f"was forcibly cancelled."
        ),
        color=discord.Color.red()
    )
    
    # Calculate and format duration
    duration = datetime.now() - match_data['start_time']
    duration_str = str(duration).split('.')[0]  # Removes microseconds
    embed.set_footer(text=f"Match duration: {duration_str}")
    
    # Try to notify in the game channel
    try:
        await target_channel.send(embed=embed)
    except discord.Forbidden:
        await interaction.followup.send(
            "⚠️ Couldn't send cancellation message to the game channel",
            ephemeral=True
        )
    # Notify players via DM
    for player in (player1, player2):
        try:
            dm = await player.create_dm()
            await dm.send("⚠️ The RPS match was cancelled by an admin.")
        except discord.Forbidden:
            continue
    # Clean up and confirm
    del active_matches[target_channel.id]
    await interaction.response.send_message(
        f"✅ Successfully cancelled match in {target_channel.mention}",
        ephemeral=True
    )
bot.run(TOKEN)