"""End-to-end acceptance check against running API, PostgreSQL, S3 and worker.

Run: docker compose exec app python scripts/smoke.py
Creates disposable accounts/content; cleans its own video even on failure.
DEMO_PASSWORD authenticates the explicitly seeded moderator.
"""
import os
from pathlib import Path
import tempfile
import time
import urllib.error
import urllib.request
import uuid

from seed import client, request, upload, make_video
import seed

seed.BASE = os.getenv('SMOKE_BASE_URL', 'http://127.0.0.1:8000')


def check(condition, message):
    if not condition:
        raise AssertionError(message)


def rejected(opener, method, path, expected, data=None):
    try:
        request(opener, method, path, data)
    except urllib.error.HTTPError as error:
        check(error.code == expected, f'{path}: expected {expected}, got {error.code}')
    else:
        raise AssertionError(f'{path}: unexpectedly accessible')


def denied(opener, path):
    try:
        request(opener, 'GET', path)
    except urllib.error.HTTPError as error:
        check(error.code == 404, f'{path}: expected 404, got {error.code}')
    else:
        raise AssertionError(f'{path}: unexpectedly accessible')


def media(opener, path, expected=200, byte_range=None):
    headers = {}
    if byte_range:
        headers['Range'] = byte_range
    with opener.open(urllib.request.Request(seed.BASE + path, headers=headers), timeout=20) as response:
        content = response.read()
        check(response.status == expected, f'Media status {response.status} != {expected}')
        return content, response.headers


def main():
    token = uuid.uuid4().hex
    password = 'Acceptance-' + token
    owner, viewer, anon, moderator, admin = client(), client(), client(), client(), client()
    video_id = malformed_id = None
    owner_user = viewer_user = None
    try:
        for opener, kind in ((owner, 'owner'), (viewer, 'viewer')):
            account = {'email': f'{kind}-{token}@smoke.local', 'password': password, 'display_name': 'Smoke ' + kind}
            request(opener, 'POST', '/api/auth/register', account)
            request(opener, 'POST', '/api/auth/login', {'email': account['email'], 'password': password})
        owner_user = request(owner, 'GET', '/api/auth/me')
        viewer_user = request(viewer, 'GET', '/api/auth/me')
        check(owner_user['role'] == viewer_user['role'] == 'user', 'Registration assigned an elevated role')
        for opener, email in ((moderator, 'moderator@demo.local'), (admin, 'admin@demo.local')):
            request(opener, 'POST', '/api/auth/login', {'email': email, 'password': os.environ['DEMO_PASSWORD']})
        for opener in (owner, moderator):
            rejected(opener, 'GET', '/api/admin/users', 403)
            rejected(opener, 'PATCH', f"/api/admin/users/{viewer_user['id']}/role", 403, {'role': 'admin'})
        request(admin, 'PATCH', f"/api/admin/users/{viewer_user['id']}/role", {'role': 'moderator'})
        check(request(viewer, 'GET', '/api/auth/me')['role'] == 'moderator', 'Role upgrade not visible to existing session')
        request(viewer, 'GET', '/api/moderation/reports')
        request(admin, 'PATCH', f"/api/admin/users/{viewer_user['id']}/role", {'role': 'user'})
        rejected(viewer, 'GET', '/api/moderation/reports', 403)
        admin_user = request(admin, 'GET', '/api/auth/me')
        rejected(admin, 'PATCH', f"/api/admin/users/{admin_user['id']}/role", 422, {'role': 'user'})
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'test.mp4'
            make_video(path, '0x225588', 330, 1)
            video = upload(owner, path, 'Acceptance test ' + token, 'Real generated MP4', 'public')
        video_id = video['id']
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            video = request(owner, 'GET', f'/api/videos/{video_id}')
            if video['status'] == 'ready':
                break
            check(video['status'] != 'failed', 'FFmpeg worker rejected generated valid video')
            time.sleep(1)
        else:
            raise AssertionError('Worker did not complete within 120 seconds')
        full, headers = media(anon, f'/api/videos/{video_id}/media')
        check(len(full) > 100, 'MP4 payload is empty')
        first, headers = media(anon, f'/api/videos/{video_id}/media', 206, 'bytes=0-99')
        check(first == full[:100], 'Range bytes do not match full object')
        check(headers.get('Content-Range') == f'bytes 0-99/{len(full)}', 'Invalid Content-Range')
        suffix, _ = media(anon, f'/api/videos/{video_id}/media', 206, 'bytes=-50')
        check(suffix == full[-50:], 'Suffix byte range mismatch')
        thumb, _ = media(anon, f'/api/videos/{video_id}/thumbnail')
        check(thumb[:2] == b'\xff\xd8', 'Thumbnail is not a JPEG')
        request(viewer, 'POST', f'/api/videos/{video_id}/view')
        like = request(viewer, 'PUT', f'/api/videos/{video_id}/like')
        check(like['liked'] and like['likes'] == 1, 'Like did not persist')
        check(request(viewer, 'PUT', f'/api/videos/{video_id}/like')['likes'] == 1, 'Like is not idempotent')
        request(viewer, 'POST', f'/api/videos/{video_id}/comments', {'body': 'Acceptance smoke comment'})
        check(any(c['body'] == 'Acceptance smoke comment' for c in request(anon, 'GET', f'/api/videos/{video_id}/comments')), 'Comment not found')
        request(viewer, 'PUT', f"/api/users/{owner_user['id']}/subscribe")
        check(request(viewer, 'GET', f"/api/users/{owner_user['id']}")['subscribed'], 'Subscription failed')
        playlist = request(owner, 'POST', '/api/playlists', {'title': 'Acceptance ' + token})
        request(owner, 'PUT', f"/api/playlists/{playlist['id']}/videos/{video_id}")
        request(owner, 'PATCH', f'/api/videos/{video_id}', {'visibility': 'private'})
        for opener in (anon, viewer):
            for suffix in ('', '/media', '/thumbnail', '/comments'):
                denied(opener, f'/api/videos/{video_id}{suffix}')
        # Staff roles do not override an owner's private ACL.
        for opener in (moderator, admin):
            for suffix in ('', '/media', '/thumbnail', '/comments'):
                denied(opener, f'/api/videos/{video_id}{suffix}')
        request(owner, 'PUT', f'/api/videos/{video_id}/access', {'email': viewer_user['email']})
        granted, _ = media(viewer, f'/api/videos/{video_id}/media', 206, 'bytes=0-99')
        check(granted == first, 'Granted viewer cannot read exact private media')
        request(owner, 'DELETE', f"/api/videos/{video_id}/access/{viewer_user['id']}")
        for suffix in ('', '/media', '/thumbnail', '/comments'):
            denied(viewer, f'/api/videos/{video_id}{suffix}')
        request(owner, 'PATCH', f'/api/videos/{video_id}', {'visibility': 'public'})
        request(viewer, 'POST', f'/api/videos/{video_id}/reports', {'reason': 'Acceptance moderation test'})
        rejected(viewer, 'POST', f'/api/moderation/videos/{video_id}/hide', 403, {'hidden': True})
        reports = request(moderator, 'GET', '/api/moderation/reports')
        check(any(r.get('video_id') == video_id for r in reports), 'Report absent from moderation queue')
        request(moderator, 'POST', f'/api/moderation/videos/{video_id}/hide', {'hidden': True})
        denied(anon, f'/api/videos/{video_id}/media')
        denied(owner, f'/api/videos/{video_id}/media')
        request(moderator, 'POST', f'/api/moderation/videos/{video_id}/hide', {'hidden': False})
        media(anon, f'/api/videos/{video_id}/media', 206, 'bytes=0-99')
        with tempfile.TemporaryDirectory() as directory:
            malformed = Path(directory) / 'broken.mp4'
            malformed.write_bytes(b'not an MP4 container')
            malformed_id = upload(owner, malformed, 'Malformed acceptance ' + token, 'Must never play')['id']
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            result = request(owner, 'GET', f'/api/videos/{malformed_id}')
            if result['status'] == 'failed':
                break
            check(result['status'] != 'ready', 'Malformed input was published')
            time.sleep(1)
        else:
            raise AssertionError('Malformed video was not rejected within 60 seconds')
        denied(anon, f'/api/videos/{malformed_id}')
        denied(owner, f'/api/videos/{malformed_id}/media')
        print('PASS: roles/update/demotion → malformed rejection → real PostgreSQL/S3/FFmpeg upload → playback/seek → like/comment → private grant/revoke → report/hide/unhide')
    finally:
        for cleanup_id in (video_id, malformed_id):
            if cleanup_id:
                request(owner, 'DELETE', f'/api/videos/{cleanup_id}')


if __name__ == '__main__':
    main()
