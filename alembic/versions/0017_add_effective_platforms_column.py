"""Add effective platforms column

Revision ID: bc583d539b05
Revises: 56f3f2e67805
Create Date: 2026-07-16 20:23:03.796799

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "bc583d539b05"
down_revision: str | None = "56f3f2e67805"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "reports",
        sa.Column("effective_platforms_bitflag", sa.Integer(), nullable=True),
    )
    op.execute(
        """
        UPDATE reports
        SET effective_platforms_bitflag = platforms_bitflag
        """
    )
    op.alter_column(
        "reports",
        "effective_platforms_bitflag",
        existing_type=sa.Integer(),
        nullable=False,
    )


def downgrade() -> None:
    op.drop_column("reports", "effective_platforms_bitflag")
