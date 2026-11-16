"""Standard-library test harness with isolated SQLite and filesystem object storage."""
import os
import tempfile
import unittest
from pathlib import Path

_ROOT = Path(tempfile.mkdtemp(prefix="youtube-tests-"))
os.environ["DATABASE_URL"] = f"sqlite:///{_ROOT / 'test.db'}"
os.environ["STORAGE_BACKEND"] = "local"
os.environ["STORAGE_PATH"] = str(_ROOT / "objects")
os.environ["PUBLIC_ORIGIN"] = "http://testserver"
os.environ["COOKIE_SECURE"] = "false"
os.environ["TESTING"] = "true"

from fastapi.testclient import TestClient
from app.db import Base, engine, SessionLocal
from app.main import app
from app.models import User, Video
from app.storage import get_storage


class APITestCase(unittest.TestCase):
    def setUp(self):
        from app.main import _auth_attempts
        _auth_attempts.clear()
        Base.metadata.drop_all(engine)
        Base.metadata.create_all(engine)
        self._clients = []
        self.client = self.new_client()
        self.tmp = tempfile.TemporaryDirectory(prefix="youtube-fixture-")
        self.addCleanup(self.tmp.cleanup)

    def tearDown(self):
        for client in self._clients:
            client.close()

    def new_client(self):
        client = TestClient(app)
        self._clients.append(client)
        return client

    def register(self, client, name="Owner", email=None):
        response = client.post("/api/auth/register", json={
            "email": email or f"{name.lower()}@example.com",
            "password": "StrongPassword123!", "display_name": name,
        })
        assert response.status_code in (200, 201), response.text
        return response.json()

    def ready_video(self, owner_id, visibility="public", hidden=False, status="ready", title="A real title"):
        data = b"0123456789abcdefghijklmnopqrstuvwxyz"
        path = Path(self.tmp.name) / "fixture.mp4"
        path.write_bytes(data)
        key = f"test/{owner_id}-{visibility}-{hidden}-{status}.mp4"
        get_storage().put_file(key, path, "video/mp4")
        with SessionLocal() as db:
            video = Video(owner_id=owner_id, title=title, description="Fixture", visibility=visibility,
                          hidden=hidden, status=status, source_key=key, media_key=key, thumbnail_key=key,
                          duration=3)
            db.add(video)
            db.commit()
            return video.id

    def set_role(self, user_id, role):
        with SessionLocal() as db:
            db.get(User, user_id).role = role
            db.commit()
