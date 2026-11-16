#!/usr/bin/env python3
"""Real PostgreSQL/S3/FFmpeg worker acceptance in a disposable schema and bucket.

Run inside the app image: python scripts/integration_runtime.py
Does not stop the deployed worker or alter application data. Requires permission
for CREATE SCHEMA and temporary S3 buckets (the bundled Compose credentials do).
Unlike application unit tests, no encoder, storage, database or worker is mocked.
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import uuid


def check(condition, message):
    if not condition:
        raise AssertionError(message)


def main():
    from sqlalchemy import create_engine, select, text
    from sqlalchemy.engine import make_url

    check(os.environ.get('STORAGE_BACKEND', 's3') == 's3', 'S3 storage required')
    original_url = make_url(os.environ['DATABASE_URL'])
    check(original_url.get_backend_name() == 'postgresql', 'PostgreSQL required')
    token = uuid.uuid4().hex
    schema = 'runtime_' + token
    bucket = 'runtime-' + token
    # A distinct bucket prevents media/{id} from colliding with application IDs.
    os.environ['S3_BUCKET'] = bucket
    os.environ['DATABASE_URL'] = original_url.update_query_dict({'options': '-csearch_path=' + schema}).render_as_string(hide_password=False)
    root = Path(__file__).resolve().parents[1]
    import_root = root / 'backend' if (root / 'backend' / 'app').is_dir() else root
    sys.path.insert(0, str(import_root))
    os.environ['PYTHONPATH'] = str(import_root)
    from app.db import Base, SessionLocal, engine
    from app.models import Job, User, Video
    from app.storage import get_storage

    control_engine = create_engine(original_url)
    storage = get_storage()
    schema_created = bucket_created = False
    running = []
    child_code = 'from app.worker import process_one; print("CLAIM_RESULT=" + str(process_one()), flush=True)'

    def worker_start():
        child = subprocess.Popen([sys.executable, '-c', child_code], cwd=root,
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                 text=True, start_new_session=True)
        running.append(child)
        return child

    def worker_result(child):
        stdout, stderr = child.communicate(timeout=120)
        check(child.returncode == 0, f'Worker exited {child.returncode}: {stderr[-2000:]}')
        check('CLAIM_RESULT=' in stdout, 'Worker did not return its claim result')
        return 'CLAIM_RESULT=True' in stdout

    def seed_job(path, label):
        key = 'sources/' + uuid.uuid4().hex
        storage.put_file(key, path)
        with SessionLocal() as db:
            video = Video(owner_id=owner_id, title=label, visibility='public', source_key=key)
            db.add(video)
            db.flush()
            job = Job(video_id=video.id)
            db.add(job)
            db.commit()
            return video.id, job.id

    def state(video_id, job_id):
        with SessionLocal() as db:
            video, job = db.get(Video, video_id), db.get(Job, job_id)
            return video.status, job.status, job.attempts

    def verify_output(video_id):
        with SessionLocal() as db:
            video = db.get(Video, video_id)
            check(video.status == 'ready', 'Expected ready video')
            media_key, thumbnail_key = video.media_key, video.thumbnail_key
        check(storage.size(media_key) > 100, 'Encoded S3 MP4 is empty')
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'output.mp4'
            storage.download(media_key, output)
            probe = subprocess.run(['ffprobe', '-v', 'error', '-show_streams', '-of', 'json', str(output)], check=True, capture_output=True, text=True, timeout=20)
            streams = json.loads(probe.stdout)['streams']
            check(any(s.get('codec_name') == 'h264' for s in streams), 'Output is not H.264')
        obj = storage.read(thumbnail_key, 0, 1)
        try:
            check(obj['body'].read() == b'\xff\xd8', 'Thumbnail is not JPEG')
        finally:
            obj['body'].close()

    try:
        with control_engine.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA {schema}'))
        schema_created = True
        Base.metadata.create_all(engine)
        storage.ensure()
        bucket_created = True
        with SessionLocal() as db:
            owner = User(email=f'{token}@runtime.local', display_name='Runtime test', password_hash='unused', role='user')
            db.add(owner)
            db.commit()
            owner_id = owner.id
        with tempfile.TemporaryDirectory(prefix='youtube-runtime-') as directory:
            fixture = Path(directory) / 'fixture.mp4'
            subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'testsrc2=size=960x540:rate=24:duration=8', '-f', 'lavfi', '-i', 'sine=frequency=440:duration=8', '-c:v', 'libx264', '-preset', 'ultrafast', '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-shortest', '-y', str(fixture)], check=True, timeout=60)
            first = seed_job(fixture, 'Locked queue head')
            second = seed_job(fixture, 'Skip locked claim')
            # Keep the oldest queued job locked in a real PostgreSQL transaction.
            with SessionLocal() as locking_db:
                locking_db.scalar(select(Job).where(Job.id == first[1]).with_for_update())
                check(worker_result(worker_start()), 'Worker did not skip locked head')
                check(state(*first) == ('uploaded', 'queued', 0), 'Worker touched the locked job')
                check(state(*second) == ('ready', 'done', 1), 'Unlocked job was not processed exactly once')
                locking_db.rollback()
            print('PASS: PostgreSQL FOR UPDATE SKIP LOCKED skips a locked queue head', flush=True)
            third = seed_job(fixture, 'Parallel worker claim')
            children = [worker_start(), worker_start()]
            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(worker_result, children))
            check(results == [True, True], 'Parallel workers did not claim both jobs')
            for video_id, job_id in (first, second, third):
                check(state(video_id, job_id) == ('ready', 'done', 1), 'Job duplicated or unfinished')
                verify_output(video_id)
            print('PASS: parallel worker processes claim distinct jobs exactly once; real S3 H.264/JPEG outputs verified', flush=True)

            crashed = seed_job(fixture, 'Killed worker recovery')
            child = worker_start()
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                if state(*crashed) == ('processing', 'processing', 1):
                    os.killpg(child.pid, signal.SIGKILL)
                    child.communicate(timeout=10)
                    break
                check(child.poll() is None, 'Worker finished before interruption checkpoint')
                time.sleep(0.005)
            else:
                raise AssertionError('Worker did not claim the restart fixture')
            check(child.returncode == -signal.SIGKILL, 'Worker was not actually killed')
            check(state(*crashed) == ('processing', 'processing', 1), 'Interrupted job did not persist')
            check(not worker_result(worker_start()), 'Fresh unexpired claim was stolen')
            with SessionLocal() as db:
                db.get(Job, crashed[1]).claimed_at = datetime.utcnow() - timedelta(minutes=36)
                db.commit()
            check(worker_result(worker_start()), 'Restarted worker did not reclaim stale claim')
            check(state(*crashed) == ('ready', 'done', 2), 'Stale claim recovery did not complete on second attempt')
            verify_output(crashed[0])
            print('PASS: SIGKILL preserves processing claim; fresh lease excluded; expired lease reclaimed by restarted worker', flush=True)

            malformed = Path(directory) / 'malformed.mp4'
            malformed.write_bytes(b'not an mp4 container')
            broken = seed_job(malformed, 'Malformed rejection')
            check(worker_result(worker_start()), 'Malformed job was not claimed')
            check(state(*broken) == ('failed', 'failed', 1), 'Malformed upload was published or not rejected')
            with SessionLocal() as db:
                video = db.get(Video, broken[0])
                check(video.media_key is None and video.thumbnail_key is None, 'Malformed outputs published')
                source_key = video.source_key
            try:
                storage.size(source_key)
            except FileNotFoundError:
                pass
            else:
                raise AssertionError('Rejected source was not cleaned from S3')
            check(not worker_result(worker_start()), 'Completed or failed jobs were reprocessed')
            print('PASS: real ffprobe rejects malformed upload, cleans source and never publishes media', flush=True)
    finally:
        for child in running:
            if child.poll() is None:
                os.killpg(child.pid, signal.SIGKILL)
                child.communicate(timeout=10)
        engine.dispose()
        if bucket_created:
            for page in storage.client.get_paginator('list_objects_v2').paginate(Bucket=bucket):
                objects = [{'Key': item['Key']} for item in page.get('Contents', [])]
                if objects:
                    storage.client.delete_objects(Bucket=bucket, Delete={'Objects': objects})
            storage.client.delete_bucket(Bucket=bucket)
        if schema_created:
            with control_engine.begin() as connection:
                connection.execute(text(f'DROP SCHEMA {schema} CASCADE'))
        control_engine.dispose()
        print('CLEANUP: disposable schema and S3 bucket removed', flush=True)


if __name__ == '__main__':
    main()
