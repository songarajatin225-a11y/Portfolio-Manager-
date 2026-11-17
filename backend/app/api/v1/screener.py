from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.api.deps import DB, CurrentUser, WriterUser
from app.core.errors import ConflictError, NotFoundError
from app.db.models import SavedScreen
from app.services.screener import FIELDS, PRESETS, ScreenRequest, run_screen

router = APIRouter(prefix="/screener", tags=["screener"])


@router.get("/fields")
async def fields(_: CurrentUser) -> dict[str, Any]:
    return {"fields": [{"key": k, **v} for k, v in FIELDS.items()], "presets": PRESETS}


@router.post("/run")
async def run(body: ScreenRequest, _: CurrentUser, db: DB) -> dict[str, Any]:
    return await run_screen(db, body)


class SavedScreenIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    definition: ScreenRequest


def _out(s: SavedScreen) -> dict[str, Any]:
    return {"id": s.id, "name": s.name, "definition": s.definition, "updated_at": s.updated_at}


@router.get("/saved")
async def list_saved(user: CurrentUser, db: DB) -> list[dict[str, Any]]:
    rows = (
        await db.scalars(select(SavedScreen).where(SavedScreen.user_id == user.id).order_by(SavedScreen.name))
    ).all()
    return [_out(s) for s in rows]


@router.post("/saved", status_code=201)
async def save(body: SavedScreenIn, user: WriterUser, db: DB) -> dict[str, Any]:
    s = SavedScreen(user_id=user.id, name=body.name.strip(), definition=body.definition.model_dump())
    db.add(s)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise ConflictError("A saved screen with this name already exists") from exc
    await db.refresh(s)
    return _out(s)


@router.put("/saved/{screen_id}")
async def update(screen_id: int, body: SavedScreenIn, user: WriterUser, db: DB) -> dict[str, Any]:
    s = await db.get(SavedScreen, screen_id)
    if s is None or s.user_id != user.id:
        raise NotFoundError("Saved screen not found")
    s.name, s.definition = body.name.strip(), body.definition.model_dump()
    await db.commit()
    await db.refresh(s)
    return _out(s)


@router.delete("/saved/{screen_id}", status_code=204)
async def delete(screen_id: int, user: WriterUser, db: DB) -> None:
    s = await db.get(SavedScreen, screen_id)
    if s is None or s.user_id != user.id:
        raise NotFoundError("Saved screen not found")
    await db.delete(s)
    await db.commit()
