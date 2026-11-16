from support import APITestCase


class ApplicationAssetsTests(APITestCase):
    def test_entrypoint_and_required_assets_are_served(self):
        response = self.client.get('/')
        assert response.status_code == 200
        assert 'text/html' in response.headers['content-type']
        assert '/static/app.js' in response.text and '/static/style.css' in response.text
        for path, content_type in (('/static/app.js', 'javascript'), ('/static/style.css', 'text/css')):
            response = self.client.get(path)
            assert response.status_code == 200, path
            assert content_type in response.headers['content-type']
            assert len(response.content) > 100

    def test_swagger_assets_allowed_and_openapi_describes_core_routes(self):
        response = self.client.get('/docs')
        assert response.status_code == 200
        csp = response.headers['content-security-policy']
        assert 'cdn.jsdelivr.net' in csp and "'unsafe-inline'" in csp
        assert 'swagger-ui' in response.text
        schema = self.client.get('/openapi.json').json()
        assert '/api/videos' in schema['paths']
        assert '/api/videos/{video_id}/media' in schema['paths']

    def test_liveness_and_local_readiness(self):
        assert self.client.get('/health/live').json() == {'status': 'ok'}
        assert self.client.get('/health/ready').json() == {'status': 'ready'}
