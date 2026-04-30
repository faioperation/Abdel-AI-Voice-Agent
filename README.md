# 🍕 Pizzeria Network - AI Voice Agent & Dashboard

A professional, high-performance AI ordering system designed for modern pizzerias. This project features a sophisticated AI Voice Agent capable of handling real-time customer orders, calculating prices from a dynamic menu, and automating the capture of caller details.

## ✨ Key Features

- **🎙️ Premium Voice Agent**: Optimized for low-latency, "Voice-First" conversations with natural, human-like responses.
- **📊 Orders Dashboard**: A glassmorphism-styled dashboard to track customer orders, total sales, and item details.
- **📞 Call History**: Real-time logging of all inbound and outbound calls, including durations, call costs, and audio recordings.
- **🤖 Chat Integration**: Integrated "Chat Test" environment to simulate AI conversations and order flows before going live.
- **📁 Managed Knowledge**: AI behavior and menu items are managed via simple text files (`system_prompt.txt` and `pizza_menu.txt`).
- **📱 Automatic Caller ID**: Automatically captures customer phone numbers during voice calls without the AI needing to ask.

## 🛠️ Technology Stack

- **Backend**: FastAPI (Python 3.9+)
- **Database**: Supabase (PostgreSQL) with SQLAlchemy ORM
- **AI/Voice**: Vapi.ai SDK & OpenAI GPT-4o-mini
- **Frontend**: Vanilla JS, CSS3 (Glassmorphism design), SweetAlert2
- **Deployment**: Optimized for Vercel

## 🚀 Quick Start

### 1. Prerequisites
- Python 3.9+ installment
- A Supabase account (or any PostgreSQL instance)
- Vapi.ai and OpenAI API keys

### 2. Installation
```bash
# Clone the repository
git clone https://github.com/r0nY-0017/Voice-Agent.git

# Install dependencies
pip install -r requirements.txt
```

### 3. Environment Variables
Create a `.env` file in the root directory:
```env
# Vapi & OpenAI
VAPI_API_KEY=your_vapi_key
VAPI_PUBLIC_KEY=your_vapi_public_key
OPENAI_API_KEY=your_openai_key

# Admin Auth
ADMIN_USERNAME=admin
ADMIN_PASSWORD=your_password
SECRET_KEY=highly-secret-string

# Database
DATABASE_URL=postgresql://user:pass@host:port/dbname
BACKEND_URL=https://your-domain.vercel.app
```

### 4. Running Locally
```bash
python -m uvicorn main:app --reload --port 8001
```

### 5. Deployment (Vercel)
This repository is pre-configured for Vercel. Simply connect your GitHub repository to Vercel, add the Environment Variables in the Vercel Dashboard, and it's ready to go.

## 📁 Project Structure
- `app/`: Core backend logic and routes.
- `api/`: Vercel serverless entry point.
- `static/`: Frontend assets (CSS/JS).
- `templates/`: HTML pages.
- `system_prompt.txt`: AI personality and order flow configuration.

---
Developed with ❤️ for the Pizzeria Network.
