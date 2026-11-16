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
        job_id,video_id,source_key=job.id,video.id,video.source_key
    storage=get_storage()
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
            duration=float(probe.get('format',{}).get('duration') or streams[0].get('duration') or 0)
            if not 0<duration<=7200:
                raise ValueError('Video duration must be between 0 and 7200 seconds')
            if int(streams[0].get('width',0))*int(streams[0].get('height',0))>3840*2160:
                raise ValueError('Video resolution exceeds 4K limit')
            # No shell interpolation, user paths, URLs, playlists or external protocols.
            run(['ffmpeg','-nostdin','-v','error','-protocol_whitelist','file,pipe','-format_whitelist','mov,matroska,webm,avi,mpegts,mpegvideo,ogg,flv','-i',str(source),'-map','0:v:0','-map','0:a:0?','-sn','-dn','-map_metadata','-1','-vf','scale=1280:720:force_original_aspect_ratio=decrease:force_divisible_by=2','-c:v','libx264','-preset','veryfast','-crf','24','-pix_fmt','yuv420p','-c:a','aac','-b:a','128k','-movflags','+faststart','-threads','2','-y',str(output)],1800)
            run(['ffmpeg','-nostdin','-v','error','-ss',str(min(duration/2,5)),'-i',str(output),'-frames:v','1','-vf','scale=640:-2','-y',str(thumbnail)],60)
            media_key=f'media/{video_id}/video.mp4'
            thumbnail_key=f'media/{video_id}/thumbnail.jpg'
            storage.put_file(media_key,output,'video/mp4')
            storage.put_file(thumbnail_key,thumbnail,'image/jpeg')
            with SessionLocal() as db:
                video=db.get(Video,video_id)
                job=db.get(Job,job_id)
                if video and job:
                    video.media_key=media_key
                    video.thumbnail_key=thumbnail_key
                    video.duration=duration
                    video.status='ready'
                    video.error=None
                    job.status='done'
                    db.commit()
                else:
                    storage.delete(media_key)
                    storage.delete(thumbnail_key)
            # Once the publication transaction succeeds, a cleanup outage must
            # not turn a playable video into a failure and delete its outputs.
            try:
                storage.delete(source_key)
            except Exception:
                log.exception('Failed to clean source for published video %s',video_id)
    except Exception as error:
        log.exception('Video processing failed for video %s',video_id)
        try:
            storage.delete(source_key)
            storage.delete(f'media/{video_id}/video.mp4')
            storage.delete(f'media/{video_id}/thumbnail.jpg')
        except Exception:
            log.exception('Failed to clean objects for video %s',video_id)
        with SessionLocal() as db:
            video=db.get(Video,video_id)
            job=db.get(Job,job_id)
            if video:
                video.status='failed'
                video.error='Unsupported or damaged video, or processing failed. Try uploading an MP4 video.'
            if job:
                job.status='failed'
            db.commit()
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
