"""Queue/lifecycle invariants. Real ffmpeg output is covered by deployment smoke."""
import subprocess
from unittest.mock import patch
from sqlalchemy import select
from support import APITestCase
from app.db import SessionLocal
from app.models import Job, Video


class UploadLifecycleTests(APITestCase):
    def test_upload_is_queued_and_source_is_never_streamed(self):
        self.register(self.client)
        response = self.client.post("/api/videos", data={"title": "Untrusted upload", "visibility": "public"},
                                    files={"file": ("../../escape.mp4", b"untrusted payload", "video/mp4")})
        assert response.status_code == 202, response.text
        video_id = response.json()["id"]
        with SessionLocal() as db:
            video = db.get(Video, video_id)
            assert video.status == "uploaded"
            assert video.source_key.startswith("sources/")
            assert "escape" not in video.source_key and ".." not in video.source_key
            assert db.scalar(select(Job).where(Job.video_id == video_id)).status == "queued"
        assert self.client.get(f"/api/videos/{video_id}/media").status_code == 404
        assert self.new_client().get(f"/api/videos/{video_id}").status_code == 404

    def test_processor_failure_sets_failed_state_without_publishing(self):
        self.register(self.client)
        response = self.client.post("/api/videos", data={"title": "Invalid video"},
                                    files={"file": ("video.mp4", b"not a video", "video/mp4")})
        video_id = response.json()["id"]
        from app.worker import process_one
        with patch("app.worker.run", side_effect=subprocess.CalledProcessError(1, ["ffprobe"])):
            assert process_one() is True
        with SessionLocal() as db:
            video = db.get(Video, video_id)
            assert video.status == "failed"
            assert video.media_key is None and video.thumbnail_key is None
            assert db.scalar(select(Job).where(Job.video_id == video_id)).status == "failed"
        assert self.client.get(f"/api/videos/{video_id}/media").status_code == 404
        assert self.client.get(f"/api/videos/{video_id}").json()["status"] == "failed"
        assert process_one() is False

    def test_streaming_upload_enforces_size_before_database_write(self):
        self.register(self.client)
        from app import config
        with patch.object(config, "MAX_UPLOAD_BYTES", 8):
            response = self.client.post("/api/videos", data={"title": "Too large"},
                                        files={"file": ("video.mp4", b"123456789", "video/mp4")})
        assert response.status_code == 413
        with SessionLocal() as db:
            assert db.scalar(select(Video)) is None
            assert db.scalar(select(Job)) is None

    def test_stale_processing_job_is_reclaimed_and_published_only_after_success(self):
        from datetime import datetime, timedelta
        from pathlib import Path
        from app.worker import process_one
        self.register(self.client)
        response = self.client.post('/api/videos', data={'title': 'Recover me'}, files={'file': ('v.mp4', b'source', 'video/mp4')})
        video_id = response.json()['id']
        with SessionLocal() as db:
            job = db.scalar(select(Job).where(Job.video_id == video_id))
            job.status = 'processing'
            job.claimed_at = datetime.utcnow() - timedelta(hours=1)
            job.attempts = 1
            db.get(Video, video_id).status = 'processing'
            db.commit()
        def fake_encoder(command, timeout):
            assert self.client.get(f'/api/videos/{video_id}/media').status_code == 404
            if command[0] == 'ffprobe':
                return subprocess.CompletedProcess(command, 0, stdout='{"streams":[{"index":0,"codec_type":"video","width":320,"height":240}],"format":{"duration":"3"}}')
            Path(command[-1]).write_bytes(b'encoded-fixture')
            return subprocess.CompletedProcess(command, 0, stdout='')
        with patch('app.worker.run', side_effect=fake_encoder):
            assert process_one() is True
        with SessionLocal() as db:
            video = db.get(Video, video_id)
            job = db.scalar(select(Job).where(Job.video_id == video_id))
            assert video.status == 'ready' and job.status == 'done'
            assert job.attempts == 2 and video.duration == 3
        assert self.client.get(f'/api/videos/{video_id}/media').content == b'encoded-fixture'
        assert process_one() is False

    def test_deleted_video_during_processing_does_not_leave_published_objects(self):
        from pathlib import Path
        from app.worker import process_one
        from app.storage import get_storage
        self.register(self.client)
        response = self.client.post('/api/videos', data={'title': 'Delete while processing'}, files={'file': ('v.mp4', b'source', 'video/mp4')})
        video_id = response.json()['id']
        def fake_encoder(command, timeout):
            if command[0] == 'ffprobe':
                return subprocess.CompletedProcess(command, 0, stdout='{"streams":[{"index":0,"codec_type":"video","width":320,"height":240}],"format":{"duration":"3"}}')
            if str(command[-1]).endswith('video.mp4'):
                assert self.client.delete(f'/api/videos/{video_id}').status_code == 204
            Path(command[-1]).write_bytes(b'encoded-fixture')
            return subprocess.CompletedProcess(command, 0, stdout='')
        with patch('app.worker.run', side_effect=fake_encoder):
            assert process_one() is True
        assert self.client.get(f'/api/videos/{video_id}').status_code == 404
        storage = get_storage()
        assert not [p for p in storage.path(f'media/{video_id}').rglob('*') if p.is_file()]
        with SessionLocal() as db:
            assert db.scalar(select(Job).where(Job.video_id == video_id)) is None

    def test_source_cleanup_failure_does_not_unpublish_successful_encode(self):
        from pathlib import Path
        from app.worker import process_one
        from app.storage import LocalStorage
        self.register(self.client)
        response = self.client.post('/api/videos', data={'title': 'Cleanup outage'}, files={'file': ('v.mp4', b'source', 'video/mp4')})
        video_id = response.json()['id']
        original_delete = LocalStorage.delete
        def cleanup(storage, key):
            if key.startswith('sources/'):
                raise OSError('temporary cleanup outage')
            return original_delete(storage, key)
        def fake_encoder(command, timeout):
            if command[0] == 'ffprobe':
                return subprocess.CompletedProcess(command, 0, stdout='{"streams":[{"index":0,"codec_type":"video","width":320,"height":240}],"format":{"duration":"3"}}')
            Path(command[-1]).write_bytes(b'encoded-fixture')
            return subprocess.CompletedProcess(command, 0, stdout='')
        with patch('app.worker.run', side_effect=fake_encoder), patch.object(LocalStorage, 'delete', cleanup):
            assert process_one() is True
        with SessionLocal() as db:
            assert db.get(Video, video_id).status == 'ready'
            assert db.scalar(select(Job).where(Job.video_id == video_id)).status == 'done'
        assert self.client.get(f'/api/videos/{video_id}/media').content == b'encoded-fixture'

    def _stale_attempt_after_replacement(self, fail_old_attempt):
        """Keep W1 live while a genuinely reclaimed W2 completes its job."""
        from datetime import datetime, timedelta
        from pathlib import Path
        from app.worker import process_one
        from app.storage import get_storage
        self.register(self.client)
        response = self.client.post('/api/videos', data={'title': 'Fenced replacement'}, files={'file': ('v.mp4', b'source', 'video/mp4')})
        video_id = response.json()['id']
        replacing = False
        winner = {}
        def encoder(command, timeout):
            nonlocal replacing
            if command[0] == 'ffprobe':
                if not replacing:
                    replacing = True
                    with SessionLocal() as db:
                        job = db.scalar(select(Job).where(Job.video_id == video_id))
                        job.claimed_at = datetime.utcnow() - timedelta(hours=1)
                        db.commit()
                    assert process_one() is True
                    with SessionLocal() as db:
                        video = db.get(Video, video_id)
                        winner.update(media_key=video.media_key, thumbnail_key=video.thumbnail_key)
                    if fail_old_attempt:
                        raise subprocess.CalledProcessError(1, ['ffprobe'])
                    replacing = False
                return subprocess.CompletedProcess(command, 0, stdout='{"streams":[{"index":0,"codec_type":"video","width":320,"height":240}],"format":{"duration":"3"}}')
            Path(command[-1]).write_bytes(b'winner-output' if replacing else b'obsolete-output')
            return subprocess.CompletedProcess(command, 0, stdout='')
        with patch('app.worker.run', side_effect=encoder):
            assert process_one() is True
        with SessionLocal() as db:
            video = db.get(Video, video_id)
            job = db.scalar(select(Job).where(Job.video_id == video_id))
            assert (video.status, job.status, job.attempts) == ('ready', 'done', 2)
            assert (video.media_key, video.thumbnail_key) == (winner['media_key'], winner['thumbnail_key'])
        storage = get_storage()
        assert storage.path(winner['media_key']).read_bytes() == b'winner-output'
        assert storage.path(winner['thumbnail_key']).read_bytes() == b'winner-output'
        assert self.client.get(f'/api/videos/{video_id}/media').content == b'winner-output'
        outputs = [p for p in storage.path(f'media/{video_id}').rglob('*') if p.is_file()]
        assert len(outputs) == 2, 'Obsolete attempt outputs were not cleaned'

    def test_stale_success_cannot_overwrite_replacement_publication(self):
        self._stale_attempt_after_replacement(fail_old_attempt=False)

    def test_stale_failure_cannot_fail_or_delete_replacement_publication(self):
        self._stale_attempt_after_replacement(fail_old_attempt=True)

    def test_stale_failure_preserves_source_needed_by_current_attempt(self):
        from app.worker import process_one
        from app.storage import get_storage
        self.register(self.client)
        video_id = self.client.post('/api/videos', data={'title': 'Current attempt needs source'}, files={'file': ('v.mp4', b'source', 'video/mp4')}).json()['id']
        def fail_after_reclaim(command, timeout):
            with SessionLocal() as db:
                job = db.scalar(select(Job).where(Job.video_id == video_id))
                job.attempts += 1
                db.commit()
            raise subprocess.CalledProcessError(1, ['ffprobe'])
        with patch('app.worker.run', side_effect=fail_after_reclaim):
            assert process_one() is True
        with SessionLocal() as db:
            video = db.get(Video, video_id)
            job = db.scalar(select(Job).where(Job.video_id == video_id))
            assert (video.status, job.status, job.attempts) == ('processing', 'processing', 2)
            assert get_storage().path(video.source_key).read_bytes() == b'source'

    def test_delete_refreshes_outputs_published_after_initial_owner_lookup(self):
        """The owner lookup may predate a worker's publication transaction."""
        from pathlib import Path
        from app import main
        from app.worker import process_one
        from app.storage import get_storage
        self.register(self.client)
        video_id = self.client.post('/api/videos', data={'title': 'Delete concurrent publication'}, files={'file': ('v.mp4', b'source', 'video/mp4')}).json()['id']
        initial_lookup = main.owned_video
        def publish_after_lookup(db, selected_id, user):
            video = initial_lookup(db, selected_id, user)
            assert video.media_key is None
            assert process_one() is True
            return video
        def encoder(command, timeout):
            if command[0] == 'ffprobe':
                return subprocess.CompletedProcess(command, 0, stdout='{"streams":[{"index":0,"codec_type":"video","width":320,"height":240}],"format":{"duration":"3"}}')
            Path(command[-1]).write_bytes(b'encoded-fixture')
            return subprocess.CompletedProcess(command, 0, stdout='')
        with patch('app.main.owned_video', side_effect=publish_after_lookup), patch('app.worker.run', side_effect=encoder):
            assert self.client.delete(f'/api/videos/{video_id}').status_code == 204
        with SessionLocal() as db:
            assert db.get(Video, video_id) is None
            assert db.scalar(select(Job).where(Job.video_id == video_id)) is None
        assert not [p for p in get_storage().path(f'media/{video_id}').rglob('*') if p.is_file()]


    def test_encoder_maps_validated_video_instead_of_embedded_cover(self):
        import json
        from pathlib import Path
        from app.worker import process_one
        self.register(self.client)
        video_id = self.client.post('/api/videos', data={'title': 'Embedded cover first'}, files={'file': ('covered.mp4', b'source', 'video/mp4')}).json()['id']
        def encoder(command, timeout):
            if command[0] == 'ffprobe':
                return subprocess.CompletedProcess(command, 0, stdout=json.dumps({
                    'streams': [
                        {'index': 0, 'codec_type': 'video', 'width': 100, 'height': 100, 'disposition': {'attached_pic': 1}},
                        {'index': 1, 'codec_type': 'video', 'width': 320, 'height': 180, 'disposition': {'attached_pic': 0}},
                        {'index': 2, 'codec_type': 'audio'},
                    ], 'format': {'duration': '3'}}))
            if str(command[-1]).endswith('video.mp4'):
                assert command[command.index('-map') + 1] == '0:1', 'Encoder selected cover art instead of validated video'
            Path(command[-1]).write_bytes(b'encoded-fixture')
            return subprocess.CompletedProcess(command, 0, stdout='')
        with patch('app.worker.run', side_effect=encoder):
            assert process_one() is True
        with SessionLocal() as db:
            assert db.get(Video, video_id).status == 'ready'
        assert self.client.get(f'/api/videos/{video_id}/media').content == b'encoded-fixture'
