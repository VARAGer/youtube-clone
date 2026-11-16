from support import APITestCase


class AuthTests(APITestCase):
    def test_registration_login_logout_cookie_and_password(self):
        client = self.client
        register = self.register
        assert client.get("/api/auth/me").status_code == 401
        user = register(client)
        assert user["role"] == "user"
        assert "password" not in user and "password_hash" not in user
        assert client.cookies.get("session"), "Registration must establish an opaque session"
        assert client.get("/api/auth/me").json()["id"] == user["id"]
        assert client.post("/api/auth/logout").status_code in (200, 204)
        assert client.get("/api/auth/me").status_code == 401
        assert client.post("/api/auth/login", json={"email": user["email"], "password": "incorrect"}).status_code == 401
        response = client.post("/api/auth/login", json={"email": user["email"], "password": "StrongPassword123!"})
        assert response.status_code == 200
        assert "httponly" in response.headers["set-cookie"].lower()
        assert "samesite" in response.headers["set-cookie"].lower()


    def test_registration_cannot_assign_staff_role(self):
        client = self.client
        response = client.post("/api/auth/register", json={
            "email": "attacker@example.com", "password": "StrongPassword123!",
            "display_name": "Attacker", "role": "admin",
        })
        assert response.status_code in (200, 201, 422)
        if response.status_code != 422:
            assert response.json()["role"] == "user"
        assert client.get("/api/admin/users").status_code in (401, 403)


    def test_duplicate_email_and_weak_password(self):
        client = self.client
        register = self.register
        register(client)
        duplicate = client.post("/api/auth/register", json={"email": "owner@example.com", "password": "StrongPassword123!", "display_name": "Other"})
        assert duplicate.status_code in (400, 409)
        weak = client.post("/api/auth/register", json={"email": "new@example.com", "password": "a", "display_name": "Other"})
        assert weak.status_code == 422


    def test_cross_origin_write_rejected(self):
        client = self.client
        register = self.register
        register(client)
        response = client.post("/api/playlists", json={"title": "CSRF"}, headers={"Origin": "https://evil.example"})
        assert response.status_code == 403


    def test_staff_endpoints_reject_regular_users(self):
        client = self.client
        register = self.register
        register(client)
        for path in ("/api/admin/users", "/api/moderation/reports"):
            assert client.get(path).status_code == 403

    def test_only_admin_changes_roles(self):
        owner = self.register(self.client)
        moderator = self.new_client()
        mod = self.register(moderator, "Moderator")
        self.set_role(mod["id"], "moderator")
        assert moderator.patch(f"/api/admin/users/{owner['id']}/role", json={"role": "admin"}).status_code == 403
        admin = self.new_client()
        user = self.register(admin, "Administrator")
        self.set_role(user["id"], "admin")
        assert admin.patch(f"/api/admin/users/{owner['id']}/role", json={"role": "moderator"}).status_code == 200
        assert self.client.get("/api/auth/me").json()["role"] == "moderator"
        assert admin.patch(f"/api/admin/users/{owner['id']}/role", json={"role": "superuser"}).status_code == 422

    def test_auth_rate_limit_enforced(self):
        for _ in range(20):
            response = self.client.post("/api/auth/login", json={"email": "unknown@example.com", "password": "StrongPassword123!"})
            assert response.status_code == 401
        response = self.client.post("/api/auth/login", json={"email": "unknown@example.com", "password": "StrongPassword123!"})
        assert response.status_code == 429
        assert self.client.get("/api/auth/me").status_code == 401
