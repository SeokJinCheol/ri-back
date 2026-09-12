import json
from contextlib import closing
from datetime import datetime, timezone
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Header, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.core.config import get_settings
from app.services.documents import DocumentService
from app.services.projects import ProjectService

router = APIRouter(prefix='/projects/{project_id}/services', tags=['services'])


@router.get('')
def list_services(project_id: UUID, x_user_email: Annotated[str, Header()]):
    actor = x_user_email.strip().lower()
    settings = get_settings()
    with closing(ProjectService(settings).connect()) as projects:
        if not projects.execute('SELECT id FROM projects WHERE id = ?', (str(project_id),)).fetchone():
            raise HTTPException(404, '프로젝트를 찾을 수 없습니다.')
    with closing(DocumentService(settings).connect()) as connection:
        rows = connection.execute('SELECT * FROM service_settings WHERE project_id = ? ORDER BY created_at DESC, id', (str(project_id),)).fetchall()
        services = [{**dict(row), 'members': json.loads(row['members'])} for row in rows]
        return [service for service in services if any(member['email'] == actor for member in service['members'])]


@router.delete('/{service_id}', status_code=204)
def delete_service(project_id: UUID, service_id: UUID, x_user_email: Annotated[str, Header()]):
    actor = x_user_email.strip().lower()
    with closing(DocumentService(get_settings()).connect()) as connection, connection:
        connection.execute('BEGIN IMMEDIATE')
        existing = connection.execute('SELECT members FROM service_settings WHERE project_id = ? AND id = ?', (str(project_id), str(service_id))).fetchone()
        if existing is None:
            raise HTTPException(404, '서비스를 찾을 수 없습니다.')
        if not any(member['email'] == actor and member['role'] == 'admin' for member in json.loads(existing['members'])):
            raise HTTPException(403, '서비스 관리자만 삭제할 수 있습니다.')
        # 인덱스와 문서는 독립적으로 유지하고 서비스 설정과 사용자 등록만 삭제합니다.
        connection.execute('DELETE FROM service_settings WHERE project_id = ? AND id = ?', (str(project_id), str(service_id)))
    return Response(status_code=204)


class Member(BaseModel):
    email: str = Field(min_length=3, max_length=254, pattern=r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
    role: Literal['admin', 'member']


class ServiceWrite(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=80)
    description: str = Field(default='', max_length=500)
    members: list[Member] = Field(min_length=1, max_length=100)
    index_limit: int = Field(default=5, ge=1, le=10000, strict=True)
    created_at: datetime | None = None

    @field_validator('members')
    @classmethod
    def validate_members(cls, members):
        for member in members:
            member.email = member.email.strip().lower()
        if len({member.email for member in members}) != len(members) or not any(member.role == 'admin' for member in members):
            raise ValueError('중복 사용자를 제거하고 관리자를 지정하세요.')
        return members


@router.put('/{service_id}')
def save_service(project_id: UUID, service_id: UUID, payload: ServiceWrite,
                 x_user_email: Annotated[str, Header()]):
    actor = x_user_email.strip().lower()
    settings = get_settings()
    with closing(ProjectService(settings).connect()) as projects:
        if not projects.execute('SELECT id FROM projects WHERE id = ?', (str(project_id),)).fetchone():
            raise HTTPException(404, '프로젝트를 찾을 수 없습니다.')
    with closing(DocumentService(settings).connect()) as connection, connection:
        connection.execute('BEGIN IMMEDIATE')
        existing = connection.execute('SELECT * FROM service_settings WHERE project_id = ? AND id = ?', (str(project_id), str(service_id))).fetchone()
        members = json.loads(existing['members']) if existing else [member.model_dump() for member in payload.members]
        if not any(member['email'] == actor and member['role'] == 'admin' for member in members):
            raise HTTPException(403, '서비스 관리자만 설정을 변경할 수 있습니다.')
        count = connection.execute('SELECT COUNT(*) FROM indices WHERE project_id = ? AND service_id = ?', (str(project_id), str(service_id))).fetchone()[0]
        if payload.index_limit < count:
            raise HTTPException(409, f'현재 인덱스 {count}개보다 한도를 작게 설정할 수 없습니다.')
        now = datetime.now(timezone.utc).isoformat()
        created = existing['created_at'] if existing else (payload.created_at.isoformat() if payload.created_at else now)
        connection.execute('''INSERT INTO service_settings (id, project_id, name, description, members, index_limit, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(project_id, id) DO UPDATE SET
            name=excluded.name, description=excluded.description, members=excluded.members,
            index_limit=excluded.index_limit, updated_at=excluded.updated_at''',
            (str(service_id), str(project_id), payload.name, payload.description, json.dumps([m.model_dump() for m in payload.members]), payload.index_limit, created, now))
        connection.execute('UPDATE indices SET service_name = ?, updated_at = ? WHERE project_id = ? AND service_id = ? AND service_name != ?',
                           (payload.name, now, str(project_id), str(service_id), payload.name))
        return {**payload.model_dump(exclude={'created_at'}), 'id': str(service_id), 'project_id': str(project_id), 'created_at': created, 'updated_at': now}
