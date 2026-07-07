"""Track edit timestamps

Revision ID: 85aac3506a89
Revises: 8dba132a8e7e
Create Date: 2026-07-07 00:28:29.578153

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "85aac3506a89"
down_revision: str | None = "8dba132a8e7e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "player_report_responses",
        sa.Column("responded_at", sa.TIMESTAMP(timezone=True), nullable=True),
    )
    op.add_column(
        "reports", sa.Column("edited_at", sa.TIMESTAMP(timezone=True), nullable=True)
    )
    op.add_column("reports", sa.Column("edited_by", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("reports", "edited_by")
    op.drop_column("reports", "edited_at")
    op.drop_column("player_report_responses", "responded_at")
