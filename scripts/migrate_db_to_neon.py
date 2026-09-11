import os
from dotenv import load_dotenv

# Try to load from configs/.env or root .env
if os.path.exists("configs/.env"):
    load_dotenv("configs/.env")
else:
    load_dotenv()

neon_url = os.getenv("DATABASE_URL")

if not neon_url:
    print("❌ ERROR: DATABASE_URL not found in .env")
    exit(1)

print(f"Connecting to Neon DB: {neon_url.split('@')[1] if '@' in neon_url else neon_url}")

# Run the setup script
import sys
sys.path.append(os.path.abspath("."))
from examples.setup_scripts.db_setup import setup_database

setup_database(neon_url)

# Also initialize the episodic memory tables
from src.core.memory_manager import DatabaseMemory
print("🔧 Initializing Memory Tables in Neon...")
mem = DatabaseMemory(neon_url)
print("✅ Memory Tables created successfully.")

print("🎉 Migration complete! Your Neon DB is ready for action.")
