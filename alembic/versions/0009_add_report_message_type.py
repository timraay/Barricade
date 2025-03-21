"""Add report message type

Revision ID: e2c683f033d2
Revises: cae0c0791b80
Create Date: 2025-03-21 20:57:47.172152

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'e2c683f033d2'
down_revision: Union[str, None] = 'cae0c0791b80'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

message_type = postgresql.ENUM('PUBLIC', 'MANAGE', 'REVIEW', 'T17_SUPPORT', name='reportmessagetype')

def upgrade() -> None:
    # Create new enum
    message_type.create(op.get_bind())

    # Add ReportMessage.message_type column
    op.add_column('report_messages', sa.Column('message_type', sa.Enum('PUBLIC', 'MANAGE', 'REVIEW', 'T17_SUPPORT', name='reportmessagetype'), server_default='REVIEW', nullable=False))

    # Drop primary key
    op.drop_constraint('report_messages_pkey', table_name='report_messages')
    # Drop index on ReportMessage.message_id since it will become the new primary key
    op.drop_index('ix_report_messages_message_id', table_name='report_messages')

    # Make ReportMessage.community_id nullable
    op.alter_column('report_messages', 'community_id', existing_type=sa.INTEGER(), nullable=True)

    # Add new primary key on ReportMessage.message_id
    op.create_primary_key('report_messages_pkey', 'report_messages', ['message_id'])
    # Add unique constraint on columns that were previously primary key, using an index
    op.create_index('ix_report_messages_report_id_community_id', 'report_messages', ['report_id', 'community_id'], unique=True)

    # Update data to have the right message type
    op.execute("""
        UPDATE report_messages rm
        SET message_type = 'MANAGE'
        FROM report_tokens t
        WHERE rm.report_id = t.id AND rm.community_id = t.community_id;
    """)

def downgrade() -> None:
    op.drop_column('report_messages', 'message_type')
    message_type.drop(op.get_bind())

    op.drop_constraint('report_messages_pkey', table_name='report_messages')
    op.drop_index('ix_report_messages_report_id_community_id', table_name='report_messages')

    op.alter_column('report_messages', 'community_id', existing_type=sa.INTEGER(), nullable=False)

    op.create_primary_key('report_messages_pkey', 'report_messages', ['report_id', 'community_id'])
    op.create_index('ix_report_messages_message_id', 'report_messages', ['message_id'], unique=True)

