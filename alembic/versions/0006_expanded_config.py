"""Expanded config

Revision ID: 266169fb0d75
Revises: af4926f7d139
Create Date: 2024-09-20 20:36:50.361320

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '266169fb0d75'
down_revision: Union[str, None] = 'af4926f7d139'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ### commands auto generated by Alembic - please adjust! ###
    op.add_column('communities', sa.Column('confirmations_channel_id', sa.BigInteger(), nullable=True))
    op.add_column('communities', sa.Column('alerts_channel_id', sa.BigInteger(), nullable=True))
    op.add_column('communities', sa.Column('alerts_role_id', sa.BigInteger(), nullable=True))
    # ### end Alembic commands ###


def downgrade() -> None:
    # ### commands auto generated by Alembic - please adjust! ###
    op.drop_column('communities', 'alerts_role_id')
    op.drop_column('communities', 'alerts_channel_id')
    op.drop_column('communities', 'confirmations_channel_id')
    # ### end Alembic commands ###