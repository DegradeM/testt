import os
import discord
from discord.ext import commands
from discord import app_commands
from dotenv import load_dotenv
import json
import asyncio
import datetime

load_dotenv()

TOKEN = os.getenv("TOKEN")
GUILD_ID = int(os.getenv("GUILD_ID"))

intents = discord.Intents.default()
intents.guilds = True
intents.members = True
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)
guild_obj = discord.Object(id=GUILD_ID)

DATA_FILE = "bot_data.json"


# ---------------- PERSISTENT DATA ----------------

def load_data():
    try:
        with open(DATA_FILE, "r") as f:
            return json.load(f)
    except:
        return {
            "ticket_counter": 0,
            "tickets": {},
            "command_counts": {}
        }


def save_data(data):
    with open(DATA_FILE, "w") as f:
        json.dump(data, f, indent=4)


data = load_data()


# ---------------- HELPERS ----------------

def is_staff(member: discord.Member):
    return any(role.name == "Staff" for role in member.roles)


def has_role_5(member: discord.Member):
    return any(role.name == "5" for role in member.roles)


def get_role_5(guild: discord.Guild):
    return discord.utils.get(guild.roles, name="5")


def next_ticket_number():
    data["ticket_counter"] += 1
    save_data(data)
    return data["ticket_counter"]


async def get_log_channel(guild: discord.Guild):
    channel = discord.utils.get(guild.text_channels, name="bot-logs")
    if channel is None:
        channel = await guild.create_text_channel("bot-logs")
    return channel


async def log_command(interaction: discord.Interaction, ticket_id=None):
    channel = await get_log_channel(interaction.guild)

    user_id = str(interaction.user.id)

    data["command_counts"][user_id] = data["command_counts"].get(user_id, 0) + 1
    save_data(data)

    embed = discord.Embed(
        title="Command Log",
        color=discord.Color.dark_gray(),
        timestamp=datetime.datetime.utcnow()
    )

    embed.add_field(name="User", value=str(interaction.user), inline=False)
    embed.add_field(name="Command", value=interaction.command.name, inline=True)

    if ticket_id:
        embed.add_field(name="Ticket #", value=str(ticket_id), inline=True)

    await channel.send(embed=embed)


# ---------------- TICKET FORM ----------------

class TicketForm(discord.ui.Modal, title="Service Form"):

    server_number = discord.ui.TextInput(
        label="Server Number",
        min_length=4,
        max_length=4,
        required=True
    )

    services = discord.ui.TextInput(
        label="Services Provided",
        style=discord.TextStyle.paragraph,
        required=True
    )

    async def on_submit(self, interaction: discord.Interaction):

        if not self.server_number.value.isdigit():
            await interaction.response.send_message(
                "Server number must be 4 digits.",
                ephemeral=True
            )
            return

        channel = interaction.channel

        await channel.set_permissions(
            interaction.user,
            view_channel=True,
            send_messages=True,
            read_message_history=True
        )

        embed = discord.Embed(
            title="Form Completed",
            color=discord.Color.blurple()
        )

        embed.add_field(name="Server Number", value=self.server_number.value, inline=True)
        embed.add_field(name="Services", value=self.services.value, inline=False)

        msg = await channel.send(embed=embed)
        await msg.pin()

        await interaction.response.send_message(
            "Access granted.",
            ephemeral=True
        )


class OpenFormButton(discord.ui.View):

    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="Fill Form", style=discord.ButtonStyle.secondary)
    async def open_form(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(TicketForm())


# ---------------- READY ----------------

@bot.event
async def on_ready():
    bot.tree.copy_global_to(guild=guild_obj)
    await bot.tree.sync(guild=guild_obj)
    print(f"Logged in as {bot.user}")


# ---------------- /ADD ----------------

@bot.tree.command(name="add", description="Create or update ticket", guild=guild_obj)
@app_commands.describe(user="User", name="Ticket name optional")
async def add(interaction: discord.Interaction, user: discord.Member, name: str = None):

    await log_command(interaction)

    role_5 = get_role_5(interaction.guild)

    # IF INSIDE AN EXISTING TICKET → ADD USER TO IT
    if str(interaction.channel.id) in data["tickets"]:

        ticket_id = data["tickets"][str(interaction.channel.id)]

        await interaction.channel.set_permissions(
            user,
            view_channel=True,
            send_messages=True,
            read_message_history=True
        )

        # GIVE ROLE 5
        if role_5 and role_5 not in user.roles:
            await user.add_roles(role_5, reason="Added to ticket")

        await log_command(interaction, ticket_id)

        await interaction.response.send_message(
            f"Added {user.mention} to ticket #{ticket_id}",
            ephemeral=True
        )

        await interaction.channel.send(
            f"{user.mention} added to ticket #{ticket_id}"
        )
        return

    # CREATE NEW TICKET
    ticket_number = next_ticket_number()

    channel_name = name.lower().replace(" ", "-") if name else f"ticket-{ticket_number}"

    overwrites = {
        interaction.guild.default_role: discord.PermissionOverwrite(view_channel=False),

        user: discord.PermissionOverwrite(
            view_channel=True,
            send_messages=False,
            read_message_history=True
        ),

        interaction.user: discord.PermissionOverwrite(
            view_channel=True,
            send_messages=False,
            read_message_history=True
        )
    }

    for role in interaction.guild.roles:
        if role.name == "Staff":
            overwrites[role] = discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                read_message_history=True
            )

    channel = await interaction.guild.create_text_channel(
        name=channel_name,
        overwrites=overwrites,
        category=discord.utils.get(interaction.guild.categories, name="Tickets")
    )

    data["tickets"][str(channel.id)] = ticket_number
    save_data(data)

    # GIVE ROLE 5 ON TICKET CREATION
    if role_5 and role_5 not in user.roles:
        await user.add_roles(role_5, reason="Ticket created")

    await interaction.response.send_message(
        f"Ticket #{ticket_number} created: {channel.mention}",
        ephemeral=True
    )

    embed = discord.Embed(
        title=f"Support Ticket #{ticket_number}",
        description=f"User: {user.mention}\nComplete form before chatting.",
        color=discord.Color.blurple()
    )

    msg = await channel.send(embed=embed, view=OpenFormButton())
    await msg.pin()


# ---------------- REQUEST ----------------

@bot.tree.command(name="request", description="Send admin request", guild=guild_obj)
@app_commands.describe(message="Message")
async def request(interaction: discord.Interaction, message: str):

    await log_command(interaction)

    if not has_role_5(interaction.user):
        await interaction.response.send_message("No permission.", ephemeral=True)
        return

    await interaction.response.send_message("Sending...", ephemeral=True)

    ticket_number = None
    if interaction.channel.id in map(int, data["tickets"].keys()):
        ticket_number = data["tickets"].get(str(interaction.channel.id))

    admins = [
        m for m in interaction.guild.members
        if m.guild_permissions.administrator and not m.bot
    ]

    sent = 0

    for admin in admins:
        try:
            embed = discord.Embed(
                title="Request",
                description=message,
                color=discord.Color.red()
            )

            embed.add_field(name="From Role", value="Role 5", inline=True)

            if ticket_number:
                embed.add_field(name="Ticket #", value=str(ticket_number), inline=True)

            embed.set_footer(text=f"From {interaction.user}")

            await admin.send(embed=embed)
            sent += 1

        except:
            pass

    await interaction.followup.send(f"Sent to {sent} admins.", ephemeral=True)


# ---------------- INFO ----------------

@bot.tree.command(name="info", description="User command stats", guild=guild_obj)
@app_commands.describe(user="User or ID")
async def info(interaction: discord.Interaction, user: str):

    await log_command(interaction)

    try:
        if user.isdigit():
            member = await interaction.guild.fetch_member(int(user))
        else:
            member = await interaction.guild.fetch_member(int(user.strip("<@!>")))
    except:
        await interaction.response.send_message("User not found.", ephemeral=True)
        return

    count = data["command_counts"].get(str(member.id), 0)

    embed = discord.Embed(
        title="User Info",
        color=discord.Color.blurple()
    )

    embed.add_field(name="User", value=str(member), inline=False)
    embed.add_field(name="Commands Used", value=str(count), inline=True)

    await interaction.response.send_message(embed=embed, ephemeral=True)


# ---------------- CLOSE ----------------

@bot.tree.command(name="close", description="Close ticket", guild=guild_obj)
async def close(interaction: discord.Interaction):

    await log_command(interaction)

    await interaction.response.send_message("Closing ticket...")
    await interaction.channel.delete()


# ---------------- RUN ----------------

bot.run(TOKEN)
