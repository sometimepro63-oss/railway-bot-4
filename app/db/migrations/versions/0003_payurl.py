from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0003_payurl"
down_revision = "0002_lifetime"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("payments", sa.Column("payment_url", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("payments", "payment_url")

