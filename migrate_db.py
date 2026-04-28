import os
from sqlalchemy import create_engine, text
from dotenv import load_dotenv

load_dotenv()
DATABASE_URL = os.getenv("DATABASE_URL")

def migrate():
    if not DATABASE_URL:
        print("DATABASE_URL not found in .env")
        return
    
    engine = create_engine(DATABASE_URL)
    
    # Run create_all to ensure all tables exist
    from app.database import init_db
    init_db()
    
    try:
        with engine.connect() as conn:
            # Add language column if it doesn't exist
            conn.execute(text("ALTER TABLE assistants ADD COLUMN IF NOT EXISTS language VARCHAR DEFAULT 'en'"))
            conn.commit()
        print("Successfully added 'language' column to assistants table.")
    except Exception as e:
        print(f"Error during migration: {e}")

if __name__ == "__main__":
    migrate()
