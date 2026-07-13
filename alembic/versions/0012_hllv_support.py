"""HLLV support

Revision ID: 8dba132a8e7e
Revises: 75b03de0a5b3
Create Date: 2026-07-06 12:58:29.678800

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "8dba132a8e7e"
down_revision: str | None = "75b03de0a5b3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

Game = postgresql.ENUM("HLL", "HLLV", name="game")
PlayerPlatform = postgresql.ENUM(
    "STEAM", "EPIC", "XBOX", "PLAYSTATION", name="playerplatform"
)


def upgrade() -> None:
    # Create game enum type
    Game.create(op.get_bind())
    # Create playerplatform enum type
    PlayerPlatform.create(op.get_bind())
    # Add "CROSSPLAY" member to platform enum type
    op.execute(sa.text("ALTER TYPE platform ADD VALUE IF NOT EXISTS 'CROSSPLAY'"))

    # Add game column to player_bans and reports
    op.add_column(
        "player_bans",
        sa.Column(
            "game",
            Game,
            server_default="HLL",
            nullable=False,
        ),
    )
    op.add_column(
        "reports",
        sa.Column(
            "game",
            Game,
            server_default="HLL",
            nullable=False,
        ),
    )

    # Split banlist IDs
    op.alter_column("integrations", "banlist_id", new_column_name="hll_banlist_id")
    op.add_column(
        "integrations", sa.Column("hllv_banlist_id", sa.String(), nullable=True)
    )

    # Split eos IDs
    op.alter_column("players", "eos_id", new_column_name="hll_eos_id")
    op.add_column("players", sa.Column("hllv_eos_id", sa.String(), nullable=True))

    # Add platform column to players
    op.add_column(
        "players",
        sa.Column(
            "platform",
            PlayerPlatform,
            nullable=True,
        ),
    )

    # Migrate report_tokens.platform to reports.server_type
    op.add_column(
        "reports",
        sa.Column(
            "server_type",
            sa.Enum("PC", "CONSOLE", "CROSSPLAY", name="platform"),
            nullable=True,
        ),
    )
    op.execute(
        sa.text(
            """
            UPDATE reports AS r
            SET server_type = rt.platform
            FROM report_tokens AS rt
            WHERE r.id = rt.id AND rt.platform IS NOT NULL
            """
        )
    )
    op.alter_column("reports", "server_type", nullable=False)
    op.drop_column("report_tokens", "platform")


def downgrade() -> None:
    # Migrate reports.server_type to report_tokens.platform
    op.add_column(
        "report_tokens",
        sa.Column(
            "platform",
            postgresql.ENUM("PC", "CONSOLE", name="platform"),
            server_default=sa.text("'PC'::platform"),
            autoincrement=False,
            nullable=False,
        ),
    )
    op.execute(
        sa.text(
            """
            UPDATE report_tokens AS rt
            SET platform = (
                CASE
                    WHEN r.server_type = 'CROSSPLAY'
                    THEN 'PC'
                    ELSE r.server_type
                END
            )::platform
            FROM reports AS r
            WHERE r.id = rt.id AND r.server_type IS NOT NULL
            """
        )
    )
    op.drop_column("reports", "server_type")

    # Drop platform column from players
    op.drop_column("players", "platform")

    # Drop HLLV EOS ID
    op.alter_column("players", "hll_eos_id", new_column_name="eos_id")
    op.drop_column("players", "hllv_eos_id")

    # Drop HLLV banlist ID
    op.alter_column("integrations", "hll_banlist_id", new_column_name="banlist_id")
    op.drop_column("integrations", "hllv_banlist_id")

    # Remove game column from player_bans and reports
    op.drop_column("player_bans", "game")
    op.drop_column("reports", "game")

    # Remove playerplatform enum type
    PlayerPlatform.drop(op.get_bind())
    # Remove game enum type
    Game.drop(op.get_bind())
