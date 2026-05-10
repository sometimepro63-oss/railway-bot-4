from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "0004_buyer_email"
down_revision = "0003_payurl"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("payments", sa.Column("buyer_email", sa.String(length=320), nullable=True))
    op.create_index("ix_payments_buyer_email", "payments", ["buyer_email"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_payments_buyer_email", table_name="payments")
    op.drop_column("payments", "buyer_email")

