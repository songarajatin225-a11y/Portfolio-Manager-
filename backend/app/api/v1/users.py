from __future__ import annotations

import uuid

from fastapi import APIRouter, Request
from sqlalchemy import select

from app.api.deps import DB, AdminUser
from app.core.errors import AppError, NotFoundError
from app.core.security import hash_password, validate_password_strength
from app.db.models import Role, User
from app.schemas.auth import UserCreate, UserOut, UserUpdate
from app.services import auth as auth_service
from app.services.audit import audit

router = APIRouter(prefix="/users", tags=["users"])


@router.get("", response_model=list[UserOut])
async def list_users(_: AdminUser, db: DB) -> list[UserOut]:
    rows = (await db.scalars(select(User).order_by(User.created_at))).all()
    return [UserOut.model_validate(u) for u in rows]


@router.post("", response_model=UserOut, status_code=201)
async def create_user(body: UserCreate, request: Request, admin: AdminUser, db: DB) -> UserOut:
    user = await auth_service.create_user(db, email=body.email, password=body.password,
                                          full_name=body.full_name, role=body.role)
    await audit(db, action="user.create", user_id=admin.id, request=request, entity_type="user",
                entity_id=str(user.id), details={"email": user.email, "role": user.role})
    await db.commit()
    return UserOut.model_validate(user)


@router.patch("/{user_id}", response_model=UserOut)
async def update_user(user_id: uuid.UUID, body: UserUpdate, request: Request, admin: AdminUser, db: DB) -> UserOut:
    user = await db.get(User, user_id)
    if user is None:
        raise NotFoundError("User not found")
    changes: dict = {}
    if body.full_name is not None:
        user.full_name = body.full_name
        changes["full_name"] = True
    if body.role is not None and body.role != user.role:
        if user.id == admin.id and body.role != Role.ADMIN:
            raise AppError("You cannot remove your own admin role")
        changes["role"] = {"from": user.role, "to": body.role}
        user.role = body.role
    if body.is_active is not None and body.is_active != user.is_active:
        if user.id == admin.id and not body.is_active:
            raise AppError("You cannot deactivate your own account")
        user.is_active = body.is_active
        changes["is_active"] = body.is_active
    if body.password:
        problems = validate_password_strength(body.password)
        if problems:
            raise AppError("; ".join(problems), code="weak_password", status_code=422)
        user.password_hash = hash_password(body.password)
        changes["password_reset"] = True
    await audit(db, action="user.update", user_id=admin.id, request=request, entity_type="user",
                entity_id=str(user.id), details=changes)
    await db.commit()
    return UserOut.model_validate(user)
