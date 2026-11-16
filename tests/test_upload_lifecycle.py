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
                return subprocess.CompletedProcess(command, 0, stdout='{"streams":[{"codec_type":"video","width":320,"height":240}],"format":{"duration":"3"}}')
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
                return subprocess.CompletedProcess(command, 0, stdout='{"streams":[{"codec_type":"video","width":320,"height":240}],"format":{"duration":"3"}}')
            if str(command[-1]).endswith('video.mp4'):
                assert self.client.delete(f'/api/videos/{video_id}').status_code == 204
            Path(command[-1]).write_bytes(b'encoded-fixture')
            return subprocess.CompletedProcess(command, 0, stdout='')
        with patch('app.worker.run', side_effect=fake_encoder):
            assert process_one() is True
        assert self.client.get(f'/api/videos/{video_id}').status_code == 404
        storage = get_storage()
        assert not storage.path(f'media/{video_id}/video.mp4').exists()
        assert not storage.path(f'media/{video_id}/thumbnail.jpg').exists()
        with SessionLocal() as db:
            assert db.scalar(select(Job).where(Job.video_id == video_id)) is None
