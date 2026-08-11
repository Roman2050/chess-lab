"""add_player_reports

Revision ID: b7c1d9e2f3a4
Revises: e15ebabb6f14
Create Date: 2026-06-26 19:54:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'b7c1d9e2f3a4'
down_revision: Union[str, Sequence[str], None] = 'e15ebabb6f14'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table('player_reports',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('player_name', sa.String(), nullable=False),
    sa.Column('language', sa.String(), server_default='en', nullable=False),
    sa.Column('report_text', sa.Text(), nullable=True),
    sa.Column('analyzed_games_count', sa.Integer(), server_default='0', nullable=False),
    sa.Column('last_game_played_at', sa.DateTime(), nullable=True),
    sa.Column('status', sa.String(), server_default='ready', nullable=False),
    sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('player_name', 'language', name='uq_player_reports_player_lang')
    )
    op.create_index(op.f('ix_player_reports_id'), 'player_reports', ['id'], unique=False)
    op.create_index(op.f('ix_player_reports_player_name'), 'player_reports', ['player_name'], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f('ix_player_reports_player_name'), table_name='player_reports')
    op.drop_index(op.f('ix_player_reports_id'), table_name='player_reports')
    op.drop_table('player_reports')
