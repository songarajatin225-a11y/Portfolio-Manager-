"""Import every model so Alembic autogenerate and ``Base.metadata`` see all tables."""

from app.db.models.ai import AIConversation, AIMessage, AIReport
from app.db.models.alerts import Alert, Notification
from app.db.models.fundamentals import BulkBlockDeal, Fundamental, InstitutionalFlow, Shareholding
from app.db.models.market import Candle, IndexConstituent, Instrument, InstrumentMetrics, InstrumentProviderKey
from app.db.models.ml import MLModel, MLPrediction
from app.db.models.news import NewsArticle, NewsArticleInstrument, NewsSource
from app.db.models.portfolio import (
    Backtest,
    JournalTrade,
    Portfolio,
    SavedScreen,
    Transaction,
    Watchlist,
    WatchlistItem,
)
from app.db.models.settings import AppSetting, MarketHoliday, ProviderConfig
from app.db.models.user import AuditLog, RefreshToken, Role, User

__all__ = [
    "AIConversation",
    "AIMessage",
    "AIReport",
    "Alert",
    "AppSetting",
    "AuditLog",
    "Backtest",
    "BulkBlockDeal",
    "Candle",
    "Fundamental",
    "IndexConstituent",
    "InstitutionalFlow",
    "Instrument",
    "InstrumentMetrics",
    "InstrumentProviderKey",
    "JournalTrade",
    "MLModel",
    "MLPrediction",
    "MarketHoliday",
    "NewsArticle",
    "NewsArticleInstrument",
    "NewsSource",
    "Notification",
    "Portfolio",
    "ProviderConfig",
    "RefreshToken",
    "Role",
    "SavedScreen",
    "Shareholding",
    "Transaction",
    "User",
    "Watchlist",
    "WatchlistItem",
]
