from fastapi import APIRouter, Request, HTTPException, Depends
from app.auth import create_access_token, get_current_user
from app.config import ADMIN_USERNAME, ADMIN_PASSWORD, VAPI_API_KEY

router = APIRouter()

@router.post("/api/login")
async def login(request: Request):
    data = await request.json()          # ✅ await added
    if data.get("username") == ADMIN_USERNAME and data.get("password") == ADMIN_PASSWORD:
        token = create_access_token({"sub": ADMIN_USERNAME})
        return {"access_token": token, "token_type": "bearer"}
    raise HTTPException(401, "Invalid credentials")