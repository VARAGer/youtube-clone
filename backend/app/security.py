import hashlib
import hmac
import secrets
from datetime import datetime, timedelta
from fastapi import Depends, HTTPException, Request, Response
from sqlalchemy.orm import Session as DBSession
from .db import get_db
from .models import Session, User
from . import config

def hash_password(password):
    salt = secrets.token_bytes(16)
    value = hashlib.scrypt(password.encode(),salt=salt,n=16384,r=8,p=1,dklen=32)
    return salt.hex() + ':' + value.hex()
def verify_password(password, encoded):
    try:
        salt, expected = encoded.split(':')
        value = hashlib.scrypt(password.encode(),salt=bytes.fromhex(salt),n=16384,r=8,p=1,dklen=32)
        return hmac.compare_digest(value.hex(), expected)
    except (ValueError,TypeError):
        return False
def issue_session(db, user, response):
    token = secrets.token_urlsafe(32)
    db.add(Session(token_hash=hashlib.sha256(token.encode()).hexdigest(),user_id=user.id,expires_at=datetime.utcnow()+timedelta(days=config.SESSION_DAYS)))
    db.commit()
    response.set_cookie('session',token,httponly=True,secure=config.COOKIE_SECURE,samesite='lax',max_age=config.SESSION_DAYS*86400,path='/')
def current_user(request: Request, db: DBSession=Depends(get_db)):
    token = request.cookies.get('session')
    if not token:
        return None
    session = db.get(Session, hashlib.sha256(token.encode()).hexdigest())
    if not session or session.expires_at <= datetime.utcnow():
        return None
    return db.get(User,session.user_id)
def require_user(user=Depends(current_user)):
    if not user:
        raise HTTPException(401,'Sign in required')
    return user
def require_staff(user=Depends(require_user)):
    if user.role not in ('moderator','admin'):
        raise HTTPException(403,'Moderator access required')
    return user
def require_admin(user=Depends(require_user)):
    if user.role != 'admin':
        raise HTTPException(403,'Administrator access required')
    return user
