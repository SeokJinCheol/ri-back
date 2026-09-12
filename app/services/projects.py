import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from uuid import uuid4

from app.core.config import Settings
from app.schemas.projects import ProjectCreate, ProjectMember, ProjectResponse, ProjectUpdate


class ProjectError(Exception):
    def __init__(self, status_code: int, detail: str):
        self.status_code = status_code
        self.detail = detail
        super().__init__(detail)


class ProjectService:
    def __init__(self, settings: Settings):
        self.settings = settings

    def connect(self) -> sqlite3.Connection:
        self.settings.document_db_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.settings.document_db_path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("""
            CREATE TABLE IF NOT EXISTS projects (
                id TEXT PRIMARY KEY, name TEXT NOT NULL,
                description TEXT NOT NULL, created_at TEXT NOT NULL
            )
        """)
        connection.execute("""
            CREATE TABLE IF NOT EXISTS project_members (
                project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
                name TEXT NOT NULL, email TEXT NOT NULL,
                position INTEGER NOT NULL,
                PRIMARY KEY (project_id, email)
            )
        """)
        with connection:
            connection.execute('BEGIN IMMEDIATE')
            columns = {row[1] for row in connection.execute('PRAGMA table_info(projects)')}
            if 'creator_email' not in columns:
                connection.execute('ALTER TABLE projects ADD COLUMN creator_email TEXT')
            if 'updated_at' not in columns:
                connection.execute('ALTER TABLE projects ADD COLUMN updated_at TEXT')
                connection.execute('UPDATE projects SET updated_at = created_at')
            member_columns = {row[1] for row in connection.execute('PRAGMA table_info(project_members)')}
            if 'role' not in member_columns:
                connection.execute("ALTER TABLE project_members ADD COLUMN role TEXT NOT NULL DEFAULT 'member'")
        return connection

    def list_projects(self) -> list[ProjectResponse]:
        with closing(self.connect()) as connection:
            rows = connection.execute("SELECT * FROM projects ORDER BY created_at, id").fetchall()
            members: dict[str, list[ProjectMember]] = {}
            for member in connection.execute("SELECT * FROM project_members ORDER BY position"):
                members.setdefault(member["project_id"], []).append(
                    ProjectMember(name=member["name"], email=member["email"], role=member["role"]))
            return [ProjectResponse(**dict(row), members=members.get(row["id"], [])) for row in rows]

    @staticmethod
    def read_project(connection, project_id: str) -> ProjectResponse:
        row = connection.execute('SELECT * FROM projects WHERE id = ?', (project_id,)).fetchone()
        if row is None:
            raise ProjectError(404, '프로젝트를 찾을 수 없습니다.')
        members = connection.execute('SELECT name, email, role FROM project_members WHERE project_id = ? ORDER BY position', (project_id,)).fetchall()
        return ProjectResponse(**dict(row), members=[ProjectMember(**dict(member)) for member in members])

    @staticmethod
    def check_admin(project: ProjectResponse, actor: str | None):
        if not actor:
            raise ProjectError(401, '로그인이 필요합니다.')
        email = actor.strip().lower()
        if not project.creator_email:
            raise ProjectError(403, '이 프로젝트의 생성자 정보가 없어 관리자 지정이 필요합니다.')
        if email != project.creator_email and not any(member.email == email and member.role == 'admin' for member in project.members):
            raise ProjectError(403, '프로젝트 관리자만 설정을 조회하거나 변경할 수 있습니다.')

    def settings_for_admin(self, project_id: str, actor: str | None) -> ProjectResponse:
        with closing(self.connect()) as connection:
            project = self.read_project(connection, project_id)
            self.check_admin(project, actor)
            return project

    def create_project(self, payload: ProjectCreate, creator: ProjectMember | None = None) -> ProjectResponse:
        members = payload.members
        if creator:
            members = [creator.model_copy(update={'role': 'admin'}), *[member for member in members if member.email != creator.email]]
        if len(members) > 100:
            raise ProjectError(422, '생성자를 포함해 사용자는 최대 100명까지 등록할 수 있습니다.')
        now = datetime.now(timezone.utc).isoformat()
        project = ProjectResponse(
            name=payload.name, description=payload.description, members=members, id=str(uuid4()),
            created_at=now, updated_at=now, creator_email=creator.email if creator else None,
        )
        with closing(self.connect()) as connection, connection:
            connection.execute(
                'INSERT INTO projects (id, name, description, created_at, updated_at, creator_email) VALUES (?, ?, ?, ?, ?, ?)',
                (project.id, project.name, project.description, project.created_at, project.updated_at, project.creator_email),
            )
            self.write_members(connection, project.id, project.members)
        return project

    @staticmethod
    def write_members(connection, project_id, members):
        connection.executemany(
            'INSERT INTO project_members (project_id, name, email, position, role) VALUES (?, ?, ?, ?, ?)',
            [(project_id, member.name, member.email, index, member.role) for index, member in enumerate(members)],
        )

    def update_project(self, project_id: str, payload: ProjectUpdate, actor: str | None) -> ProjectResponse:
        with closing(self.connect()) as connection, connection:
            connection.execute('BEGIN IMMEDIATE')
            project = self.read_project(connection, project_id)
            self.check_admin(project, actor)
            if not any(member.email == project.creator_email and member.role == 'admin' for member in payload.members):
                raise ProjectError(422, '생성자는 관리자에서 해제하거나 프로젝트에서 제거할 수 없습니다.')
            connection.execute('UPDATE projects SET name = ?, description = ?, updated_at = ? WHERE id = ?',
                               (payload.name, payload.description, datetime.now(timezone.utc).isoformat(), project_id))
            connection.execute('DELETE FROM project_members WHERE project_id = ?', (project_id,))
            self.write_members(connection, project_id, payload.members)
            return self.read_project(connection, project_id)
