import os
from dotenv import load_dotenv

load_dotenv()

# Vapi & OpenAI
VAPI_API_KEY = os.getenv("VAPI_API_KEY")
VAPI_PUBLIC_KEY = os.getenv("VAPI_PUBLIC_KEY", "")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

# Admin auth
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "admin123")
SECRET_KEY = os.getenv("SECRET_KEY", "change-me")

# Database (Supabase PostgreSQL)
DATABASE_URL = os.getenv("DATABASE_URL")
if DATABASE_URL:
    if DATABASE_URL.startswith("postgres://"):
        DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql+pg8000://", 1)
    elif DATABASE_URL.startswith("postgresql://"):
        DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+pg8000://", 1)
BACKEND_URL = os.getenv("BACKEND_URL", "")

def load_system_prompt(filename="system_prompt.txt"):
    prompt_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), filename)
    if os.path.exists(prompt_path):
        with open(prompt_path, "r", encoding="utf-8") as f:
            return f.read()
    return "You are a professional AI Order Assistant for 'Pizzeria Network'." # Fallback

PIZZERIA_SYSTEM_PROMPT = load_system_prompt("system_prompt.txt")

VAPI_BASE = "https://api.vapi.ai"