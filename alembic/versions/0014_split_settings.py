"""Split settings

Revision ID: b2cfcc4ae6a1
Revises: 85aac3506a89
Create Date: 2026-07-07 21:25:14.315368

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b2cfcc4ae6a1"
down_revision: str | None = "85aac3506a89"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # fmt: off
    op.alter_column('communities', 'forward_guild_id', new_column_name='guild_id')
    op.alter_column('communities', 'forward_channel_id', new_column_name='hll_reports_channel_id')
    op.alter_column('communities', 'alerts_channel_id', new_column_name='hll_alerts_channel_id')
    op.alter_column('communities', 'confirmations_channel_id', new_column_name='hll_confirmations_channel_id')
    op.alter_column('communities', 'admin_role_id', new_column_name='hll_admin_role_id')
    op.alter_column('communities', 'alerts_role_id', new_column_name='hll_alerts_role_id')
    op.alter_column('communities', 'reasons_filter', new_column_name='hll_reason_filter')
    op.add_column('communities', sa.Column('hllv_reports_channel_id', sa.BigInteger(), nullable=True))
    op.add_column('communities', sa.Column('hllv_alerts_channel_id', sa.BigInteger(), nullable=True))
    op.add_column('communities', sa.Column('hllv_confirmations_channel_id', sa.BigInteger(), nullable=True))
    op.add_column('communities', sa.Column('hllv_admin_role_id', sa.BigInteger(), nullable=True))
    op.add_column('communities', sa.Column('hllv_alerts_role_id', sa.BigInteger(), nullable=True))
    op.add_column('communities', sa.Column('hllv_reason_filter', sa.Integer(), nullable=True))
    op.drop_index(op.f('ix_communities_forward_guild_id'), table_name='communities')
    op.create_index(op.f('ix_communities_guild_id'), 'communities', ['guild_id'], unique=True)
    # fmt: on


def downgrade() -> None:
    # fmt: off
    op.drop_column('communities', 'hllv_reason_filter')
    op.drop_column('communities', 'hllv_alerts_role_id')
    op.drop_column('communities', 'hllv_admin_role_id')
    op.drop_column('communities', 'hllv_confirmations_channel_id')
    op.drop_column('communities', 'hllv_alerts_channel_id')
    op.drop_column('communities', 'hllv_reports_channel_id')
    op.alter_column('communities', 'guild_id', new_column_name='forward_guild_id')
    op.alter_column('communities', 'hll_reports_channel_id', new_column_name='forward_channel_id')
    op.alter_column('communities', 'hll_alerts_channel_id', new_column_name='alerts_channel_id')
    op.alter_column('communities', 'hll_confirmations_channel_id', new_column_name='confirmations_channel_id')
    op.alter_column('communities', 'hll_admin_role_id', new_column_name='admin_role_id')
    op.alter_column('communities', 'hll_alerts_role_id', new_column_name='alerts_role_id')
    op.alter_column('communities', 'hll_reason_filter', new_column_name='reasons_filter')
    op.drop_index(op.f('ix_communities_guild_id'), table_name='communities')
    op.create_index(op.f('ix_communities_forward_guild_id'), 'communities', ['forward_guild_id'], unique=True)
    # fmt: on
