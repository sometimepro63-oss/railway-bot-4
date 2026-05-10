from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0001_init"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE TYPE payment_status AS ENUM ('created','pending','paid','failed','cancelled')")
    op.execute("CREATE TYPE subscription_status AS ENUM ('active','expired','cancelled')")

    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("telegram_id", sa.BigInteger(), nullable=False),
        sa.Column("username", sa.String(length=64), nullable=True),
        sa.Column("first_name", sa.String(length=128), nullable=True),
        sa.Column("last_name", sa.String(length=128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("telegram_id", name="uq_users_telegram_id"),
    )
    op.create_index("ix_users_telegram_id", "users", ["telegram_id"], unique=True)

    op.create_table(
        "payments",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("telegram_id", sa.BigInteger(), nullable=False),
        sa.Column("order_id", sa.String(length=64), nullable=False),
        sa.Column("prodamus_payment_id", sa.String(length=64), nullable=True),
        sa.Column("amount", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(length=8), server_default="rub", nullable=False),
        sa.Column(
            "status",
            sa.Enum(name="payment_status"),
            server_default="created",
            nullable=False,
        ),
        sa.Column("raw_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("paid_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("order_id", name="uq_payments_order_id"),
    )
    op.create_index("ix_payments_order_id", "payments", ["order_id"], unique=True)
    op.create_index("ix_payments_telegram_id", "payments", ["telegram_id"], unique=False)
    op.create_index(
        "ix_payments_telegram_id_created_at",
        "payments",
        ["telegram_id", "created_at"],
        unique=False,
    )

    op.create_table(
        "subscriptions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("telegram_id", sa.BigInteger(), nullable=False),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "status",
            sa.Enum(name="subscription_status"),
            server_default="active",
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.UniqueConstraint("telegram_id", name="uq_subscriptions_telegram_id"),
    )
    op.create_index("ix_subscriptions_telegram_id", "subscriptions", ["telegram_id"], unique=True)

    op.create_table(
        "invite_links",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("telegram_id", sa.BigInteger(), nullable=False),
        sa.Column("invite_link", sa.Text(), nullable=False),
        sa.Column("expire_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_invite_links_telegram_id", "invite_links", ["telegram_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_invite_links_telegram_id", table_name="invite_links")
    op.drop_table("invite_links")

    op.drop_index("ix_subscriptions_telegram_id", table_name="subscriptions")
    op.drop_table("subscriptions")

    op.drop_index("ix_payments_telegram_id_created_at", table_name="payments")
    op.drop_index("ix_payments_telegram_id", table_name="payments")
    op.drop_index("ix_payments_order_id", table_name="payments")
    op.drop_table("payments")

    op.drop_index("ix_users_telegram_id", table_name="users")
    op.drop_table("users")

    op.execute("DROP TYPE subscription_status")
    op.execute("DROP TYPE payment_status")

