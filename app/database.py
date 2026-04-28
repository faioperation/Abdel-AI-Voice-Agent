from sqlalchemy import create_engine, Column, String, Text, DateTime, Integer, Numeric
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from datetime import datetime
from .config import DATABASE_URL

# If DATABASE_URL is missing, fallback to sqlite in /tmp to prevent immediate crash on Vercel
safe_db_url = DATABASE_URL if DATABASE_URL else "sqlite:////tmp/fallback.db"

engine = create_engine(
    safe_db_url,
    echo=False,
    pool_pre_ping=True,
    pool_recycle=300
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

class Assistant(Base):
    __tablename__ = "assistants"
    id = Column(String, primary_key=True)
    name = Column(String, nullable=False)
    model = Column(String, nullable=True)
    voice_id = Column(String, nullable=True)
    system_prompt = Column(Text, nullable=True)
    language = Column(String, default="en")
    created_at = Column(DateTime, default=datetime.utcnow)
    call_count = Column(Integer, default=0)
    vapi_data = Column(Text, nullable=True)
    query_tool_id = Column(String, nullable=True)
    file_ids = Column(Text, nullable=True)

class KnowledgeBase(Base):
    __tablename__ = "knowledge_base"
    id = Column(Integer, primary_key=True, autoincrement=True)
    assistant_id = Column(String, index=True)
    file_name = Column(String)
    vapi_file_id = Column(String, nullable=True)
    extracted_text = Column(Text, nullable=True)

class CallRecord(Base):
    __tablename__ = "calls"
    id = Column(String, primary_key=True)
    assistant_id = Column(String, index=True)
    phone_number = Column(String)
    started_at = Column(DateTime, default=datetime.utcnow)
    ended_at = Column(DateTime, nullable=True)
    status = Column(String)
    duration = Column(Integer, default=0)
    recording_url = Column(String, nullable=True)

class Order(Base):
    __tablename__ = "orders"
    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String, nullable=False)
    phone = Column(String, nullable=True)
    order = Column(Text, nullable=False)
    total = Column(Numeric(10, 2), nullable=False)  # e.g. 18.98
    call_id = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

class ConversationHistory(Base):
    __tablename__ = "conversation_history"
    id = Column(Integer, primary_key=True, autoincrement=True)
    session_id = Column(String, index=True) # phone_number or uuid
    role = Column(String) # user, assistant, system
    content = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)

def init_db():
    Base.metadata.create_all(bind=engine)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()