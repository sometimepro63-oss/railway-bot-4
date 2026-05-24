from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0006_last_start_at"
down_revision = "0005_reminder_sent_at"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("last_start_at", sa.DateTime(timezone=True), nullable=True))
    op.execute(sa.text("UPDATE users SET last_start_at = created_at WHERE last_start_at IS NULL"))
    op.create_index("ix_users_last_start_at", "users", ["last_start_at"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_users_last_start_at", table_name="users")
    op.drop_column("users", "last_start_at")
