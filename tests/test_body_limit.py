"""Request caps run before multipart spooling; streaming has no Content-Length."""
import unittest
from fastapi import FastAPI, File, UploadFile
from fastapi.testclient import TestClient
from app.body_limit import BodyLimitMiddleware


class BodyLimitTests(unittest.TestCase):
    def test_oversized_declared_body_rejected_before_route(self):
        app = FastAPI()
        app.add_middleware(BodyLimitMiddleware, max_bytes=64)
        @app.post('/api/videos')
        async def upload(file: UploadFile = File(...)):
            raise AssertionError('Oversized body reached upload route')
        with TestClient(app) as client:
            response = client.post('/api/videos', content=b'x', headers={'Content-Length': '65'})
        assert response.status_code == 413

    def test_chunked_multipart_over_limit_returns_413(self):
        app = FastAPI()
        app.add_middleware(BodyLimitMiddleware, max_bytes=128)
        @app.post('/api/videos')
        async def upload(file: UploadFile = File(...)):
            raise AssertionError('Oversized body reached upload route')
        body = b'--test\r\nContent-Disposition: form-data; name="file"; filename="video.mp4"\r\nContent-Type: video/mp4\r\n\r\n' + b'x'*256 + b'\r\n--test--\r\n'
        with TestClient(app) as client:
            response = client.post('/api/videos', content=iter([body[:100], body[100:]]), headers={'Content-Type': 'multipart/form-data; boundary=test'})
        assert response.status_code == 413, response.text

    def test_json_route_has_small_separate_cap(self):
        app = FastAPI()
        app.add_middleware(BodyLimitMiddleware, max_bytes=512*1024*1024)
        with TestClient(app) as client:
            response = client.post('/api/auth/login', content=b'x'*(128*1024+1))
        assert response.status_code == 413
