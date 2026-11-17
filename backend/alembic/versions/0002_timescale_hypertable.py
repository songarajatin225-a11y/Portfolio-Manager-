"""Convert candles to a TimescaleDB hypertable when the extension is available.

Managed Postgres services without TimescaleDB (e.g. Render) skip this step and keep a plain
table with a composite primary key, which works fine at moderate data volumes.

Revision ID: 0002
Revises: 0001
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    available = bind.execute(
        sa.text("SELECT 1 FROM pg_available_extensions WHERE name = 'timescaledb'")
    ).scalar()
    if not available:
        return
    op.execute("CREATE EXTENSION IF NOT EXISTS timescaledb")
    op.execute(
        "SELECT create_hypertable('candles', by_range('ts', INTERVAL '30 days'), "
        "if_not_exists => TRUE, migrate_data => TRUE)"
    )


def downgrade() -> None:
    # Hypertables cannot be converted back in place; leaving as-is is safe.
    pass
