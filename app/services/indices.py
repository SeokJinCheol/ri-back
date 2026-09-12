from contextlib import closing
from datetime import datetime, timezone
from uuid import uuid4

from app.core.config import Settings
from app.schemas.indices import IndexCreate, IndexResponse
from app.services.documents import DocumentError, DocumentService
from app.services.projects import ProjectService


class IndexService:
    def __init__(self, settings: Settings):
        self.settings = settings

    def list_indices(self, project_id: str) -> list[IndexResponse]:
        with closing(DocumentService(self.settings).connect()) as connection:
            rows = connection.execute('''SELECT i.*, COUNT(d.id) AS document_count FROM indices i
                LEFT JOIN documents d ON d.index_id = i.id AND d.project_id = i.project_id
                WHERE i.project_id = ? GROUP BY i.id ORDER BY i.created_at DESC, i.id''', (project_id,)).fetchall()
            return [IndexResponse(**dict(row)) for row in rows]

    def get_index(self, project_id: str, index_id: str) -> IndexResponse:
        with closing(DocumentService(self.settings).connect()) as connection:
            row = connection.execute('''SELECT i.*, COUNT(d.id) AS document_count FROM indices i
                LEFT JOIN documents d ON d.index_id = i.id AND d.project_id = i.project_id
                WHERE i.project_id = ? AND i.id = ? GROUP BY i.id''', (project_id, index_id)).fetchone()
            if row is None:
                raise DocumentError(404, '인덱스를 찾을 수 없습니다.')
            return IndexResponse(**dict(row))

    def create(self, project_id: str, payload: IndexCreate) -> IndexResponse:
        with closing(ProjectService(self.settings).connect()) as connection:
            if not connection.execute('SELECT id FROM projects WHERE id = ?', (project_id,)).fetchone():
                raise DocumentError(404, '프로젝트를 찾을 수 없습니다.')
        now = datetime.now(timezone.utc).isoformat()
        index = IndexResponse(**payload.model_dump(), id=str(uuid4()), project_id=project_id,
                              created_at=now, updated_at=now)
        with closing(DocumentService(self.settings).connect()) as connection, connection:
            connection.execute('BEGIN IMMEDIATE')
            self.check_capacity(connection, project_id, str(payload.service_id))
            connection.execute('INSERT INTO indices (id, project_id, name, description, service_id, service_name, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
                               (index.id, project_id, index.name, index.description, str(index.service_id), index.service_name, index.created_at, index.updated_at))
        return index

    def delete(self, project_id: str, index_id: str):
        with closing(DocumentService(self.settings).connect()) as connection, connection:
            connection.execute('UPDATE documents SET updated_at = ? WHERE project_id = ? AND index_id = ?', (datetime.now(timezone.utc).isoformat(), project_id, index_id))
            if connection.execute('DELETE FROM indices WHERE project_id = ? AND id = ?', (project_id, index_id)).rowcount == 0:
                raise DocumentError(404, '인덱스를 찾을 수 없습니다.')


    @staticmethod
    def check_capacity(connection, project_id: str, service_id: str, excluding: str = ''):
        row = connection.execute('SELECT index_limit FROM service_settings WHERE project_id = ? AND id = ?', (project_id, service_id)).fetchone()
        limit = row['index_limit'] if row else 5
        count = connection.execute('SELECT COUNT(*) FROM indices WHERE project_id = ? AND service_id = ? AND id != ?', (project_id, service_id, excluding)).fetchone()[0]
        if count >= limit:
            raise DocumentError(409, f'서비스의 인덱스 한도 {limit}개에 도달했습니다. 관리자에게 한도 변경을 요청하세요.')

    def update(self, project_id: str, index_id: str, payload: IndexCreate) -> IndexResponse:
        with closing(DocumentService(self.settings).connect()) as connection, connection:
            connection.execute('BEGIN IMMEDIATE')
            if not connection.execute('SELECT id FROM indices WHERE id = ? AND project_id = ?', (index_id, project_id)).fetchone():
                raise DocumentError(404, '인덱스를 찾을 수 없습니다.')
            self.check_capacity(connection, project_id, str(payload.service_id), index_id)
            connection.execute('UPDATE indices SET name = ?, description = ?, service_id = ?, service_name = ?, updated_at = ? WHERE id = ? AND project_id = ?',
                               (payload.name, payload.description, str(payload.service_id), payload.service_name, datetime.now(timezone.utc).isoformat(), index_id, project_id))
        return self.get_index(project_id, index_id)
