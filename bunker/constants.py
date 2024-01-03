from dotenv import load_dotenv
import os
from pathlib import Path
from sqlalchemy.engine.url import URL

# The path to the .env file
DOTENV_PATH = Path('.env')
load_dotenv(DOTENV_PATH)

# The path pointing to the logs folder
LOGS_FOLDER = Path('logs')
# The format to use for logged events
LOGS_FORMAT = '[%(asctime)s][%(levelname)s][%(module)s.%(funcName)s:%(lineno)s] %(message)s'

if not LOGS_FOLDER.exists():
    print('Adding logs folder:\n', LOGS_FOLDER.absolute())
    LOGS_FOLDER.mkdir()

# Load DB parameters from env
DB_HOST = os.getenv('DB_HOST', 'localhost')
DB_PORT = os.getenv('DB_PORT', 5432)
DB_USER = os.getenv('DB_USER', 'postgres')
DB_PASSWORD = os.getenv('DB_PASSWORD')
# Create DB url
DB_URL = URL.create(
    drivername="postgresql+asyncpg",
    username=DB_USER,
    password=DB_PASSWORD,
    host=DB_HOST,
    port=DB_PORT,
    database="bunker",
).render_as_string(hide_password=False)

# Discord bot's token
DISCORD_BOT_TOKEN = os.getenv('DISCORD_BOT_TOKEN')
# Path to directory with discord.py cogs
DISCORD_COGS_PATH = Path("./bunker/discord/cogs")
# Main Discord guild's ID
DISCORD_GUILD_ID = int(os.getenv('DISCORD_GUILD_ID', 0))
# ID of the Server Admin role
DISCORD_ADMIN_ROLE_ID = int(os.getenv('DISCORD_ADMIN_ROLE_ID', 0))
# ID of the Server Owner role
DISCORD_OWNER_ROLE_ID = int(os.getenv('DISCORD_OWNER_ROLE_ID', 0))
# ID of the main report channel
DISCORD_REPORTS_CHANNEL_ID = int(os.getenv('DISCORD_REPORTS_CHANNEL_ID', 0))

# How many admins each community is allowed to have (including the owner)
MAX_ADMIN_LIMIT = 3

# The URL of the report form
REPORT_FORM_URL = "https://docs.google.com/forms/d/e/1FAIpQLSedlbl33F6OXaBmaIk6brem79krxSDn_UX9qLymcUOcC7lw-Q/viewform?entry.1804901355={access_token}"
