import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from fastapi.testclient import TestClient

from app.api.v1.documents import get_document_service
from app.api.v1.indices import get_index_service
from app.core.config import Settings
from app.main import create_app
from app.schemas.projects import ProjectCreate
from app.services.documents import DocumentError, DocumentService
from app.services.indices import IndexService
from app.services.projects import ProjectService


class IndexTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.settings = Settings(_env_file=None, document_db_path=Path(self.temp.name) / 'test.db')
        self.project = ProjectService(self.settings).create_project(ProjectCreate(name='Project')).id
        self.indices = IndexService(self.settings)
        self.documents = DocumentService(self.settings)
        app = create_app()
        app.dependency_overrides[get_index_service] = lambda: self.indices
        app.dependency_overrides[get_document_service] = lambda: self.documents
        self.client = TestClient(app)
        self.url = f'/api/v1/projects/{self.project}/indices'
        self.payload = {'name': '  Index  ', 'description': ' Description ', 'service_id': str(uuid4()), 'service_name': 'Support'}

    def tearDown(self):
        self.client.close()
        self.temp.cleanup()

    def create(self):
        response = self.client.post(self.url, json=self.payload)
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def upload(self, index_id, project_id=None):
        with patch.object(self.documents, 'embed', return_value=[[0.1, 0.2]]):
            return self.client.post('/api/v1/documents', data={'project_id': project_id or self.project, 'index_id': index_id}, files={'file': ('x.txt', b'hello')})

    def test_create_detail_list_and_persistence(self):
        index = self.create()
        self.assertEqual(index['name'], 'Index')
        self.assertEqual(index['description'], 'Description')
        self.assertEqual(index['document_count'], 0)
        self.assertEqual(self.client.get(self.url).json(), [index])
        self.assertEqual(self.client.get(f"{self.url}/{index['id']}").json(), index)
        self.assertEqual(IndexService(self.settings).get_index(self.project, index['id']).id, index['id'])

    def test_upload_filter_count_and_delete_preserves_documents(self):
        first = self.create()
        second = self.create()
        first_doc = self.upload(first['id'])
        second_doc = self.upload(second['id'])
        self.assertEqual(first_doc.status_code, 201, first_doc.text)
        self.assertEqual(second_doc.status_code, 201, second_doc.text)
        self.assertEqual(first_doc.json()['index_id'], first['id'])
        response = self.client.get('/api/v1/documents', params={'project_id': self.project, 'index_id': first['id']})
        self.assertEqual(response.json(), [first_doc.json()])
        self.assertEqual(self.client.get(f"{self.url}/{first['id']}").json()['document_count'], 1)
        self.assertEqual(self.client.delete(f"{self.url}/{first['id']}").status_code, 204)
        docs = {doc.id: doc for doc in self.documents.list_documents(self.project)}
        self.assertEqual(len(docs), 2)
        self.assertIsNone(docs[first_doc.json()['id']].index_id)
        self.assertEqual(docs[second_doc.json()['id']].index_id, second['id'])
        with closing(self.documents.connect()) as connection:
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM chunks').fetchone()[0], 2)
        self.assertEqual(self.client.get(f"{self.url}/{first['id']}").status_code, 404)

    def test_project_scope_and_invalid_selection(self):
        index = self.create()
        other = str(uuid4())
        other_url = f'/api/v1/projects/{other}/indices'
        self.assertEqual(self.client.get(other_url).json(), [])
        self.assertEqual(self.client.get(f"{other_url}/{index['id']}").status_code, 404)
        self.assertEqual(self.client.delete(f"{other_url}/{index['id']}").status_code, 404)
        self.assertEqual(self.upload(index['id'], other).status_code, 404)
        self.assertEqual(self.upload(str(uuid4())).status_code, 404)
        self.assertEqual(self.client.get('/api/v1/documents', params={'project_id': other, 'index_id': index['id']}).status_code, 404)
        self.assertEqual(self.client.post(other_url, json=self.payload).status_code, 404)
        self.assertEqual(self.documents.list_documents(self.project), [])

    def test_validation(self):
        for change in [{'name': ' '}, {'name': 'x' * 81}, {'description': 'x' * 501}, {'service_id': 'invalid'}, {'service_name': ''}]:
            self.assertEqual(self.client.post(self.url, json=self.payload | change).status_code, 422)
        self.assertEqual(self.client.get(self.url).json(), [])

    def test_deleted_during_embedding_does_not_store(self):
        index = self.create()
        def embed(_chunks):
            self.indices.delete(self.project, index['id'])
            return [[0.1, 0.2]]
        with patch.object(self.documents, 'embed', side_effect=embed):
            with self.assertRaises(DocumentError):
                self.documents.ingest(self.project, 'x.txt', b'hello', index['id'])
        self.assertEqual(self.documents.list_documents(self.project), [])

    def test_legacy_documents_migrate_without_index(self):
        with closing(sqlite3.connect(self.settings.document_db_path)) as connection, connection:
            connection.execute('''CREATE TABLE documents (id TEXT PRIMARY KEY, project_id TEXT NOT NULL,
                filename TEXT NOT NULL, size_bytes INTEGER NOT NULL, chunk_count INTEGER NOT NULL,
                embedding_model TEXT NOT NULL, embedding_dimensions INTEGER NOT NULL, created_at TEXT NOT NULL)''')
            connection.execute('INSERT INTO documents VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
                               ('legacy', self.project, 'old.txt', 4, 1, 'embeddinggemma', 2, '2026-09-01'))
        for _ in range(2):
            documents = self.documents.list_documents(self.project)
            self.assertEqual(documents[0].id, 'legacy')
            self.assertIsNone(documents[0].index_id)
        self.create()
