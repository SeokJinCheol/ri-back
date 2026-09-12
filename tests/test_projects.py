import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from uuid import UUID

from fastapi.testclient import TestClient

from app.api.v1.projects import get_project_service
from app.core.config import Settings
from app.main import create_app
from app.services.projects import ProjectService


class ProjectTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.settings = Settings(_env_file=None, document_db_path=Path(self.temp.name) / "data" / "app.db")
        app = create_app()
        app.dependency_overrides[get_project_service] = lambda: ProjectService(self.settings)
        self.client = TestClient(app, headers={'X-User-Email': 'creator@example.com', 'X-User-Name': 'Creator'})

    def tearDown(self):
        self.client.close()
        self.temp.cleanup()

    def test_create_and_list_persist_across_app_instances(self):
        self.assertEqual(self.client.get('/api/v1/projects').json(), [])
        response = self.client.post('/api/v1/projects', json={'name': '  테스트 프로젝트  ', 'description': '  설명  '})
        self.assertEqual(response.status_code, 201, response.text)
        project = response.json()
        UUID(project['id'])
        self.assertEqual(project['name'], '테스트 프로젝트')
        self.assertEqual(project['description'], '설명')
        second = self.client.post('/api/v1/projects', json={'name': "Second ' project"}).json()
        app = create_app()
        app.dependency_overrides[get_project_service] = lambda: ProjectService(self.settings)
        with TestClient(app) as client:
            self.assertEqual(client.get('/api/v1/projects').json(), [project, second])
        with closing(sqlite3.connect(self.settings.document_db_path)) as connection:
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM projects').fetchone()[0], 2)

    def test_invalid_input_does_not_persist(self):
        for payload in [{}, {'name': ''}, {'name': '   '}, {'name': 'x' * 81},
                        {'name': 'test', 'description': 'x' * 201}]:
            with self.subTest(payload=payload):
                self.assertEqual(self.client.post('/api/v1/projects', json=payload).status_code, 422)
        self.assertEqual(self.client.get('/api/v1/projects').json(), [])

    def test_members_persist_and_stay_in_their_project(self):
        payload = {'name': 'Team', 'members': [
            {'name': '  홍길동  ', 'email': '  Hong@Example.com  '},
            {'name': '김철수', 'email': 'kim@example.com'},
        ]}
        response = self.client.post('/api/v1/projects', json=payload)
        self.assertEqual(response.status_code, 201, response.text)
        project = response.json()
        self.assertEqual(project['members'], [
            {'name': 'Creator', 'email': 'creator@example.com', 'role': 'admin'},
            {'name': '홍길동', 'email': 'hong@example.com', 'role': 'member'},
            {'name': '김철수', 'email': 'kim@example.com', 'role': 'member'},
        ])
        other = self.client.post('/api/v1/projects', json={'name': 'Other'}).json()
        self.assertEqual(other['members'], [{'name': 'Creator', 'email': 'creator@example.com', 'role': 'admin'}])
        projects = ProjectService(self.settings).list_projects()
        self.assertEqual(projects[0].model_dump(), project)
        self.assertEqual(projects[1].model_dump(), other)

    def test_invalid_members_reject_whole_project(self):
        for members in [
            [{'name': ' ', 'email': 'a@example.com'}],
            [{'name': 'A', 'email': 'invalid'}],
            [{'name': 'A', 'email': 'a@example.com'}, {'name': 'B', 'email': ' A@EXAMPLE.COM '}],
            [{'name': 'A', 'email': 'a@example.com'}] * 101,
        ]:
            with self.subTest(members=members):
                response = self.client.post('/api/v1/projects', json={'name': 'Invalid', 'members': members})
                self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(self.client.get('/api/v1/projects').json(), [])

    def test_existing_database_preserved(self):
        self.settings.document_db_path.parent.mkdir(parents=True)
        with closing(sqlite3.connect(self.settings.document_db_path)) as connection, connection:
            connection.execute('CREATE TABLE projects (id TEXT PRIMARY KEY, name TEXT NOT NULL, description TEXT NOT NULL, created_at TEXT NOT NULL)')
            connection.execute("INSERT INTO projects VALUES ('existing', 'Existing', '', '2026-09-01')")
        projects = ProjectService(self.settings).list_projects()
        self.assertEqual(projects[0].id, 'existing')
        self.assertEqual(projects[0].members, [])

    def test_member_write_failure_rolls_back_project(self):
        from app.schemas.projects import ProjectCreate
        service = ProjectService(self.settings)
        with closing(service.connect()) as connection, connection:
            connection.execute("CREATE TRIGGER reject_member BEFORE INSERT ON project_members BEGIN SELECT RAISE(ABORT, 'test failure'); END")
        with self.assertRaises(sqlite3.IntegrityError):
            service.create_project(ProjectCreate(name='Rollback', members=[{'name': 'A', 'email': 'a@example.com'}]))
        self.assertEqual(service.list_projects(), [])


    def test_creator_and_designated_admin_can_manage_settings(self):
        response = self.client.post('/api/v1/projects', json={'name': 'Admin test', 'members': [
            {'name': 'Admin', 'email': 'admin@example.com', 'role': 'admin'},
            {'name': 'Member', 'email': 'member@example.com'},
        ]})
        project = response.json()
        url = f"/api/v1/projects/{project['id']}/settings"
        self.assertEqual(project['creator_email'], 'creator@example.com')
        self.assertEqual(project['members'][0]['role'], 'admin')
        self.assertEqual(self.client.get(url).status_code, 200)
        self.assertEqual(self.client.get(url, headers={'X-User-Email': 'ADMIN@EXAMPLE.COM'}).status_code, 200)
        payload = {'name': 'Renamed', 'description': 'Updated', 'members': project['members']}
        response = self.client.put(url, json=payload, headers={'X-User-Email': 'admin@example.com'})
        self.assertEqual(response.status_code, 200, response.text)
        saved = response.json()
        self.assertEqual(saved['name'], 'Renamed')
        self.assertEqual(saved['created_at'], project['created_at'])
        self.assertGreater(saved['updated_at'], project['updated_at'])
        for actor in ['member@example.com', 'outsider@example.com']:
            self.assertEqual(self.client.get(url, headers={'X-User-Email': actor}).status_code, 403)
            self.assertEqual(self.client.put(url, json=payload, headers={'X-User-Email': actor}).status_code, 403)
        self.assertEqual(self.client.get(url).json(), saved)

    def test_creator_cannot_be_removed_or_demoted(self):
        project = self.client.post('/api/v1/projects', json={'name': 'Protected'}).json()
        url = f"/api/v1/projects/{project['id']}/settings"
        for members in [[], [{**project['members'][0], 'role': 'member'}],
                        [{**project['members'][0], 'email': 'changed@example.com'}]]:
            self.assertEqual(self.client.put(url, json={'name': 'Changed', 'members': members}).status_code, 422)
            self.assertEqual(self.client.get(url).json(), project)

    def test_revoked_admin_loses_access_immediately(self):
        project = self.client.post('/api/v1/projects', json={'name': 'Roles', 'members': [{'name': 'Other', 'email': 'other@example.com', 'role': 'admin'}]}).json()
        url = f"/api/v1/projects/{project['id']}/settings"
        project['members'][1]['role'] = 'member'
        payload = {'name': project['name'], 'members': project['members']}
        self.assertEqual(self.client.put(url, json=payload).status_code, 200)
        payload['members'][1]['role'] = 'admin'
        self.assertEqual(self.client.put(url, json=payload, headers={'X-User-Email': 'other@example.com'}).status_code, 403)
        self.assertEqual(self.client.get(url, headers={'X-User-Email': 'other@example.com'}).status_code, 403)

    def test_missing_identity_and_unowned_project_are_denied(self):
        from app.schemas.projects import ProjectCreate
        project = self.client.post('/api/v1/projects', json={'name': 'Identity'}).json()
        url = f"/api/v1/projects/{project['id']}/settings"
        self.client.headers.pop('X-User-Email')
        self.assertEqual(self.client.get(url).status_code, 401)
        self.assertEqual(self.client.post('/api/v1/projects', json={'name': 'No actor'}).status_code, 401)
        old = ProjectService(self.settings).create_project(ProjectCreate(name='Legacy'))
        response = self.client.get(f'/api/v1/projects/{old.id}/settings', headers={'X-User-Email': 'creator@example.com'})
        self.assertEqual(response.status_code, 403)
        self.assertIsNone(ProjectService(self.settings).list_projects()[-1].creator_email)

    def test_creator_is_not_duplicated_and_name_supports_korean(self):
        response = self.client.post('/api/v1/projects', json={'name': 'Creator', 'members': [
            {'name': 'Other name', 'email': 'CREATOR@example.com', 'role': 'member'}
        ]}, headers={'X-User-Name': '%ED%99%8D%EA%B8%B8%EB%8F%99'})
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()['members'], [{'name': '홍길동', 'email': 'creator@example.com', 'role': 'admin'}])
