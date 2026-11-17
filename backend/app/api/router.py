from fastapi import APIRouter

from app.api.v1 import (
    admin,
    ai,
    alerts,
    auth,
    backtests,
    data,
    instruments,
    journal,
    market,
    ml,
    news,
    portfolios,
    providers,
    screener,
    stocks,
    system,
    users,
    watchlists,
)

api_router = APIRouter()
api_router.include_router(system.router)
api_router.include_router(auth.router)
api_router.include_router(users.router)
api_router.include_router(admin.router)
api_router.include_router(providers.router)
api_router.include_router(market.router)
api_router.include_router(instruments.router)
api_router.include_router(watchlists.router)
api_router.include_router(stocks.router)
api_router.include_router(screener.router)
api_router.include_router(data.router)
api_router.include_router(ai.router)
api_router.include_router(ml.router)
api_router.include_router(news.router)
api_router.include_router(portfolios.router)
api_router.include_router(journal.router)
api_router.include_router(backtests.router)
api_router.include_router(alerts.router)
