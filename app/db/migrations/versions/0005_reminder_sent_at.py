from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0005_reminder_sent_at"
down_revision = "0004_buyer_email"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("reminder_sent_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_users_reminder_sent_at", "users", ["reminder_sent_at"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_users_reminder_sent_at", table_name="users")
    op.drop_column("users", "reminder_sent_at")
