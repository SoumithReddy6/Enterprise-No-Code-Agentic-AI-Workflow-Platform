"""Record why a run counts as truncated

Revision ID: 0002_truncation_source
Revises: 0001_baseline
Create Date: 2026-09-29

Loops checkpointed before the loop summary existed can have unprovable completeness,
and such runs now count as truncated. Operator metrics read only denormalized columns,
never the run payload, so the distinction between a confirmed cause and unverifiable
legacy completeness needs its own column. Every run truncated before this revision was
truncated for a confirmed cause, so existing rows are backfilled accordingly.
"""
from alembic import op
import sqlalchemy as sa


revision = '0002_truncation_source'
down_revision = '0001_baseline'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('runs', schema=None) as batch_op:
        batch_op.add_column(sa.Column('truncation_source', sa.String(length=40), nullable=False, server_default=''))
    runs = sa.table('runs', sa.column('truncated', sa.Boolean), sa.column('truncation_source', sa.String))
    op.execute(runs.update().where(runs.c.truncated.is_(True)).values(truncation_source='confirmed'))


def downgrade():
    with op.batch_alter_table('runs', schema=None) as batch_op:
        batch_op.drop_column('truncation_source')
