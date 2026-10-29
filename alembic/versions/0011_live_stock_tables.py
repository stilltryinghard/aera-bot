"""Freeze additive tables already present in the live stock portal.

Check-first guards adopt existing production tables while new databases receive
the same schema. No customer or financial rows are migrated.
"""
from alembic import op
import sqlalchemy as sa

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade():
    if not sa.inspect(op.get_bind()).has_table('device_subscriptions'):
        op.create_table('device_subscriptions',
        sa.Column('parent_uuid', sa.String(length=36), nullable=False),
        sa.Column('public_uuid', sa.String(length=36), nullable=False),
        sa.PrimaryKeyConstraint('parent_uuid'),
        sa.UniqueConstraint('public_uuid')
        )
    if not sa.inspect(op.get_bind()).has_table('affiliate_withdrawals'):
        op.create_table('affiliate_withdrawals',
        sa.Column('user_id', sa.String(length=36), nullable=False),
        sa.Column('amount_minor', sa.Integer(), nullable=False),
        sa.Column('details_encrypted', sa.Text(), nullable=False),
        sa.Column('status', sa.String(length=32), nullable=False),
        sa.Column('decided_by', sa.BigInteger(), nullable=True),
        sa.Column('decided_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('receipt_reference', sa.String(length=128), nullable=True),
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint('amount_minor >= 100000'),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
        sa.PrimaryKeyConstraint('id')
        )
    if not sa.inspect(op.get_bind()).has_table('family_access'):
        op.create_table('family_access',
        sa.Column('user_id', sa.String(length=36), nullable=False),
        sa.Column('device_limit', sa.Integer(), nullable=False),
        sa.Column('is_active', sa.Boolean(), nullable=False),
        sa.Column('link_encrypted', sa.Text(), nullable=True),
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('user_id')
        )
    if not sa.inspect(op.get_bind()).has_table('access_renewals'):
        op.create_table('access_renewals',
        sa.Column('order_id', sa.String(length=36), nullable=False),
        sa.Column('user_id', sa.String(length=36), nullable=False),
        sa.Column('access_kind', sa.String(length=16), nullable=False),
        sa.Column('access_id', sa.String(length=36), nullable=False),
        sa.Column('duration_days', sa.Integer(), nullable=False),
        sa.Column('status', sa.String(length=24), nullable=False),
        sa.Column('client_uuid', sa.String(length=36), nullable=True),
        sa.Column('panel_email', sa.String(length=128), nullable=True),
        sa.Column('original_expiry_ms', sa.BigInteger(), nullable=True),
        sa.Column('target_expiry_ms', sa.BigInteger(), nullable=True),
        sa.Column('fingerprint', sa.JSON(), nullable=True),
        sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['order_id'], ['sale_orders.id'], ),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('order_id')
        )
    if not sa.inspect(op.get_bind()).has_table('affiliate_commissions'):
        op.create_table('affiliate_commissions',
        sa.Column('referred_user_id', sa.String(length=36), nullable=False),
        sa.Column('referrer_user_id', sa.String(length=36), nullable=False),
        sa.Column('order_id', sa.String(length=36), nullable=False),
        sa.Column('purchase_minor', sa.Integer(), nullable=False),
        sa.Column('amount_minor', sa.Integer(), nullable=False),
        sa.Column('status', sa.String(length=32), nullable=False),
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint('amount_minor >= 0 AND purchase_minor > 0'),
        sa.ForeignKeyConstraint(['order_id'], ['sale_orders.id'], ),
        sa.ForeignKeyConstraint(['referred_user_id'], ['users.id'], ),
        sa.ForeignKeyConstraint(['referrer_user_id'], ['users.id'], ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('order_id'),
        sa.UniqueConstraint('referred_user_id')
        )
    if not sa.inspect(op.get_bind()).has_table('referral_solo_rewards'):
        op.create_table('referral_solo_rewards',
        sa.Column('user_id', sa.String(length=36), nullable=False),
        sa.Column('referral_ids', sa.JSON(), nullable=False),
        sa.Column('order_id', sa.String(length=36), nullable=True),
        sa.Column('status', sa.String(length=32), nullable=False),
        sa.Column('issued_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['order_id'], ['sale_orders.id'], ),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('order_id'),
        sa.UniqueConstraint('user_id')
        )
    if not sa.inspect(op.get_bind()).has_table('device_bindings'):
        op.create_table('device_bindings',
        sa.Column('user_id', sa.String(length=36), nullable=False),
        sa.Column('paid_link_id', sa.String(length=36), nullable=True),
        sa.Column('trial_link_id', sa.String(length=36), nullable=True),
        sa.Column('parent_uuid', sa.String(length=36), nullable=False),
        sa.Column('device_hash', sa.String(length=64), nullable=False),
        sa.Column('client_uuid', sa.String(length=36), nullable=False),
        sa.Column('label', sa.String(length=128), nullable=False),
        sa.Column('platform', sa.String(length=32), nullable=False),
        sa.Column('device_model', sa.String(length=64), nullable=False),
        sa.Column('link_encrypted', sa.Text(), nullable=False),
        sa.Column('bundle_encrypted', sa.Text(), nullable=True),
        sa.Column('status', sa.String(length=32), nullable=False),
        sa.Column('duration_ms', sa.BigInteger(), nullable=False),
        sa.Column('issued_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('expires_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('checked_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('id', sa.String(length=36), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(['paid_link_id'], ['paid_links.id'], ),
        sa.ForeignKeyConstraint(['trial_link_id'], ['trial_links.id'], ),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('client_uuid'),
        sa.UniqueConstraint('parent_uuid', 'device_hash')
        )


def downgrade():
    op.drop_table('device_bindings')
    op.drop_table('referral_solo_rewards')
    op.drop_table('affiliate_commissions')
    op.drop_table('access_renewals')
    op.drop_table('family_access')
    op.drop_table('affiliate_withdrawals')
    op.drop_table('device_subscriptions')
