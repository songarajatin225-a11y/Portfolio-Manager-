from fastapi import APIRouter

from app.api.v1 import admin, auth, instruments, market, providers, system, users, watchlists

api_router = APIRouter()
api_router.include_router(system.router)
api_router.include_router(auth.router)
api_router.include_router(users.router)
api_router.include_router(admin.router)
api_router.include_router(providers.router)
api_router.include_router(market.router)
api_router.include_router(instruments.router)
api_router.include_router(watchlists.router)
