"""Passkey credentials and single-use, browser-bound challenges."""
from alembic import op
import sqlalchemy as sa

revision = "a19b48d207f1"
down_revision = "c83f7a19d2e4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table("passkeys",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("credential_id", sa.String(1400), nullable=False, unique=True),
        sa.Column("public_key", sa.Text(), nullable=False),
        sa.Column("user_handle", sa.String(128), nullable=False),
        sa.Column("rp_id", sa.String(253), nullable=False),
        sa.Column("name", sa.String(80), nullable=False),
        sa.Column("sign_count", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_passkeys_user_id", "passkeys", ["user_id"])
    op.create_table("passkey_challenges",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("challenge", sa.String(128), nullable=False),
        sa.Column("purpose", sa.String(16), nullable=False),
        sa.Column("origin", sa.String(512), nullable=False),
        sa.Column("rp_id", sa.String(253), nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("session_hash", sa.String(64), nullable=False),
        sa.Column("user_handle", sa.String(128), nullable=False),
        sa.Column("name", sa.String(80), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False))
    op.create_index("ix_passkey_challenges_expires_at", "passkey_challenges", ["expires_at"])


def downgrade() -> None:
    op.drop_table("passkey_challenges")
    op.drop_table("passkeys")
