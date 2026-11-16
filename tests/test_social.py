from support import APITestCase


class SocialTests(APITestCase):
    def test_likes_are_idempotent_and_comments_have_ownership(self):
        client = self.client
        new_client = self.new_client
        register = self.register
        ready_video = self.ready_video
        owner = register(client)
        vid = ready_video(owner["id"])
        viewer = new_client()
        register(viewer, "Viewer")
        for _ in range(2):
            response = viewer.put(f"/api/videos/{vid}/like")
            assert response.status_code == 200
            assert response.json()["liked"] is True
            assert response.json()["likes"] == 1
        assert client.get(f"/api/videos/{vid}").json()["likes"] == 1
        response = viewer.post(f"/api/videos/{vid}/comments", json={"body": "A useful comment"})
        assert response.status_code in (200, 201)
        comment = response.json()
        assert comment["body"] == "A useful comment"
        assert client.get(f"/api/videos/{vid}/comments").json()[0]["id"] == comment["id"]
        stranger = new_client()
        register(stranger, "Stranger")
        assert stranger.delete(f"/api/comments/{comment['id']}").status_code in (403, 404)
        assert viewer.delete(f"/api/comments/{comment['id']}").status_code in (200, 204)
        assert client.get(f"/api/videos/{vid}/comments").json() == []
        for _ in range(2):
            assert viewer.delete(f"/api/videos/{vid}/like").status_code in (200, 204)
        assert client.get(f"/api/videos/{vid}").json()["likes"] == 0


    def test_subscription_idempotency(self):
        client = self.client
        new_client = self.new_client
        register = self.register
        owner = register(client)
        viewer = new_client()
        register(viewer, "Viewer")
        for _ in range(2):
            assert viewer.put(f"/api/users/{owner['id']}/subscribe").status_code in (200, 201, 204)
        channel = viewer.get(f"/api/users/{owner['id']}").json()
        assert channel["subscribers"] == 1
        assert channel["subscribed"] is True
        assert viewer.delete(f"/api/users/{owner['id']}/subscribe").status_code in (200, 204)
        assert viewer.get(f"/api/users/{owner['id']}").json()["subscribers"] == 0


    def test_playlists_do_not_disclose_private_or_hidden_items(self):
        client = self.client
        new_client = self.new_client
        register = self.register
        ready_video = self.ready_video
        owner = register(client)
        public = ready_video(owner["id"])
        private = ready_video(owner["id"], "private")
        hidden = ready_video(owner["id"], title="Soon hidden")
        response = client.post("/api/playlists", json={"title": "My playlist"})
        assert response.status_code in (200, 201)
        playlist_id = response.json()["id"]
        for vid in (public, private, hidden):
            assert client.put(f"/api/playlists/{playlist_id}/videos/{vid}").status_code in (200, 201, 204)
        from app.db import SessionLocal
        from app.models import Video
        with SessionLocal() as db:
            db.get(Video, hidden).hidden = True
            db.commit()
        outsider = new_client()
        playlists = outsider.get(f"/api/playlists?channel_id={owner['id']}").json()
        items = next(p for p in playlists if p["id"] == playlist_id)["videos"]
        assert [v["id"] for v in items] == [public]
        register(outsider, "Outsider")
        assert outsider.put(f"/api/playlists/{playlist_id}/videos/{public}").status_code in (403, 404)


    def test_processing_not_discoverable_and_delete_cascades(self):
        client = self.client
        new_client = self.new_client
        register = self.register
        ready_video = self.ready_video
        owner = register(client)
        vid = ready_video(owner["id"], status="processing")
        outsider = new_client()
        assert outsider.get("/api/videos").json() == []
        assert outsider.get(f"/api/videos/{vid}").status_code == 404
        assert outsider.get(f"/api/videos/{vid}/media").status_code == 404
        assert client.get(f"/api/videos/{vid}").status_code == 200
        assert client.delete(f"/api/videos/{vid}").status_code in (200, 204)
        assert client.get(f"/api/videos/{vid}").status_code == 404
