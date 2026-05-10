from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0002_lifetime"
down_revision = "0001_init"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column("subscriptions", "expires_at", existing_type=sa.DateTime(timezone=True), nullable=True)


def downgrade() -> None:
    op.alter_column("subscriptions", "expires_at", existing_type=sa.DateTime(timezone=True), nullable=False)

