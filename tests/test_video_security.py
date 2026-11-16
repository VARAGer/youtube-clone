from support import APITestCase


class VideoSecurityTests(APITestCase):
    def test_private_metadata_bytes_thumbnail_comments_and_list_protected(self):
        client = self.client
        new_client = self.new_client
        register = self.register
        ready_video = self.ready_video
        owner = register(client)
        video_id = ready_video(owner["id"], "private")
        outsider = new_client()
        assert outsider.get("/api/videos").json() == []
        for suffix in ("", "/media", "/thumbnail", "/comments"):
            assert outsider.get(f"/api/videos/{video_id}{suffix}").status_code == 404
        viewer = register(outsider, "Viewer")
        assert outsider.get(f"/api/videos/{video_id}/media", headers={"Range": "bytes=0-9"}).status_code == 404
        assert client.put(f"/api/videos/{video_id}/access", json={"email": viewer["email"]}).status_code in (200, 201, 204)
        assert outsider.get(f"/api/videos/{video_id}").status_code == 200
        assert outsider.get(f"/api/videos/{video_id}/media").status_code == 200
        assert client.delete(f"/api/videos/{video_id}/access/{viewer['id']}").status_code in (200, 204)
        assert outsider.get(f"/api/videos/{video_id}/media").status_code == 404


    def test_private_acl_does_not_implicitly_allow_staff(self):
        client = self.client
        new_client = self.new_client
        register = self.register
        ready_video = self.ready_video
        set_role = self.set_role
        owner = register(client)
        vid = ready_video(owner["id"], "private")
        admin = new_client()
        user = register(admin, "Admin")
        set_role(user["id"], "admin")
        assert admin.get(f"/api/videos/{vid}/media").status_code == 404


    def test_unlisted_is_watchable_but_not_searchable(self):
        client = self.client
        new_client = self.new_client
        register = self.register
        ready_video = self.ready_video
        user = register(client)
        vid = ready_video(user["id"], "unlisted", title="Secret searchable title")
        anonymous = new_client()
        assert anonymous.get("/api/videos?q=Secret").json() == []
        assert anonymous.get(f"/api/videos/{vid}").status_code == 200


    def test_media_range_semantics(self):
        client = self.client
        register = self.register
        ready_video = self.ready_video
        user = register(client)
        vid = ready_video(user["id"])
        cases = [("bytes=0-9",206,b"0123456789"),("bytes=10-",206,b"abcdefghijklmnopqrstuvwxyz"),("bytes=-3",206,b"xyz"),("bytes=999-1000",416,None)]
        for header, expected, body in cases:
            response = client.get(f"/api/videos/{vid}/media", headers={"Range": header})
            assert response.status_code == expected, response.text
            if body is not None:
                assert response.content == body
                assert int(response.headers["content-length"]) == len(body)
                assert response.headers["accept-ranges"] == "bytes"
                assert response.headers["content-range"].endswith("/36")
            else:
                assert response.headers["content-range"] == "bytes */36"

    def test_nonowners_cannot_edit_delete_grant_or_read_acl(self):
        client = self.client
        new_client = self.new_client
        register = self.register
        ready_video = self.ready_video
        owner = register(client)
        vid = ready_video(owner["id"])
        other = new_client()
        register(other, "Other")
        assert other.patch(f"/api/videos/{vid}", json={"title": "Stolen"}).status_code in (403, 404)
        assert other.delete(f"/api/videos/{vid}").status_code in (403, 404)
        assert other.get(f"/api/videos/{vid}/access").status_code in (403, 404)
        assert other.put(f"/api/videos/{vid}/access", json={"email": "other@example.com"}).status_code in (403, 404)
        assert client.get(f"/api/videos/{vid}").json()["title"] == "A real title"


    def test_hidden_video_never_streams_even_for_owner(self):
        client = self.client
        new_client = self.new_client
        register = self.register
        ready_video = self.ready_video
        set_role = self.set_role
        owner = register(client)
        vid = ready_video(owner["id"])
        staff = new_client()
        moderator = register(staff, "Moderator")
        set_role(moderator["id"], "moderator")
        assert client.post(f"/api/videos/{vid}/reports", json={"reason": "Copyright concern"}).status_code in (200, 201)
        assert staff.get("/api/moderation/reports").status_code == 200
        assert staff.post(f"/api/moderation/videos/{vid}/hide", json={"hidden": True}).status_code == 200
        assert client.get(f"/api/videos/{vid}/media").status_code == 404
        assert staff.get(f"/api/videos/{vid}/media").status_code == 404
        assert new_client().get(f"/api/videos/{vid}").status_code == 404
        assert staff.post(f"/api/moderation/videos/{vid}/hide", json={"hidden": False}).status_code == 200
        assert client.get(f"/api/videos/{vid}/media").status_code == 200


    def test_upload_rejects_empty_and_requires_auth(self):
        client = self.client
        register = self.register
        payload = {"title": "Bad upload", "visibility": "public"}
        assert client.post("/api/videos", data=payload, files={"file": ("test.mp4", b"bad", "video/mp4")}).status_code == 401
        register(client)
        assert client.post("/api/videos", data=payload, files={"file": ("empty.mp4", b"", "video/mp4")}).status_code in (400, 415, 422)

    def test_media_removed_between_head_and_read_returns_404(self):
        from unittest.mock import patch
        from app.storage import LocalStorage
        owner = self.register(self.client)
        video_id = self.ready_video(owner['id'])
        with patch.object(LocalStorage, 'read', side_effect=FileNotFoundError('deleted')):
            response = self.client.get(f'/api/videos/{video_id}/media')
        assert response.status_code == 404
