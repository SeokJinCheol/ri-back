import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from fastapi.testclient import TestClient

from app.api.v1.documents import get_document_service
from app.api.v1.models import get_model_service
from app.core.config import Settings
from app.main import create_app
from app.schemas.projects import ProjectCreate
from app.services.documents import DocumentService
from app.services.models import ModelService
from app.services.projects import ProjectService


class ModelTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.settings = Settings(_env_file=None, document_db_path=Path(self.temp.name) / 'data' / 'test.db')
        self.service = ModelService(self.settings)
        self.project = ProjectService(self.settings).create_project(ProjectCreate(name='Test')).id
        app = create_app()
        app.dependency_overrides[get_model_service] = lambda: self.service
        app.dependency_overrides[get_document_service] = lambda: DocumentService(self.settings)
        self.client = TestClient(app)
        self.url = f'/api/v1/projects/{self.project}/models'
        self.payload = {'name': 'Search', 'provider': 'openai', 'model': 'text-embedding-3-small', 'api_key': 'test-secret-key'}

    def tearDown(self):
        self.client.close()
        self.temp.cleanup()

    def create(self):
        response = self.client.post(self.url, json=self.payload)
        self.assertEqual(response.status_code, 201, response.text)
        self.assertNotIn(self.payload['api_key'], response.text)
        self.assertNotIn('encrypted_api_key', response.json())
        return response.json()

    def test_persistence_encryption_and_key_rotation(self):
        model = self.create()
        self.assertTrue(model['has_api_key'])
        self.assertEqual(self.client.get(self.url).json(), [model])
        self.assertEqual(ModelService(self.settings).list_models(self.project)[0].id, model['id'])
        with closing(self.service.connect()) as connection:
            encrypted = connection.execute('SELECT encrypted_api_key FROM model_configs').fetchone()[0]
        self.assertNotIn(self.payload['api_key'], encrypted)
        self.assertEqual(self.settings.document_db_path.with_suffix('.key').stat().st_mode & 0o777, 0o600)
        update = {key: value for key, value in self.payload.items() if key != 'api_key'}
        self.assertEqual(self.client.put(f"{self.url}/{model['id']}", json=update).status_code, 200)
        self.assertEqual(self.service.embedding_settings(self.project, model['id']).openai_api_key.get_secret_value(), 'test-secret-key')
        update['api_key'] = 'replacement-secret'
        self.assertEqual(self.client.put(f"{self.url}/{model['id']}", json=update).status_code, 200)
        self.assertEqual(self.service.embedding_settings(self.project, model['id']).openai_api_key.get_secret_value(), 'replacement-secret')
        self.assertEqual(self.client.delete(f"{self.url}/{model['id']}").status_code, 204)
        self.assertEqual(self.client.get(self.url).json(), [])

    def test_validation_never_echoes_key(self):
        for changes in [{'name': ''}, {'provider': 'unknown'}, {'model': 'invalid'},
                        {'api_key': 'private secret'}, {'api_key': {'secret': 'private-secret'}}, {'unexpected': 'secret'}]:
            payload = self.payload | changes
            response = self.client.post(self.url, json=payload)
            self.assertEqual(response.status_code, 422, response.text)
            self.assertNotIn('secret', response.text)
            self.assertNotIn('input', response.text)
        self.assertEqual(self.client.post(self.url, json={k: v for k, v in self.payload.items() if k != 'api_key'}).status_code, 422)
        self.assertEqual(self.client.get(self.url).json(), [])

    def test_project_scope(self):
        model = self.create()
        other_url = f'/api/v1/projects/{uuid4()}/models'
        self.assertEqual(self.client.get(other_url).json(), [])
        self.assertEqual(self.client.put(f"{other_url}/{model['id']}", json=self.payload).status_code, 404)
        self.assertEqual(self.client.delete(f"{other_url}/{model['id']}").status_code, 404)
        response = self.client.post('/api/v1/documents', data={'project_id': str(uuid4()), 'model_config_id': model['id']}, files={'file': ('x.txt', b'hello')})
        self.assertEqual(response.status_code, 404)

    def test_upload_uses_saved_key_and_model(self):
        model = self.create()
        with patch('app.services.documents.httpx.Client') as provider:
            post = provider.return_value.__enter__.return_value.post
            post.return_value.json.return_value = {'data': [{'index': 0, 'embedding': [0.1, 0.2]}]}
            response = self.client.post('/api/v1/documents', data={'project_id': self.project, 'model_config_id': model['id']}, files={'file': ('x.txt', b'hello')})
            self.assertEqual(response.status_code, 201, response.text)
            self.assertEqual(post.call_args.kwargs['headers']['Authorization'], 'Bearer test-secret-key')
            self.assertEqual(post.call_args.kwargs['json']['model'], model['model'])
        self.assertNotIn('test-secret-key', response.text)
        self.assertEqual(response.json()['embedding_provider'], 'openai')

    def test_ollama_and_provider_change_clear_key(self):
        model = self.create()
        payload = {'name': 'Local', 'provider': 'ollama', 'model': 'local-embed:v1'}
        response = self.client.put(f"{self.url}/{model['id']}", json=payload)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()['has_api_key'])
        settings = self.service.embedding_settings(self.project, model['id'])
        self.assertEqual(settings.embedding_model, 'local-embed:v1')
        self.assertEqual(settings.embedding_provider, 'ollama')
        with closing(self.service.connect()) as connection:
            self.assertIsNone(connection.execute('SELECT encrypted_api_key FROM model_configs').fetchone()[0])
        self.assertEqual(self.client.post(self.url, json=payload).status_code, 201)

    def test_quantized_ollama_selection_is_used_for_upload(self):
        model_id = 'embeddinggemma:300m-qat-q8_0'
        saved = self.client.post(self.url, json={'name': 'Local Q8', 'provider': 'ollama', 'model': model_id})
        self.assertEqual(saved.status_code, 201, saved.text)
        self.assertEqual(self.client.get(self.url).json()[0]['model'], model_id)
        with patch('app.services.documents.httpx.Client') as provider:
            post = provider.return_value.__enter__.return_value.post
            post.return_value.json.return_value = {'embeddings': [[0.1, 0.2]]}
            response = self.client.post('/api/v1/documents',
                                        data={'project_id': self.project, 'model_config_id': saved.json()['id']},
                                        files={'file': ('korean.txt', '한국어 문서 검색 테스트'.encode())})
            self.assertEqual(response.status_code, 201, response.text)
            self.assertEqual(post.call_args.args[0], self.settings.embedding_base_url + '/api/embed')
            self.assertEqual(post.call_args.kwargs['json']['model'], model_id)
        self.assertEqual(response.json()['embedding_provider'], 'ollama')
        self.assertEqual(response.json()['embedding_model'], model_id)

    def test_conflicting_upload_selection(self):
        model = self.create()
        response = self.client.post('/api/v1/documents', data={'project_id': self.project, 'model_config_id': model['id'], 'embedding_provider': 'ollama'}, files={'file': ('x.txt', b'hello')})
        self.assertEqual(response.status_code, 422)
