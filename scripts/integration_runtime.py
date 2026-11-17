#!/usr/bin/env python3
"""Real PostgreSQL/S3/FFmpeg worker acceptance in a disposable schema and bucket.

Run inside the app image: python scripts/integration_runtime.py
Does not stop the deployed worker or alter application data. Requires permission
for CREATE SCHEMA and temporary S3 buckets (the bundled Compose credentials do).
Unlike application unit tests, encoding, storage and database operations are real.
Test-only child wrappers pause after a real ffprobe/owner lookup and observe
real S3 uploads to make races deterministic; the actual worker and deletion
functions still execute unchanged and no operation response is fabricated.
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
import json
import hashlib
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
    child_code = r'''
import json, os, signal
from pathlib import Path
from app import worker
from app.storage import S3Storage
real_run = worker.run
checkpoint = os.environ.get('RUNTIME_CHECKPOINT')
if checkpoint:
    # Observe actual completed uploads; do not replace their implementation.
    # Identical re-encoding can have identical bytes, which alone would hide an
    # illegal overwrite by an obsolete worker.
    real_put = S3Storage.put_file
    def observe_put(storage, key, path, content_type='application/octet-stream'):
        result = real_put(storage, key, path, content_type)
        with Path(checkpoint + '.uploads').open('a') as trace:
            trace.write(json.dumps({'key': key}) + '\n')
        return result
    S3Storage.put_file = observe_put
    def run_and_pause(command, timeout):
        result = real_run(command, timeout)
        if command[0] == 'ffprobe':
            Path(checkpoint).write_text(json.dumps({'source': command[-1]}))
            os.kill(os.getpid(), signal.SIGSTOP)
        return result
    worker.run = run_and_pause
print('CLAIM_RESULT=' + str(worker.process_one()), flush=True)
'''


    def worker_start(checkpoint=None):
        child_env = dict(os.environ)
        if checkpoint is not None:
            child_env['RUNTIME_CHECKPOINT'] = str(checkpoint)
        child = subprocess.Popen([sys.executable, '-c', child_code], cwd=root, env=child_env,
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                 text=True, start_new_session=True)
        running.append(child)
        return child

    def wait_checkpoint(child, marker):
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            check(child.poll() is None, 'Child exited before its deterministic checkpoint')
            status = Path(f'/proc/{child.pid}/status')
            if marker.exists() and status.exists():
                state_line = next(line for line in status.read_text().splitlines() if line.startswith('State:'))
                if state_line.split()[1] in ('T', 't'):
                    return json.loads(marker.read_text())
            time.sleep(0.01)
        raise AssertionError('Child did not reach SIGSTOP checkpoint')

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

    def verify_output(video_id, minimum_frames=0, minimum_duration=0):
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
            video_streams = [s for s in streams if s.get('codec_name') == 'h264']
            check(bool(video_streams), 'Output is not H.264')
            if minimum_frames:
                check(int(video_streams[0].get('nb_frames', 0)) >= minimum_frames, 'Output lost the actual video frames')
            if minimum_duration:
                check(float(video_streams[0].get('duration', 0)) >= minimum_duration, 'Output video duration was truncated')
        obj = storage.read(thumbnail_key, 0, 1)
        try:
            check(obj['body'].read() == b'\xff\xd8', 'Thumbnail is not JPEG')
        finally:
            obj['body'].close()

    def publication_snapshot(video_id):
        with SessionLocal() as db:
            video = db.get(Video, video_id)
            check(video.status == 'ready', 'Expected a published winner')
            keys = (video.media_key, video.thumbnail_key)
        contents = []
        for key in keys:
            obj = storage.read(key)
            try:
                contents.append(hashlib.sha256(obj['body'].read()).hexdigest())
            finally:
                obj['body'].close()
        return keys, tuple(contents)

    def assert_only_published_outputs(video_id, keys):
        actual = set()
        for page in storage.client.get_paginator('list_objects_v2').paginate(Bucket=bucket, Prefix=f'media/{video_id}/'):
            actual.update(item['Key'] for item in page.get('Contents', []))
        check(actual == set(keys), 'Obsolete attempt objects were retained or winner objects removed')

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

            # Keep the old claimant alive after a real download/probe. A newer
            # process must win after expiry; resuming the old process cannot
            # overwrite, fail or clean the newer publication.
            for break_old_input in (False, True):
                live = seed_job(fixture, 'Live stale failure' if break_old_input else 'Live stale success')
                marker = Path(directory) / ('stale-failure.json' if break_old_input else 'stale-success.json')
                old = worker_start(marker)
                stopped = wait_checkpoint(old, marker)
                check(state(*live) == ('processing', 'processing', 1), 'Old worker did not hold its first claim')
                with SessionLocal() as db:
                    db.get(Job, live[1]).claimed_at = datetime.utcnow() - timedelta(minutes=36)
                    db.commit()
                check(worker_result(worker_start()), 'Replacement worker did not reclaim expired live claim')
                check(state(*live) == ('ready', 'done', 2), 'Replacement worker did not publish')
                winner = publication_snapshot(live[0])
                if break_old_input:
                    # Delete the old process's downloaded file so its real
                    # ffmpeg subprocess fails after SIGCONT, without mocking it.
                    Path(stopped['source']).unlink()
                os.kill(old.pid, signal.SIGCONT)
                check(worker_result(old), 'Resumed obsolete worker did not return')
                check(state(*live) == ('ready', 'done', 2), 'Obsolete worker changed the winner state')
                check(publication_snapshot(live[0]) == winner, 'Obsolete worker altered winner S3 keys or bytes')
                if not break_old_input:
                    uploads = [json.loads(line)['key'] for line in Path(str(marker) + '.uploads').read_text().splitlines()]
                    check(len(uploads) == 2 and not set(uploads).intersection(winner[0]), 'Obsolete worker uploaded into the replacement publication')
                assert_only_published_outputs(live[0], winner[0])
            print('PASS: live SIGSTOP/SIGCONT stale success and failure preserve replacement state and exact S3 publication', flush=True)

            # An obsolete failure must also leave the shared source in place
            # while the replacement is still processing.
            pending = seed_job(fixture, 'Current claimant source protection')
            old_marker, current_marker = Path(directory) / 'old-source.json', Path(directory) / 'current-source.json'
            old = worker_start(old_marker)
            old_input = wait_checkpoint(old, old_marker)
            with SessionLocal() as db:
                db.get(Job, pending[1]).claimed_at = datetime.utcnow() - timedelta(minutes=36)
                source_key = db.get(Video, pending[0]).source_key
                db.commit()
            current = worker_start(current_marker)
            wait_checkpoint(current, current_marker)
            Path(old_input['source']).unlink()
            os.kill(old.pid, signal.SIGCONT)
            check(worker_result(old), 'Old failing process did not return')
            check(state(*pending) == ('processing', 'processing', 2), 'Old failure changed current processing claim')
            check(storage.size(source_key) > 100, 'Old failure deleted the shared S3 source')
            os.kill(current.pid, signal.SIGCONT)
            check(worker_result(current), 'Current process did not resume')
            check(state(*pending) == ('ready', 'done', 2), 'Current claimant could not finish')
            verify_output(pending[0])
            print('PASS: stale failure preserves a live replacement claim and its shared S3 source', flush=True)

            # Freeze the real API deletion function after its owner lookup,
            # publish in another process, then ensure DELETE refreshes keys
            # under the Job -> Video lock order before cleaning S3/cascading.
            deleting = seed_job(fixture, 'Delete racing publication')
            delete_marker = Path(directory) / 'delete-owner.json'
            delete_code = r'''
import json, os, signal
from pathlib import Path
from app import main
from app.db import SessionLocal
from app.models import User
lookup = main.owned_video
marker = os.environ['RUNTIME_DELETE_CHECKPOINT']
def lookup_and_pause(db, video_id, user):
    video = lookup(db, video_id, user)
    Path(marker).write_text(json.dumps({'media_key_before': video.media_key}))
    os.kill(os.getpid(), signal.SIGSTOP)
    return video
main.owned_video = lookup_and_pause
with SessionLocal() as db:
    main.delete_video(int(os.environ['RUNTIME_VIDEO_ID']), db.get(User, int(os.environ['RUNTIME_OWNER_ID'])), db)
print('DELETE_RESULT=True', flush=True)
'''
            delete_env = dict(os.environ, RUNTIME_DELETE_CHECKPOINT=str(delete_marker), RUNTIME_VIDEO_ID=str(deleting[0]), RUNTIME_OWNER_ID=str(owner_id))
            deleter = subprocess.Popen([sys.executable, '-c', delete_code], cwd=root, env=delete_env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, start_new_session=True)
            running.append(deleter)
            before_delete = wait_checkpoint(deleter, delete_marker)
            check(before_delete['media_key_before'] is None, 'Delete fixture was already published before initial lookup')
            check(worker_result(worker_start()), 'Concurrent publisher did not finish')
            check(state(*deleting) == ('ready', 'done', 1), 'Concurrent publication did not complete')
            os.kill(deleter.pid, signal.SIGCONT)
            stdout, stderr = deleter.communicate(timeout=30)
            check(deleter.returncode == 0 and 'DELETE_RESULT=True' in stdout, f'Deletion failed: {stderr[-2000:]}')
            with SessionLocal() as db:
                check(db.get(Video, deleting[0]) is None and db.get(Job, deleting[1]) is None, 'Delete did not cascade after concurrent publication')
            assert_only_published_outputs(deleting[0], ())
            print('PASS: owner DELETE refreshes and removes concurrently published S3 outputs with PostgreSQL row locks', flush=True)

            from media_fixtures import make_cover_first_mp4
            covered = make_cover_first_mp4(Path(directory) / 'cover-first.mp4')
            probe = subprocess.run(['ffprobe', '-v', 'error', '-show_streams', '-of', 'json', str(covered)], check=True, capture_output=True, text=True, timeout=20)
            input_streams = json.loads(probe.stdout)['streams']
            check(input_streams[0]['index'] == 0 and input_streams[0].get('disposition', {}).get('attached_pic') == 1, 'Fixture does not expose an embedded cover before its playable track')
            covered_job = seed_job(covered, 'Cover-first MP4')
            check(worker_result(worker_start()), 'Cover-first video was not claimed')
            check(state(*covered_job) == ('ready', 'done', 1), 'Valid cover-first MP4 was rejected')
            verify_output(covered_job[0], minimum_frames=70, minimum_duration=2.9)
            print('PASS: real cover-first MP4 publishes the actual 72-frame H.264 track, AAC and JPEG thumbnail', flush=True)

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
