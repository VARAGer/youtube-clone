"""Durable PostgreSQL job queue. Only normalized MP4/JPEG outputs are served."""
from datetime import datetime, timedelta
import json
import logging
from pathlib import Path
import subprocess
import tempfile
import time
from sqlalchemy import select, or_, and_
from .db import SessionLocal, init_db
from .models import Job, Video
from .storage import get_storage
log=logging.getLogger(__name__)

def run(command,timeout):
    return subprocess.run(command,check=True,capture_output=True,text=True,timeout=timeout)
def delete_objects(storage, keys, video_id):
    # Cleanup is best-effort per object: one failed delete must not skip others.
    for key in keys:
        try:
            storage.delete(key)
        except Exception:
            log.exception('Failed to clean object for video %s',video_id)

def owns_claim(job, attempt):
    return job is not None and job.status == 'processing' and job.attempts == attempt

def process_one():
    cutoff=datetime.utcnow()-timedelta(minutes=35)
    with SessionLocal() as db:
        job=db.scalar(select(Job).where(or_(Job.status=='queued',and_(Job.status=='processing',Job.claimed_at<cutoff))).order_by(Job.id).with_for_update(skip_locked=True).limit(1))
        if not job:
            return False
        video=db.get(Video,job.video_id)
        job.status='processing'
        job.attempts+=1
        job.claimed_at=datetime.utcnow()
        if video:
            video.status='processing'
        db.commit()
        job_id,video_id,source_key,attempt=job.id,video.id,video.source_key,job.attempts
    storage=get_storage()
    # Every claimant writes immutable, attempt-specific objects. Fencing only the
    # database update would still let a stale claimant overwrite S3 before CAS.
    prefix=f'media/{video_id}/job-{job_id}-attempt-{attempt}'
    media_key=f'{prefix}/video.mp4'
    thumbnail_key=f'{prefix}/thumbnail.jpg'
    published=False
    cleanup_source=False
    try:
        with tempfile.TemporaryDirectory(prefix='youtube-job-') as directory:
            root=Path(directory)
            source=root/'source'
            output=root/'video.mp4'
            thumbnail=root/'thumbnail.jpg'
            storage.download(source_key,source)
            probe=json.loads(run(['ffprobe','-v','error','-protocol_whitelist','file,pipe','-format_whitelist','mov,matroska,webm,avi,mpegts,mpegvideo,ogg,flv','-show_format','-show_streams','-of','json',str(source)],30).stdout)
            streams=[s for s in probe.get('streams',[]) if s.get('codec_type')=='video' and not s.get('disposition',{}).get('attached_pic')]
            if not streams:
                raise ValueError('No playable video stream')
            stream_index=int(streams[0]['index'])
            if stream_index < 0:
                raise ValueError('Invalid video stream index')
            duration=float(probe.get('format',{}).get('duration') or streams[0].get('duration') or 0)
            if not 0<duration<=7200:
                raise ValueError('Video duration must be between 0 and 7200 seconds')
            if int(streams[0].get('width',0))*int(streams[0].get('height',0))>3840*2160:
                raise ValueError('Video resolution exceeds 4K limit')
            # No shell interpolation, user paths, URLs, playlists or external protocols.
            run(['ffmpeg','-nostdin','-v','error','-protocol_whitelist','file,pipe','-format_whitelist','mov,matroska,webm,avi,mpegts,mpegvideo,ogg,flv','-i',str(source),'-map',f'0:{stream_index}','-map','0:a:0?','-sn','-dn','-map_metadata','-1','-vf','scale=1280:720:force_original_aspect_ratio=decrease:force_divisible_by=2','-c:v','libx264','-preset','veryfast','-crf','24','-pix_fmt','yuv420p','-c:a','aac','-b:a','128k','-movflags','+faststart','-threads','2','-y',str(output)],1800)
            run(['ffmpeg','-nostdin','-v','error','-ss',str(min(duration/2,5)),'-i',str(output),'-frames:v','1','-vf','scale=640:-2','-y',str(thumbnail)],60)
            storage.put_file(media_key,output,'video/mp4')
            storage.put_file(thumbnail_key,thumbnail,'image/jpeg')
            with SessionLocal() as db:
                # Serialize publication with reclaim, then compare the captured
                # generation. A current job must still be processing this attempt.
                job=db.scalar(select(Job).where(Job.id==job_id).with_for_update())
                video=db.get(Video,video_id)
                if owns_claim(job,attempt) and video:
                    video.media_key=media_key
                    video.thumbnail_key=thumbnail_key
                    video.duration=duration
                    video.status='ready'
                    video.error=None
                    job.status='done'
                    db.commit()
                    published=True
                    cleanup_source=True
    except Exception:
        log.exception('Video processing failed for video %s attempt %s',video_id,attempt)
        with SessionLocal() as db:
            job=db.scalar(select(Job).where(Job.id==job_id).with_for_update())
            video=db.get(Video,video_id)
            if owns_claim(job,attempt):
                if video:
                    video.status='failed'
                    video.error='Unsupported or damaged video, or processing failed. Try uploading an MP4 video.'
                job.status='failed'
                db.commit()
                cleanup_source=True
            elif job is not None and job.status=='done' and job.attempts==attempt:
                # A lost commit acknowledgement may follow a successful commit.
                # Preserve objects referenced by that completed publication.
                published=bool(video and video.media_key==media_key and video.thumbnail_key==thumbnail_key)
                cleanup_source=published
    if not published:
        delete_objects(storage,(media_key,thumbnail_key),video_id)
    # Only the attempt that committed its terminal state owns source cleanup.
    # Obsolete attempts must preserve the shared input for the new claimant.
    if cleanup_source:
        delete_objects(storage,(source_key,),video_id)
    return True

def main():
    logging.basicConfig(level=logging.INFO)
    init_db()
    get_storage().ensure()
    while True:
        try:
            if not process_one():
                time.sleep(2)
        except Exception:
            log.exception('Worker polling failed')
            time.sleep(5)
if __name__=='__main__':
    main()
