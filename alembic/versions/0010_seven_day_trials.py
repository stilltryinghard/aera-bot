"""Recheck free inventory against the new seven-day first-use policy."""

from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade():
    # Previously verified FREE keys may still have a two-day panel duration.
    # Keep their generation/history, but prevent claims until a fresh GET
    # confirms the seven-day setting supplied by the owner.
    op.execute("""UPDATE trial_links SET status = 'UNVERIFIED', checked_at = NULL
        WHERE status = 'FREE' AND user_id IS NULL AND pending_uuid IS NULL""")


def downgrade():
    # Reverification is safe in either version; do not invent a FREE status.
    pass
