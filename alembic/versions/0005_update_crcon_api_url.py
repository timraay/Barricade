"""Update CRCON API URL

Revision ID: af4926f7d139
Revises: 027b31d7016d
Create Date: 2024-09-12 13:26:04.400228

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'af4926f7d139'
down_revision: Union[str, None] = '027b31d7016d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Strip "/api" from CRCON API URLs
    op.execute("""
        UPDATE integrations SET
            api_url = SUBSTRING(api_url, 1, LENGTH(api_url) - 4)
        WHERE
            integration_type = 'COMMUNITY_RCON'
            AND api_url ILIKE '%/api'
    """)


def downgrade() -> None:
    # Add back "/api" from CRCON API URLs
    op.execute("""
        UPDATE integrations SET
            api_url = api_url || '/api'
        WHERE
            integration_type = 'COMMUNITY_RCON'
    """)
