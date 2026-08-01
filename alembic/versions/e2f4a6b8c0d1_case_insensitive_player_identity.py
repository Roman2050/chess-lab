"""Add case-insensitive player identity indexes.

Revision ID: e2f4a6b8c0d1
Revises: d8f2e3a4b5c6
Create Date: 2026-08-01 12:00:00.000000

"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "e2f4a6b8c0d1"
down_revision: str | Sequence[str] | None = "d8f2e3a4b5c6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add case-insensitive game lookups and the logical report key."""
    bind = op.get_bind()
    duplicate_groups = [
        tuple(row)
        for row in bind.execute(
            sa.text(
                """
                SELECT lower(player_name) AS lower_name,
                       language,
                       count(*) AS duplicate_count
                FROM player_reports
                GROUP BY lower(player_name), language
                HAVING count(*) > 1
                ORDER BY lower(player_name), language
                """
            )
        )
    ]
    if duplicate_groups:
        raise RuntimeError(
            "Cannot migrate player_reports to a case-insensitive player key. "
            "Conflicting (lower_name, language, count) groups: "
            f"{duplicate_groups}. Resolve them manually while workers are stopped, "
            "then retry the migration."
        )

    op.create_index(
        "ix_games_white_player_lower",
        "games",
        [sa.text("lower(white_player)")],
        unique=False,
    )
    op.create_index(
        "ix_games_black_player_lower",
        "games",
        [sa.text("lower(black_player)")],
        unique=False,
    )
    op.drop_constraint(
        "uq_player_reports_player_lang",
        "player_reports",
        type_="unique",
    )
    op.create_index(
        "uq_player_reports_player_lang_lower",
        "player_reports",
        [sa.text("lower(player_name)"), "language"],
        unique=True,
    )


def downgrade() -> None:
    """Restore the exact-case report constraint and remove functional indexes."""
    op.drop_index(
        "uq_player_reports_player_lang_lower",
        table_name="player_reports",
    )
    op.create_unique_constraint(
        "uq_player_reports_player_lang",
        "player_reports",
        ["player_name", "language"],
    )
    op.drop_index("ix_games_black_player_lower", table_name="games")
    op.drop_index("ix_games_white_player_lower", table_name="games")
