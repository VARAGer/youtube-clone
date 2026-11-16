from contextlib import asynccontextmanager
from datetime import datetime
import hashlib
import os
from pathlib import Path
import re
import tempfile
import time
from collections import defaultdict, deque
from urllib.parse import urlsplit
import uuid
from fastapi import FastAPI, Depends, HTTPException, Request, Response, UploadFile, File, Form
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select, func, text, delete, update, or_, and_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session as DBSession
from . import config, models as m, schemas as s
from .db import get_db, init_db
from .security import current_user, require_user, require_staff, require_admin, hash_password, verify_password, issue_session
from .storage import get_storage

@asynccontextmanager
async def lifespan(app):
    init_db()
    get_storage().ensure()
    yield
app = FastAPI(title='YouTube Clone API',version='1.0.0',lifespan=lifespan)
# Per-process abuse protection; reverse proxy is expected to set a global rate limit.
_auth_attempts = defaultdict(deque)
@app.middleware('http')
async def security_headers(request, call_next):
    if request.method not in ('GET','HEAD','OPTIONS'):
        origin = request.headers.get('origin')
        expected = config.PUBLIC_ORIGIN or str(request.base_url).rstrip('/')
        if origin and origin.rstrip('/') != expected:
            return JSONResponse({'detail':'Cross-origin request denied'},status_code=403)
        if request.headers.get('sec-fetch-site') == 'cross-site':
            return JSONResponse({'detail':'Cross-site request denied'},status_code=403)
    if request.url.path in ('/api/auth/register','/api/auth/login') and request.method == 'POST':
        key = request.client.host if request.client else 'unknown'
        bucket = _auth_attempts[key]
        now = time.monotonic()
        while bucket and bucket[0] < now - 60:
            bucket.popleft()
        if len(bucket) >= 20:
            return JSONResponse({'detail':'Too many attempts; retry in a minute'},status_code=429)
        bucket.append(now)
    response = await call_next(request)
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['Referrer-Policy'] = 'same-origin'
    response.headers['X-Frame-Options'] = 'DENY'
    response.headers['Content-Security-Policy'] = "default-src 'self'; img-src 'self' data:; media-src 'self' blob:; script-src 'self'; style-src 'self' 'unsafe-inline'; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
    if request.url.path.startswith('/api'):
        response.headers['Cache-Control'] = 'no-store'
    return response

def user_json(user):
    return {'id':user.id,'email':user.email,'display_name':user.display_name,'role':user.role}
def video_allowed(db, video, user, playable=False):
    if not video:
        return False
    owner = user is not None and video.owner_id == user.id
    if video.hidden and (playable or not owner):
        return False
    if video.status != 'ready' and (playable or not owner):
        return False
    return video.visibility != 'private' or owner or (user is not None and db.get(m.Access,(video.id,user.id)) is not None)
def get_video(db,video_id,user,playable=False):
    video = db.get(m.Video,video_id)
    if not video_allowed(db,video,user,playable):
        raise HTTPException(404,'Video not found')
    return video
def owned_video(db,video_id,user):
    video = db.get(m.Video,video_id)
    if not video or video.owner_id != user.id:
        raise HTTPException(404,'Video not found')
    return video
def video_json(db,video,user):
    owner = db.get(m.User,video.owner_id)
    likes = db.scalar(select(func.count()).select_from(m.Like).where(m.Like.video_id==video.id))
    result = {key:getattr(video,key) for key in ('id','title','description','owner_id','visibility','status','hidden','views','duration','created_at')}
    result.update(owner_name=owner.display_name,likes=likes,liked=bool(user and db.get(m.Like,(video.id,user.id))),thumbnail_url=f'/api/videos/{video.id}/thumbnail' if video.thumbnail_key else None)
    if user and user.id == video.owner_id:
        result['error'] = video.error
    return result
@app.post('/api/auth/register',status_code=201)
def register(data:s.Registration,response:Response,db:DBSession=Depends(get_db)):
    if db.scalar(select(m.User).where(m.User.email==data.email)):
        raise HTTPException(409,'Email already registered')
    user=m.User(email=data.email,display_name=data.display_name,password_hash=hash_password(data.password),role='user')
    db.add(user)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409,'Email already registered')
    issue_session(db,user,response)
    return user_json(user)
@app.post('/api/auth/login')
def login(data:s.Credentials,response:Response,db:DBSession=Depends(get_db)):
    user=db.scalar(select(m.User).where(m.User.email==data.email))
    if not user or not verify_password(data.password,user.password_hash):
        raise HTTPException(401,'Invalid email or password')
    issue_session(db,user,response)
    return user_json(user)
@app.post('/api/auth/logout',status_code=204)
def logout(request:Request,response:Response,db:DBSession=Depends(get_db)):
    token=request.cookies.get('session')
    if token:
        db.execute(delete(m.Session).where(m.Session.token_hash==hashlib.sha256(token.encode()).hexdigest()))
        db.commit()
    response.delete_cookie('session',path='/')
@app.get('/api/auth/me')
def me(user=Depends(require_user)):
    return user_json(user)
@app.get('/api/videos')
def videos(q:str='',channel_id:int|None=None,mine:bool=False,user=Depends(current_user),db:DBSession=Depends(get_db)):
    statement=select(m.Video)
    if mine:
        if not user:
            raise HTTPException(401,'Sign in required')
        statement=statement.where(m.Video.owner_id==user.id)
    else:
        statement=statement.where(m.Video.visibility=='public',m.Video.status=='ready',m.Video.hidden.is_(False))
    if channel_id is not None:
        statement=statement.where(m.Video.owner_id==channel_id)
    if q:
        statement=statement.where(m.Video.title.ilike('%'+q.replace('%','\\%').replace('_','\\_')+'%',escape='\\'))
    rows=db.scalars(statement.order_by(m.Video.created_at.desc()).limit(100)).all()
    return [video_json(db,v,user) for v in rows]
@app.post('/api/videos',status_code=202)
def upload(file:UploadFile=File(...),title:str=Form(...),description:str=Form(''),visibility:str=Form('public'),user=Depends(require_user),db:DBSession=Depends(get_db)):
    title=title.strip()
    if not title or len(title)>200 or len(description)>10000 or visibility not in ('public','private','unlisted'):
        raise HTTPException(422,'Invalid video metadata')
    key='sources/'+uuid.uuid4().hex
    storage=get_storage()
    size=0
    with tempfile.NamedTemporaryFile() as temp:
        while True:
            chunk=file.file.read(1024*1024)
            if not chunk:
                break
            size+=len(chunk)
            if size>config.MAX_UPLOAD_BYTES:
                raise HTTPException(413,'Video exceeds upload size limit')
            temp.write(chunk)
        if not size:
            raise HTTPException(422,'Empty video file')
        temp.flush()
        storage.put_file(key,temp.name)
    video=m.Video(owner_id=user.id,title=title,description=description,visibility=visibility,status='uploaded',source_key=key)
    try:
        db.add(video)
        db.flush()
        db.add(m.Job(video_id=video.id))
        db.commit()
    except Exception:
        db.rollback()
        storage.delete(key)
        raise
    return video_json(db,video,user)
@app.get('/api/videos/{video_id}')
def video_detail(video_id:int,user=Depends(current_user),db:DBSession=Depends(get_db)):
    return video_json(db,get_video(db,video_id,user),user)
@app.patch('/api/videos/{video_id}')
def edit_video(video_id:int,data:s.VideoEdit,user=Depends(require_user),db:DBSession=Depends(get_db)):
    video=owned_video(db,video_id,user)
    for key,value in data.model_dump(exclude_none=True).items():
        if key=='title':
            value=value.strip()
            if not value:
                raise HTTPException(422,'Title is required')
        setattr(video,key,value)
    db.commit()
    return video_json(db,video,user)
@app.delete('/api/videos/{video_id}',status_code=204)
def delete_video(video_id:int,user=Depends(require_user),db:DBSession=Depends(get_db)):
    video=owned_video(db,video_id,user)
    storage=get_storage()
    for key in (video.source_key,video.media_key,video.thumbnail_key):
        if key:
            storage.delete(key)
    db.delete(video)
    db.commit()

def serve_object(video_id,kind,request,user,db):
    video=get_video(db,video_id,user,playable=True)
    key=video.media_key if kind=='media' else video.thumbnail_key
    if not key:
        raise HTTPException(404,'Media not found')
    storage=get_storage()
    try:
        total=storage.size(key)
    except (FileNotFoundError,KeyError):
        raise HTTPException(404,'Media not found')
    start,end=0,total-1
    status=200
    range_header=request.headers.get('range')
    if range_header:
        match=re.fullmatch(r'bytes=(\d*)-(\d*)',range_header)
        if not match or not any(match.groups()):
            raise HTTPException(416,'Invalid byte range',headers={'Content-Range':f'bytes */{total}'})
        left,right=match.groups()
        if left:
            start=int(left)
            end=min(int(right),total-1) if right else total-1
        else:
            start=max(total-int(right),0)
        if start>=total or start>end:
            raise HTTPException(416,'Unsatisfiable byte range',headers={'Content-Range':f'bytes */{total}'})
        status=206
    obj=storage.read(key,start,end)
    def chunks():
        remaining=end-start+1
        try:
            while remaining:
                data=obj['body'].read(min(1024*1024,remaining))
                if not data:
                    break
                remaining-=len(data)
                yield data
        finally:
            obj['body'].close()
    headers={'Accept-Ranges':'bytes','Content-Length':str(end-start+1),'Cache-Control':'private, no-store'}
    if status==206:
        headers['Content-Range']=f'bytes {start}-{end}/{total}'
    return StreamingResponse(chunks(),status_code=status,media_type='video/mp4' if kind=='media' else 'image/jpeg',headers=headers)
@app.get('/api/videos/{video_id}/media')
def media(video_id:int,request:Request,user=Depends(current_user),db:DBSession=Depends(get_db)):
    return serve_object(video_id,'media',request,user,db)
@app.get('/api/videos/{video_id}/thumbnail')
def thumbnail(video_id:int,request:Request,user=Depends(current_user),db:DBSession=Depends(get_db)):
    return serve_object(video_id,'thumbnail',request,user,db)
@app.post('/api/videos/{video_id}/view')
def record_view(video_id:int,user=Depends(current_user),db:DBSession=Depends(get_db)):
    video=get_video(db,video_id,user,True)
    db.execute(update(m.Video).where(m.Video.id==video.id).values(views=m.Video.views+1))
    db.commit()
    return {'ok':True}
@app.put('/api/videos/{video_id}/like')
def like_video(video_id:int,user=Depends(require_user),db:DBSession=Depends(get_db)):
    video=get_video(db,video_id,user,True)
    if not db.get(m.Like,(video.id,user.id)):
        db.add(m.Like(video_id=video.id,user_id=user.id))
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
    return {'liked':True,'likes':db.scalar(select(func.count()).select_from(m.Like).where(m.Like.video_id==video.id))}
@app.delete('/api/videos/{video_id}/like')
def unlike_video(video_id:int,user=Depends(require_user),db:DBSession=Depends(get_db)):
    video=get_video(db,video_id,user,True)
    db.execute(delete(m.Like).where(m.Like.video_id==video.id,m.Like.user_id==user.id))
    db.commit()
    return {'liked':False,'likes':db.scalar(select(func.count()).select_from(m.Like).where(m.Like.video_id==video.id))}
@app.get('/api/videos/{video_id}/comments')
def comments(video_id:int,user=Depends(current_user),db:DBSession=Depends(get_db)):
    get_video(db,video_id,user,True)
    return [{'id':c.id,'body':c.body,'user_id':c.user_id,'display_name':u.display_name,'created_at':c.created_at} for c,u in db.execute(select(m.Comment,m.User).join(m.User,m.Comment.user_id==m.User.id).where(m.Comment.video_id==video_id).order_by(m.Comment.created_at.desc()).limit(200))]
@app.post('/api/videos/{video_id}/comments',status_code=201)
def add_comment(video_id:int,data:s.CommentInput,user=Depends(require_user),db:DBSession=Depends(get_db)):
    get_video(db,video_id,user,True)
    if not data.body.strip():
        raise HTTPException(422,'Comment is empty')
    comment=m.Comment(video_id=video_id,user_id=user.id,body=data.body.strip())
    db.add(comment)
    db.commit()
    return {'id':comment.id,'body':comment.body,'user_id':user.id,'display_name':user.display_name,'created_at':comment.created_at}
@app.delete('/api/comments/{comment_id}',status_code=204)
def remove_comment(comment_id:int,user=Depends(require_user),db:DBSession=Depends(get_db)):
    comment=db.get(m.Comment,comment_id)
    if not comment or (comment.user_id!=user.id and user.role not in ('moderator','admin')):
        raise HTTPException(404,'Comment not found')
    db.delete(comment)
    db.commit()
@app.get('/api/videos/{video_id}/access')
def list_access(video_id:int,user=Depends(require_user),db:DBSession=Depends(get_db)):
    owned_video(db,video_id,user)
    return [user_json(u) for u in db.scalars(select(m.User).join(m.Access,m.Access.user_id==m.User.id).where(m.Access.video_id==video_id))]
@app.put('/api/videos/{video_id}/access')
def grant_access(video_id:int,data:s.EmailInput,user=Depends(require_user),db:DBSession=Depends(get_db)):
    owned_video(db,video_id,user)
    recipient=db.scalar(select(m.User).where(m.User.email==data.email.strip().lower()))
    if not recipient:
        raise HTTPException(404,'User not found')
    if not db.get(m.Access,(video_id,recipient.id)):
        db.add(m.Access(video_id=video_id,user_id=recipient.id))
        db.commit()
    return user_json(recipient)
@app.delete('/api/videos/{video_id}/access/{user_id}',status_code=204)
def revoke_access(video_id:int,user_id:int,user=Depends(require_user),db:DBSession=Depends(get_db)):
    owned_video(db,video_id,user)
    db.execute(delete(m.Access).where(m.Access.video_id==video_id,m.Access.user_id==user_id))
    db.commit()
@app.get('/api/users/{user_id}')
def channel(user_id:int,user=Depends(current_user),db:DBSession=Depends(get_db)):
    owner=db.get(m.User,user_id)
    if not owner:
        raise HTTPException(404,'Channel not found')
    return {'id':owner.id,'display_name':owner.display_name,'subscribers':db.scalar(select(func.count()).select_from(m.Subscription).where(m.Subscription.channel_id==user_id)),'subscribed':bool(user and db.get(m.Subscription,(user.id,user_id)))}
@app.put('/api/users/{user_id}/subscribe')
def subscribe(user_id:int,user=Depends(require_user),db:DBSession=Depends(get_db)):
    if not db.get(m.User,user_id):
        raise HTTPException(404,'Channel not found')
    if user_id==user.id:
        raise HTTPException(422,'Cannot subscribe to yourself')
    if not db.get(m.Subscription,(user.id,user_id)):
        db.add(m.Subscription(user_id=user.id,channel_id=user_id))
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
    return channel(user_id,user,db)
@app.delete('/api/users/{user_id}/subscribe')
def unsubscribe(user_id:int,user=Depends(require_user),db:DBSession=Depends(get_db)):
    db.execute(delete(m.Subscription).where(m.Subscription.user_id==user.id,m.Subscription.channel_id==user_id))
    db.commit()
    return channel(user_id,user,db)
@app.get('/api/playlists')
def playlists(channel_id:int|None=None,user=Depends(current_user),db:DBSession=Depends(get_db)):
    statement=select(m.Playlist)
    if channel_id is not None:
        statement=statement.where(m.Playlist.owner_id==channel_id)
    result=[]
    for playlist in db.scalars(statement.order_by(m.Playlist.id.desc()).limit(100)):
        items=db.scalars(select(m.Video).join(m.PlaylistItem,m.PlaylistItem.video_id==m.Video.id).where(m.PlaylistItem.playlist_id==playlist.id)).all()
        result.append({'id':playlist.id,'title':playlist.title,'owner_id':playlist.owner_id,'videos':[video_json(db,v,user) for v in items if video_allowed(db,v,user,True)]})
    return result
@app.post('/api/playlists',status_code=201)
def create_playlist(data:s.PlaylistInput,user=Depends(require_user),db:DBSession=Depends(get_db)):
    if not data.title.strip():
        raise HTTPException(422,'Title is required')
    playlist=m.Playlist(owner_id=user.id,title=data.title.strip())
    db.add(playlist)
    db.commit()
    return {'id':playlist.id,'title':playlist.title,'owner_id':user.id,'videos':[]}
def owned_playlist(db,playlist_id,user):
    playlist=db.get(m.Playlist,playlist_id)
    if not playlist or playlist.owner_id!=user.id:
        raise HTTPException(404,'Playlist not found')
    return playlist
@app.put('/api/playlists/{playlist_id}/videos/{video_id}')
def add_to_playlist(playlist_id:int,video_id:int,user=Depends(require_user),db:DBSession=Depends(get_db)):
    owned_playlist(db,playlist_id,user)
    get_video(db,video_id,user,True)
    if not db.get(m.PlaylistItem,(playlist_id,video_id)):
        db.add(m.PlaylistItem(playlist_id=playlist_id,video_id=video_id))
        db.commit()
    return {'ok':True}
@app.delete('/api/playlists/{playlist_id}/videos/{video_id}',status_code=204)
def remove_from_playlist(playlist_id:int,video_id:int,user=Depends(require_user),db:DBSession=Depends(get_db)):
    owned_playlist(db,playlist_id,user)
    db.execute(delete(m.PlaylistItem).where(m.PlaylistItem.playlist_id==playlist_id,m.PlaylistItem.video_id==video_id))
    db.commit()
@app.post('/api/videos/{video_id}/reports',status_code=201)
def report_video(video_id:int,data:s.ReportInput,user=Depends(require_user),db:DBSession=Depends(get_db)):
    get_video(db,video_id,user,True)
    report=m.Report(video_id=video_id,user_id=user.id,reason=data.reason)
    db.add(report)
    db.commit()
    return {'id':report.id}
@app.get('/api/moderation/reports')
def list_reports(user=Depends(require_staff),db:DBSession=Depends(get_db)):
    return [{'id':r.id,'video_id':v.id,'title':v.title,'hidden':v.hidden,'reason':r.reason,'user_id':r.user_id,'created_at':r.created_at} for r,v in db.execute(select(m.Report,m.Video).join(m.Video,m.Report.video_id==m.Video.id).order_by(m.Report.created_at.desc()).limit(200))]
@app.post('/api/moderation/videos/{video_id}/hide')
def hide_video(video_id:int,data:s.HideInput,user=Depends(require_staff),db:DBSession=Depends(get_db)):
    video=db.get(m.Video,video_id)
    if not video:
        raise HTTPException(404,'Video not found')
    video.hidden=data.hidden
    db.add(m.Audit(actor_id=user.id,action='hide_video' if data.hidden else 'restore_video',target=str(video.id)))
    db.commit()
    return {'id':video.id,'hidden':video.hidden}
@app.get('/api/admin/users')
def list_users(user=Depends(require_admin),db:DBSession=Depends(get_db)):
    return [user_json(u) for u in db.scalars(select(m.User).order_by(m.User.id).limit(500))]
@app.patch('/api/admin/users/{user_id}/role')
def set_role(user_id:int,data:s.RoleInput,user=Depends(require_admin),db:DBSession=Depends(get_db)):
    target=db.get(m.User,user_id)
    if not target:
        raise HTTPException(404,'User not found')
    if target.id==user.id and data.role!='admin':
        raise HTTPException(422,'Cannot remove your own administrator role')
    target.role=data.role
    db.add(m.Audit(actor_id=user.id,action='set_role:'+data.role,target=str(target.id)))
    db.commit()
    return user_json(target)
@app.get('/health/live')
def live():
    return {'status':'ok'}
@app.get('/health/ready')
def ready(db:DBSession=Depends(get_db)):
    try:
        db.execute(text('SELECT 1'))
        get_storage().ensure()
    except Exception:
        raise HTTPException(503,'Dependencies unavailable')
    return {'status':'ready'}
frontend=Path(os.getenv('FRONTEND_PATH',Path(__file__).resolve().parents[2]/'frontend'))
if frontend.exists():
    app.mount('/static',StaticFiles(directory=str(frontend)),name='static')
    @app.get('/',include_in_schema=False)
    def index():
        return FileResponse(frontend/'index.html')
