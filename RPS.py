import os
import discord
import asyncio
import requests
import logging
from datetime import datetime
from discord import app_commands, ui, Interaction, Message, Embed
from discord.ext import commands
from typing import Optional, Dict, List
from dotenv import load_dotenv
from keep_alive import keep_alive

# Setup logging and environment
load_dotenv()
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
TOKEN = os.getenv("DISCORD_TOKEN")
if not TOKEN:
    raise RuntimeError("Missing DISCORD_TOKEN in environment")

# Initialize bot with required intents
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

# --------------------------
# RPS Game Components
# --------------------------
EMOJI_TO_MOVE = {"🪨": "rock", "📄": "paper", "✂️": "scissors"}
MAX_ROUNDS = 20
MOVE_TIMEOUT = 14400  # 4 hours

async def send_embed_safely(interaction: Interaction, embed: Embed) -> Message:
    """Universal embed sender with fallbacks"""
    try:
        if interaction.channel and isinstance(interaction.channel, (discord.TextChannel, discord.Thread)):
            return await interaction.channel.send(embed=embed)
        
        if not interaction.response.is_done():
            await interaction.response.defer()
        msg = await interaction.followup.send(embed=embed)
        if not isinstance(msg, Message):
            raise ValueError("Unexpected response from followup.send")
        return msg
    except Exception as e:
        logging.error(f"Failed to send embed: {e}")
        raise

class RPSView(ui.View):
    def __init__(self, player: discord.User, round_num: int):
        super().__init__(timeout=MOVE_TIMEOUT)
        self.player = player
        self.choice = None
        self.round_num = round_num

    @ui.button(emoji="🪨", style=discord.ButtonStyle.secondary)
    async def rock(self, interaction: Interaction, button: ui.Button):
        await self.handle_choice(interaction, "rock")

    @ui.button(emoji="📄", style=discord.ButtonStyle.secondary)
    async def paper(self, interaction: Interaction, button: ui.Button):
        await self.handle_choice(interaction, "paper")

    @ui.button(emoji="✂️", style=discord.ButtonStyle.secondary)
    async def scissors(self, interaction: Interaction, button: ui.Button):
        await self.handle_choice(interaction, "scissors")

    async def handle_choice(self, interaction: Interaction, choice: str):
        if interaction.user.id != self.player.id:
            return await interaction.response.send_message("Not your game!", ephemeral=True)
        self.choice = choice
        await interaction.response.defer()
        self.stop()

class Match:
    def __init__(self, player1: discord.User, player2: discord.User, wins_needed: int, description: str = ""):
        self.players = {player1.id: player1, player2.id: player2}
        self.scores = {player1.id: 0, player2.id: 0, "ties": 0}
        self.move_history = {player1.id: [], player2.id: []}
        self.wins_needed = wins_needed
        self.description = description
        self.start_time = datetime.utcnow()
        self.round_num = 1
        self.channel_message: Optional[Message] = None
        self.dm_messages: Dict[int, Message] = {}

    def _format_move_history(self, player_id: int) -> str:
        """Format all moves for a player as emoji sequence"""
        return " ".join(self.move_history.get(player_id, []))

    def add_move(self, player_id: int, move: str):
        emoji = next((e for e, name in EMOJI_TO_MOVE.items() if name == move), "❔")
        self.move_history[player_id].append(emoji)

    def create_embed(self, result_text: str, final: bool) -> Embed:
        p1, p2 = list(self.players.values())
        embed = Embed(
            title=f"RPS Match: {self.description}" if self.description else "RPS Match",
            color=discord.Color.blurple(),
            timestamp=datetime.utcnow()
        )
        embed.add_field(
            name="Move History",
            value=f"{p1.display_name}: {self._format_move_history(p1.id)}\n{p2.display_name}: {self._format_move_history(p2.id)}",
            inline=False
        )
        embed.add_field(
            name="Score",
            value=f"{p1.display_name}: {self.scores[p1.id]}\n{p2.display_name}: {self.scores[p2.id]}\nTies: {self.scores['ties']}",
            inline=False
        )
        embed.add_field(name=f"Round {self.round_num}", value=result_text, inline=False)
        
        if final:
            winner = self.determine_winner()
            if winner:
                embed.set_footer(text=f"🎉 {winner.display_name} wins!")
            else:
                embed.set_footer(text="🤝 Match ended in a draw!")
        return embed

    def determine_winner(self) -> Optional[discord.User]:
        p1_id, p2_id = list(self.players.keys())
        if self.scores[p1_id] >= self.wins_needed:
            return self.players[p1_id]
        if self.scores[p2_id] >= self.wins_needed:
            return self.players[p2_id]
        return None

    async def update_display(self, interaction: Interaction, result_text: str, final: bool = False):
        embed = self.create_embed(result_text, final)
        try:
            if self.channel_message:
                await self.channel_message.edit(embed=embed)
            else:
                self.channel_message = await send_embed_safely(interaction, embed)
        except Exception as e:
            logging.error(f"Channel update failed: {e}")

        for player_id, player in self.players.items():
            try:
                if player_id in self.dm_messages:
                    await self.dm_messages[player_id].edit(
                        content=f"**Round {self.round_num}**" if not final else "Match complete!",
                        embed=embed,
                        view=None if final else RPSView(player, self.round_num)
                    )
            except Exception as e:
                logging.error(f"DM update failed for {player}: {e}")

# --------------------------
# Bot Commands
# --------------------------
@bot.tree.command(name="rps", description="Start a competitive RPS match")
@app_commands.describe(
    player1="First player",
    player2="Second player",
    wins="Wins needed (1-10)",
    description="Optional match description"
)
async def rps(interaction: Interaction, player1: discord.User, player2: discord.User, 
             wins: app_commands.Range[int, 1, 10], description: str = ""):
    if player1.bot or player2.bot:
        return await interaction.response.send_message("Bots can't play RPS!", ephemeral=True)
    
    await interaction.response.send_message(
        f"🎮 RPS Match: {player1.mention} vs {player2.mention}\nFirst to {wins} wins!",
        allowed_mentions=discord.AllowedMentions(users=[player1, player2])
    )
    
    match = Match(player1, player2, wins, description)
    
    # Setup DMs
    for player in [player1, player2]:
        try:
            dm = await player.create_dm()
            match.dm_messages[player.id] = await dm.send(
                f"**Round 1** - Select your move:",
                view=RPSView(player, 1)
            )
        except:
            await interaction.followup.send(f"⚠️ Couldn't DM {player.mention}", ephemeral=True)
            return

    # Game loop
    while not match.determine_winner() and match.scores["ties"] < 7 and match.round_num <= MAX_ROUNDS:
        # Get moves
        moves = {}
        views = {p.id: RPSView(p, match.round_num) for p in match.players.values()}
        try:
            await asyncio.wait_for(
                asyncio.gather(*[v.wait() for v in views.values()]),
                timeout=MOVE_TIMEOUT
            )
            moves = {pid: v.choice for pid, v in views.items()}
        except asyncio.TimeoutError:
            moves = {pid: None for pid in match.players}
        
        # Process round
        p1_id, p2_id = list(match.players.keys())
        m1, m2 = moves[p1_id], moves[p2_id]
        
        if m1: match.add_move(p1_id, m1)
        if m2: match.add_move(p2_id, m2)
        
        if m1 is None and m2 is None:
            result = "Both timed out!"
        elif m1 is None:
            match.scores[p2_id] += 1
            result = f"{match.players[p2_id].display_name} wins by forfeit!"
        elif m2 is None:
            match.scores[p1_id] += 1
            result = f"{match.players[p1_id].display_name} wins by forfeit!"
        else:
            winner = (1 if (m1 == "rock" and m2 == "scissors") or 
                          (m1 == "scissors" and m2 == "paper") or 
                          (m1 == "paper" and m2 == "rock") 
                     else 2 if m1 != m2 else 0)
            if winner == 1:
                match.scores[p1_id] += 1
                result = f"{match.players[p1_id].display_name} wins!"
            elif winner == 2:
                match.scores[p2_id] += 1
                result = f"{match.players[p2_id].display_name} wins!"
            else:
                match.scores["ties"] += 1
                result = "Tie!"
        
        # Update
        await match.update_display(interaction, result)
        match.round_num += 1
    
    # Finalize
    winner = match.determine_winner()
    if winner:
        await interaction.followup.send(
            f"🎉 {winner.mention} won the match!",
            allowed_mentions=discord.AllowedMentions(users=[winner])
        )
    await match.update_display(interaction, "Match complete!", final=True)

@bot.tree.command(name="ping", description="Check bot latency")
async def ping(interaction: Interaction):
    """Simple ping command"""
    await interaction.response.send_message(
        f"🏓 Pong! {round(bot.latency * 1000)}ms",
        ephemeral=True
    )

@bot.tree.command(name="update", description="Redeploy the bot (Admin only)")
@app_commands.check(lambda i: i.user.guild_permissions.administrator if isinstance(i.user, discord.Member) else False)
async def update(interaction: Interaction):
    """Update command for admins"""
    hook_url = os.getenv("RENDER_DEPLOY_HOOK_URL")
    if not hook_url:
        return await interaction.response.send_message(
            "🚨 Deploy hook not configured!",
            ephemeral=True
        )
    
    try:
        resp = requests.post(hook_url, timeout=10)
        if resp.status_code == 200:
            await interaction.response.send_message(
                "✅ Redeploy initiated!",
                ephemeral=True
            )
        else:
            await interaction.response.send_message(
                f"❌ Failed (HTTP {resp.status_code})",
                ephemeral=True
            )
    except Exception as e:
        await interaction.response.send_message(
            f"⚠️ Error: {str(e)}",
            ephemeral=True
        )

# --------------------------
# Bot Events
# --------------------------
@bot.event
async def on_ready():
    """Bot startup handler with proper null checks"""
    if bot.user is None:
        logging.error("Bot user is None - failed to log in")
        return

    logging.info(f"Logged in as {bot.user} (ID: {bot.user.id})")
    
    try:
        synced = await bot.tree.sync()
        logging.info(f"Synced {len(synced)} commands")
    except Exception as e:
        logging.error(f"Command sync error: {e}")
    
    # Optional: Set bot presence
    await bot.change_presence(activity=discord.Game(name="Rock Paper Scissors"))

# --------------------------
# Start the Bot
# --------------------------
keep_alive()
bot.run(TOKEN)