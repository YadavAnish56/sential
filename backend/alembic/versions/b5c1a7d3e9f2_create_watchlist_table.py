"""create watchlist table

Revision ID: b5c1a7d3e9f2
Revises: 8e1deeb6a900
Create Date: 2026-09-11 09:55:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b5c1a7d3e9f2'
down_revision: Union[str, Sequence[str], None] = '8e1deeb6a900'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        'watchlists',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('plate_number', sa.String(length=50), nullable=False),
        sa.Column('description', sa.String(length=255), nullable=True),
        sa.Column('severity', sa.String(length=50), server_default='high', nullable=False),
        sa.Column('is_active', sa.Boolean(), server_default=sa.text('true'), nullable=False),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_watchlists_plate_number'), 'watchlists', ['plate_number'], unique=False)
    op.create_index(
        'uq_watchlist_active_plate',
        'watchlists',
        ['plate_number'],
        unique=True,
        postgresql_where=sa.text('is_active = true'),
        sqlite_where=sa.text('is_active = 1'),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index('uq_watchlist_active_plate', table_name='watchlists')
    op.drop_index(op.f('ix_watchlists_plate_number'), table_name='watchlists')
    op.drop_table('watchlists')
