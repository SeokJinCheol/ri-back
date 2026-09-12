import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from fastapi.testclient import TestClient

from app.api.v1.documents import get_document_service
from app.api.v1.indices import get_index_service
from app.core.config import Settings
from app.main import create_app
from app.schemas.indices import IndexCreate
from app.schemas.projects import ProjectCreate
from app.services.documents import DocumentError, DocumentService
from app.services.indices import IndexService
from app.services.projects import ProjectService


class LimitsChunkTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.settings = Settings(_env_file=None, document_db_path=Path(self.temp.name) / 'test.db', chunk_size=100, chunk_overlap=20)
        self.project = ProjectService(self.settings).create_project(ProjectCreate(name='Test')).id
        self.service_id = str(uuid4())
        self.docs = DocumentService(self.settings)
        self.indices = IndexService(self.settings)
        self.payload = IndexCreate(name='Index', service_id=self.service_id, service_name='Test')
        app = create_app()
        app.dependency_overrides[get_document_service] = lambda: self.docs
        app.dependency_overrides[get_index_service] = lambda: self.indices
        self.client = TestClient(app)
        self.settings_patch = patch('app.api.v1.services.get_settings', return_value=self.settings)
        self.settings_patch.start()
        self.service_url = f'/api/v1/projects/{self.project}/services/{self.service_id}'
        self.service_payload = {'name': 'Service', 'members': [{'email': 'admin@example.com', 'role': 'admin'}, {'email': 'member@example.com', 'role': 'member'}]}

    def tearDown(self):
        self.settings_patch.stop()
        self.client.close()
        self.temp.cleanup()

    def save_service(self, limit=5, actor='admin@example.com'):
        return self.client.put(self.service_url, json=self.service_payload | {'index_limit': limit}, headers={'X-User-Email': actor})

    def test_service_dates_admin_and_limit(self):
        created = self.save_service().json()
        self.assertEqual(created['index_limit'], 5)
        self.assertEqual(created['created_at'], created['updated_at'])
        for _ in range(5):
            self.indices.create(self.project, self.payload)
        with self.assertRaises(DocumentError) as error:
            self.indices.create(self.project, self.payload)
        self.assertEqual(error.exception.status_code, 409)
        self.assertEqual(self.save_service(6, 'member@example.com').status_code, 403)
        self.assertEqual(self.save_service(4).status_code, 409)
        updated = self.save_service(6).json()
        self.assertEqual(updated['created_at'], created['created_at'])
        self.assertGreater(updated['updated_at'], created['updated_at'])
        self.indices.create(self.project, self.payload)
        with self.assertRaises(DocumentError):
            self.indices.create(self.project, self.payload)

    def test_default_capacity_concurrent_creates(self):
        def create(_):
            try:
                return self.indices.create(self.project, self.payload).id
            except DocumentError:
                return None
        with ThreadPoolExecutor(max_workers=4) as executor:
            results = list(executor.map(create, range(8)))
        self.assertEqual(sum(value is not None for value in results), 5)
        self.assertEqual(len(self.indices.list_indices(self.project)), 5)

    def test_index_update_preserves_creation_date(self):
        index = self.indices.create(self.project, self.payload)
        response = self.client.put(f'/api/v1/projects/{self.project}/indices/{index.id}', json={**self.payload.model_dump(mode='json'), 'name': 'Renamed'})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['created_at'], index.created_at)
        self.assertGreater(response.json()['updated_at'], index.updated_at)

    def test_embedding_byte_boundary_and_rollback(self):
        size = len(json.dumps([0.1, 0.2]).encode())
        self.settings.max_embedding_bytes = size
        with patch.object(self.docs, 'embed', return_value=[[0.1, 0.2]]):
            doc = self.docs.ingest(self.project, 'ok.txt', b'hello')
        self.assertEqual(doc.embedding_size_bytes, size)
        self.settings.max_embedding_bytes = size - 1
        with patch.object(self.docs, 'embed', return_value=[[0.1, 0.2]]):
            with self.assertRaises(DocumentError) as error:
                self.docs.ingest(self.project, 'too-big.txt', b'hello')
        self.assertEqual(error.exception.status_code, 413)
        self.assertEqual(len(self.docs.list_documents(self.project)), 1)

    def test_embedding_limit_while_receiving_batches(self):
        self.settings.max_embedding_bytes = 1
        with patch('app.services.documents.httpx.Client') as provider:
            provider.return_value.__enter__.return_value.post.return_value.json.return_value = {'embeddings': [[0.1, 0.2]]}
            with self.assertRaises(DocumentError) as error:
                self.docs.embed(['hello'])
        self.assertEqual(error.exception.status_code, 413)

    def test_chunk_pagination_scoping_and_document_update(self):
        with patch.object(self.docs, 'embed', side_effect=lambda chunks: [[0.1, 0.2] for _ in chunks]):
            doc = self.docs.ingest(self.project, 'long.txt', b'x' * 5000)
        self.assertGreater(doc.chunk_count, 50)
        url = f'/api/v1/documents/{doc.id}/chunks'
        first = self.client.get(url, params={'project_id': self.project, 'offset': 0, 'limit': 50}).json()
        second = self.client.get(url, params={'project_id': self.project, 'offset': 50, 'limit': 50}).json()
        chunks = first['items'] + second['items']
        self.assertEqual([item['chunk_index'] for item in chunks], list(range(doc.chunk_count)))
        self.assertEqual(sum(item['embedding_size_bytes'] for item in chunks), doc.embedding_size_bytes)
        self.assertNotIn('embedding', chunks[0])
        self.assertEqual(self.client.get(url, params={'project_id': str(uuid4())}).status_code, 404)
        self.assertEqual(self.client.get(url, params={'project_id': self.project, 'limit': 101}).status_code, 422)
        self.assertEqual(self.client.get(url, params={'project_id': self.project, 'offset': -1}).status_code, 422)
        response = self.client.put(f'/api/v1/documents/{doc.id}', params={'project_id': self.project}, json={'filename': 'renamed.txt'})
        self.assertEqual(response.status_code, 200)
        updated = response.json()
        self.assertEqual(updated['created_at'], doc.created_at)
        self.assertGreater(updated['updated_at'], doc.updated_at)
        self.assertEqual(updated['embedding_size_bytes'], doc.embedding_size_bytes)
        self.assertEqual(updated['chunk_count'], doc.chunk_count)
        self.assertEqual(self.docs.list_chunks(self.project, doc.id, 0, 50).items[0].content, chunks[0]['content'])
