"""HLLV support

Revision ID: 7f1f9fe3b92f
Revises: 75b03de0a5b3
Create Date: 2026-05-28 18:12:17.955761

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "7f1f9fe3b92f"
down_revision: str | None = "75b03de0a5b3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


game = postgresql.ENUM("HLL", "HLLV", name="game")


def upgrade() -> None:
    game.create(op.get_bind())
    op.alter_column("players", "eos_id", new_column_name="hll_eos_id")
    op.add_column("players", sa.Column("hllv_eos_id", sa.String(), nullable=True))
    op.add_column(
        "reports",
        sa.Column(
            "game",
            game,
            server_default="HLL",
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("reports", "game")
    op.drop_column("players", "hllv_eos_id")
    op.alter_column("players", "hll_eos_id", new_column_name="eos_id")
    game.drop(op.get_bind())
