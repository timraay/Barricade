"""Add platform filter

Revision ID: a075bdbccd7e
Revises: b2cfcc4ae6a1
Create Date: 2026-07-09 18:28:55.580844

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a075bdbccd7e"
down_revision: str | None = "b2cfcc4ae6a1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "communities", sa.Column("hll_platform_filter", sa.Integer(), nullable=True)
    )
    op.add_column(
        "communities", sa.Column("hllv_platform_filter", sa.Integer(), nullable=True)
    )

    # Migrate server_type to platforms_bitflag
    op.add_column(
        "reports", sa.Column("platforms_bitflag", sa.Integer(), nullable=True)
    )
    op.execute(
        """
        UPDATE reports
        SET platforms_bitflag = CASE server_type
            WHEN 'PC' THEN 1
            WHEN 'CONSOLE' THEN 2
            WHEN 'CROSSPLAY' THEN 3
        END
        """
    )
    op.alter_column("reports", "platforms_bitflag", nullable=False)
    op.drop_column("reports", "server_type")


def downgrade() -> None:
    # Migrate platforms_bitflag back to server_type
    op.add_column(
        "reports",
        sa.Column(
            "server_type",
            postgresql.ENUM("PC", "CONSOLE", "CROSSPLAY", name="platform"),
            autoincrement=False,
            nullable=True,
        ),
    )
    op.execute(
        """
        UPDATE reports
        SET server_type = CASE platforms_bitflag
            WHEN 1 THEN 'PC'::platform
            WHEN 2 THEN 'CONSOLE'::platform
            ELSE 'CROSSPLAY'::platform
        END
        """
    )
    op.alter_column("reports", "server_type", nullable=False)
    op.drop_column("reports", "platforms_bitflag")

    # Drop the platform filter columns from communities
    op.drop_column("communities", "hllv_platform_filter")
    op.drop_column("communities", "hll_platform_filter")
