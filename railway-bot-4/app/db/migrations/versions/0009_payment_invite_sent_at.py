"""add invite_sent_at to payments

Revision ID: 0009_payment_invite_sent_at
Revises: 0008_broadcast_templates
Create Date: 2026-09-04 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "0009_payment_invite_sent_at"
down_revision = "0008_broadcast_templates"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("payments", sa.Column("invite_sent_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("payments", "invite_sent_at")

