from typing import Annotated
from uuid import UUID
from urllib.parse import unquote

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import ValidationError

from app.core.config import get_settings
from app.schemas.projects import ProjectCreate, ProjectMember, ProjectResponse, ProjectUpdate
from app.services.projects import ProjectError, ProjectService

router = APIRouter(prefix="/projects", tags=["projects"])


def get_project_service() -> ProjectService:
    return ProjectService(get_settings())


Service = Annotated[ProjectService, Depends(get_project_service)]
Actor = Annotated[str | None, Header(alias='X-User-Email')]


@router.get("", response_model=list[ProjectResponse])
def list_projects(service: Service):
    return service.list_projects()


@router.post("", response_model=ProjectResponse, status_code=201)
def create_project(payload: ProjectCreate, service: Service, x_user_email: Actor = None,
                   x_user_name: Annotated[str | None, Header()] = None):
    if not x_user_email:
        raise HTTPException(401, '로그인이 필요합니다.')
    try:
        creator = ProjectMember(name=unquote(x_user_name) if x_user_name else x_user_email, email=x_user_email, role='admin')
        return service.create_project(payload, creator)
    except ValidationError as exc:
        raise HTTPException(422, '생성자 이름과 이메일을 확인하세요.') from exc
    except ProjectError as exc:
        raise HTTPException(exc.status_code, exc.detail) from exc


@router.get('/{project_id}/settings', response_model=ProjectResponse)
def project_settings(project_id: UUID, service: Service, x_user_email: Actor = None):
    try:
        return service.settings_for_admin(str(project_id), x_user_email)
    except ProjectError as exc:
        raise HTTPException(exc.status_code, exc.detail) from exc


@router.put('/{project_id}/settings', response_model=ProjectResponse)
def update_project_settings(project_id: UUID, payload: ProjectUpdate, service: Service, x_user_email: Actor = None):
    try:
        return service.update_project(str(project_id), payload, x_user_email)
    except ProjectError as exc:
        raise HTTPException(exc.status_code, exc.detail) from exc
