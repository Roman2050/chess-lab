"""add analysis_status lifecycle columns to games

Revision ID: c4a1f2b3d5e6
Revises: b7c1d9e2f3a4
Create Date: 2026-07-25 18:20:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'c4a1f2b3d5e6'
down_revision: Union[str, Sequence[str], None] = 'b7c1d9e2f3a4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    # server_default keeps the columns NOT NULL for rows that already exist,
    # before the backfill below assigns their real status.
    op.add_column(
        'games',
        sa.Column('analysis_status', sa.String(), server_default='pending', nullable=False),
    )
    op.add_column('games', sa.Column('analysis_started_at', sa.DateTime(), nullable=True))
    op.add_column('games', sa.Column('analysis_error', sa.Text(), nullable=True))
    op.add_column(
        'games',
        sa.Column('analysis_attempts', sa.Integer(), server_default='0', nullable=False),
    )

    # is_analyzed predates the status column and was nullable with no default;
    # the "is_analyzed <=> completed" invariant only holds on a NOT NULL column.
    op.execute("UPDATE games SET is_analyzed = false WHERE is_analyzed IS NULL")
    op.alter_column(
        'games',
        'is_analyzed',
        existing_type=sa.Boolean(),
        server_default=sa.text('false'),
        nullable=False,
    )

    op.execute(
        """
        UPDATE games
        SET analysis_status = CASE WHEN is_analyzed THEN 'completed' ELSE 'pending' END
        """
    )

    op.create_index(
        'ix_games_pending_analysis',
        'games',
        ['id'],
        unique=False,
        postgresql_where=sa.text("analysis_status IN ('pending', 'failed')"),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('ix_games_pending_analysis', table_name='games')
    op.alter_column(
        'games',
        'is_analyzed',
        existing_type=sa.Boolean(),
        server_default=None,
        nullable=True,
    )
    op.drop_column('games', 'analysis_attempts')
    op.drop_column('games', 'analysis_error')
    op.drop_column('games', 'analysis_started_at')
    op.drop_column('games', 'analysis_status')
