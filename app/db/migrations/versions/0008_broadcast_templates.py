"""add broadcast templates

Revision ID: 0008_broadcast_templates
Revises: 0007_broadcast_fields
Create Date: 2026-07-15 00:00:00.000000
"""

from alembic import op
import sqlalchemy as sa


revision = "0008_broadcast_templates"
down_revision = "0007_broadcast_fields"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "broadcast_templates",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("key", sa.String(length=64), nullable=False),
        sa.Column("text", sa.Text(), nullable=True),
        sa.Column("photo_file_id", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_broadcast_templates_key", "broadcast_templates", ["key"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_broadcast_templates_key", table_name="broadcast_templates")
    op.drop_table("broadcast_templates")
