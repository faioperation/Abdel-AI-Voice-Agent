import os
from sqlalchemy import create_engine, text
from dotenv import load_dotenv

load_dotenv()
DATABASE_URL = os.getenv("DATABASE_URL")

def migrate():
    if not DATABASE_URL:
        print("DATABASE_URL not found in .env")
        return
    
    db_url = DATABASE_URL
    if db_url.startswith("postgres://"):
        db_url = db_url.replace("postgres://", "postgresql+pg8000://", 1)
    elif db_url.startswith("postgresql://"):
        db_url = db_url.replace("postgresql://", "postgresql+pg8000://", 1)
    
    engine = create_engine(db_url)
    
    # Run create_all to ensure all tables exist
    from app.database import init_db
    init_db()
    
    try:
        with engine.connect() as conn:
            # Add language column if it doesn't exist
            conn.execute(text("ALTER TABLE assistants ADD COLUMN IF NOT EXISTS language VARCHAR DEFAULT 'en'"))
            # Add forwarding_number column if it doesn't exist
            conn.execute(text("ALTER TABLE assistants ADD COLUMN IF NOT EXISTS forwarding_number VARCHAR"))
            conn.commit()
        print("Successfully applied migrations to assistants table.")
    except Exception as e:
        print(f"Error during migration: {e}")

if __name__ == "__main__":
    migrate()
