import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app
from app.schemas.indices import IndexCreate
from app.schemas.projects import ProjectCreate
from app.services.indices import IndexService
from app.services.projects import ProjectService


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.settings = Settings(_env_file=None, document_db_path=Path(self.temp.name) / 'test.db')
        settings_patch = patch('app.api.v1.services.get_settings', return_value=self.settings)
        settings_patch.start()
        self.addCleanup(settings_patch.stop)
        self.client = TestClient(create_app())
        self.addCleanup(self.client.close)
        self.project = ProjectService(self.settings).create_project(ProjectCreate(name='Test')).id
        self.base = f'/api/v1/projects/{self.project}/services'
        self.id = str(uuid4())
        self.url = f'{self.base}/{self.id}'
        self.admin = {'X-User-Email': 'admin@example.com'}
        self.member = {'X-User-Email': 'member@example.com'}
        self.payload = {'name': 'Service', 'members': [
            {'email': 'admin@example.com', 'role': 'admin'},
            {'email': 'member@example.com', 'role': 'member'},
        ]}

    def test_save_reload_update_delete_preserves_indices(self):
        created = self.client.put(self.url, json=self.payload, headers=self.admin)
        self.assertEqual(created.status_code, 200, created.text)
        index_service = IndexService(self.settings)
        index = index_service.create(self.project, IndexCreate(name='Index', service_id=self.id, service_name='Service'))
        listed = self.client.get(self.base, headers=self.member).json()
        self.assertEqual(listed, [created.json()])
        updated = self.client.put(self.url, json=self.payload | {'name': 'Renamed', 'index_limit': 8}, headers=self.admin)
        self.assertEqual(updated.status_code, 200, updated.text)
        self.assertEqual(updated.json()['created_at'], created.json()['created_at'])
        self.assertEqual(index_service.list_indices(self.project)[0].service_name, 'Renamed')
        self.assertEqual(self.client.get(self.base, headers=self.admin).json()[0]['index_limit'], 8)
        self.assertEqual(self.client.delete(self.url, headers=self.admin).status_code, 204)
        self.assertEqual(self.client.get(self.base, headers=self.admin).json(), [])
        self.assertEqual(index_service.list_indices(self.project)[0].id, index.id)
        self.assertEqual(self.client.delete(self.url, headers=self.admin).status_code, 404)

    def test_access_and_project_scoping(self):
        self.client.put(self.url, json=self.payload, headers=self.admin)
        self.assertEqual(self.client.get(self.base, headers={'X-User-Email': 'outside@example.com'}).json(), [])
        self.assertEqual(len(self.client.get(self.base, headers={'X-User-Email': ' ADMIN@example.com '}).json()), 1)
        self.assertEqual(self.client.delete(self.url, headers=self.member).status_code, 403)
        self.assertEqual(self.client.put(self.url, json=self.payload, headers=self.member).status_code, 403)
        other = f'/api/v1/projects/{uuid4()}/services'
        self.assertEqual(self.client.get(other, headers=self.admin).status_code, 404)
        self.assertEqual(self.client.delete(f'{other}/{self.id}', headers=self.admin).status_code, 404)
        self.assertEqual(len(self.client.get(self.base, headers=self.admin).json()), 1)
        self.assertEqual(self.client.get(self.base).status_code, 422)
        self.assertEqual(self.client.delete(self.url).status_code, 422)
