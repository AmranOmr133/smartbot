"""
FastAPI Dependencies
"""
from fastapi import Depends, HTTPException, status, Request
from fastapi.responses import RedirectResponse
import aiosqlite
from app.core.database import get_db
from app.core.security import decode_token, get_token_from_request


async def get_current_user(request: Request, db: aiosqlite.Connection = Depends(get_db)):
    """Get current authenticated user from JWT cookie."""
    token = get_token_from_request(request)
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")
    
    payload = decode_token(token)
    if not payload or payload.get("type") != "user":
        raise HTTPException(status_code=401, detail="Invalid token")
    
    user_id = payload.get("sub")
    async with db.execute("SELECT * FROM users WHERE id = ? AND is_active = 1", (user_id,)) as cursor:
        user = await cursor.fetchone()
    
    if not user:
        raise HTTPException(status_code=401, detail="User not found")
    
    return dict(user)


async def get_current_admin(request: Request, db: aiosqlite.Connection = Depends(get_db)):
    """Get current authenticated admin from JWT cookie."""
    token = get_token_from_request(request)
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")
    
    payload = decode_token(token)
    if not payload or payload.get("type") != "admin":
        raise HTTPException(status_code=401, detail="Invalid admin token")
    
    admin_id = payload.get("sub")
    async with db.execute("SELECT * FROM admins WHERE id = ? AND is_active = 1", (admin_id,)) as cursor:
        admin = await cursor.fetchone()
    
    if not admin:
        raise HTTPException(status_code=401, detail="Admin not found")
    
    return dict(admin)


async def get_user_subscription(user_id: int, db: aiosqlite.Connection):
    """Get user subscription details."""
    async with db.execute(
        "SELECT * FROM subscriptions WHERE user_id = ? ORDER BY id DESC LIMIT 1",
        (user_id,)
    ) as cursor:
        return await cursor.fetchone()
