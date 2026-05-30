from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0007_broadcast_fields"
down_revision = "0006_last_start_at"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("broadcast_key", sa.String(length=64), nullable=True))
    op.add_column("users", sa.Column("broadcast_sent_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_users_broadcast_key", "users", ["broadcast_key"], unique=False)
    op.create_index("ix_users_broadcast_sent_at", "users", ["broadcast_sent_at"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_users_broadcast_sent_at", table_name="users")
    op.drop_index("ix_users_broadcast_key", table_name="users")
    op.drop_column("users", "broadcast_sent_at")
    op.drop_column("users", "broadcast_key")

