"""external_puzzles (Fase 6 — modalità puzzle, dataset Lichess)

Revision ID: c41e8d5a2f90
Revises: 927361b0445b
Create Date: 2026-07-18 15:10:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c41e8d5a2f90'
down_revision: Union[str, Sequence[str], None] = '927361b0445b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Tabella nuova (nessun ALTER: batch mode non necessario qui). Nessuna FK
    # verso games: i puzzle esterni non appartengono a nessuna partita.
    op.create_table('external_puzzles',
    sa.Column('id', sa.String(length=8), nullable=False),
    sa.Column('fen', sa.Text(), nullable=False),
    sa.Column('initial_uci', sa.String(length=6), nullable=False),
    sa.Column('moves_uci', sa.Text(), nullable=False),
    sa.Column('rating', sa.Integer(), nullable=False),
    sa.Column('themes', sa.Text(), nullable=False),
    sa.Column('lichess_url', sa.Text(), nullable=True),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_external_puzzles_rating'), 'external_puzzles', ['rating'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_external_puzzles_rating'), table_name='external_puzzles')
    op.drop_table('external_puzzles')
