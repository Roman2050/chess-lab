"""rehash custom PGN unique_id

Revision ID: d8f2e3a4b5c6
Revises: c4a1f2b3d5e6
Create Date: 2026-07-31 18:30:00.000000

"""
from datetime import date, datetime
import hashlib
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.sql import text

# revision identifiers, used by Alembic.
revision: str = 'd8f2e3a4b5c6'
down_revision: Union[str, Sequence[str], None] = 'c4a1f2b3d5e6'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

STARTPOS_FEN = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"


def upgrade() -> None:
    """Upgrade schema by rehashing custom PGN unique_ids."""
    bind = op.get_bind()

    result = bind.execute(
        text(
            "SELECT id, unique_id, white_player, black_player, result, date_played, pgn_content "
            "FROM games"
        )
    )
    rows = result.fetchall()

    for row in rows:
        game_id, unique_id, white_player, black_player, res, date_played, pgn_content = row

        if date_played is None:
            # If candidate hex hash is present on a row with NULL date_played,
            # the raw Date tag is unrecoverable. Raise explicit error per spec.
            if len(unique_id) == 64:
                try:
                    int(unique_id, 16)
                    raise RuntimeError(
                        f"Game id={game_id} has NULL date_played; migration applied too late "
                        "after tolerant date parsing was enabled."
                    )
                except ValueError:
                    pass
            continue

        if isinstance(date_played, (date, datetime)):
            date_str = date_played.strftime("%Y.%m.%d")
        else:
            date_str = str(date_played).replace("-", ".")

        w_name = white_player or ""
        b_name = black_player or ""

        # Verify exact match against old contract: sha256("WhiteBlackDateSTARTPOS_FEN")
        old_raw = f"{w_name}{b_name}{date_str}{STARTPOS_FEN}"
        expected_old_hash = hashlib.sha256(old_raw.encode("utf-8")).hexdigest()

        if unique_id == expected_old_hash:
            # Rehash under new contract: sha256("White|Black|Date|Result|clean_pgn")
            new_raw = "|".join([
                w_name,
                b_name,
                date_str,
                res or "",
                pgn_content or "",
            ])
            new_hash = hashlib.sha256(new_raw.encode("utf-8")).hexdigest()

            bind.execute(
                text("UPDATE games SET unique_id = :new_hash WHERE id = :game_id"),
                {"new_hash": new_hash, "game_id": game_id},
            )


def downgrade() -> None:
    """Downgrade schema."""
    raise NotImplementedError(
        "Reverting unique_id rehashing for custom PGNs is not supported because "
        "re-introducing the old contract would trigger UNIQUE constraint collisions."
    )
