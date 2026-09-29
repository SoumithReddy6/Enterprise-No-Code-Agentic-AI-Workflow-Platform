"""Store loop item progress as rows and run accounting as its own column

Revision ID: 0003_loop_storage
Revises: 0002_truncation_source
Create Date: 2026-09-29

Every event and every action reservation rewrote the whole run document. Loop progress
lived in that document, so an n-item batch rewrote O(n^2) bytes; accounting did too, so
each reservation also rewrote every checkpoint. Items become one row each in
run_loop_items, and accounting moves to runs.accounting.

Nothing is copied. Readers merge the document and the new storage - a row wins over
document progress for the same item, and the column over a document accounting copy - so
no startup migration has to rewrite every in-flight run.
"""
from alembic import op
import sqlalchemy as sa


revision = '0003_loop_storage'
down_revision = '0002_truncation_source'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('run_loop_items',
    sa.Column('run_id', sa.String(length=64), nullable=False),
    sa.Column('node_id', sa.String(length=64), nullable=False),
    sa.Column('item_index', sa.Integer(), nullable=False),
    sa.Column('entry', sa.JSON(), nullable=False),
    sa.PrimaryKeyConstraint('run_id', 'node_id', 'item_index')
    )
    with op.batch_alter_table('runs', schema=None) as batch_op:
        batch_op.add_column(sa.Column('accounting', sa.JSON(), nullable=True))


def downgrade():
    with op.batch_alter_table('runs', schema=None) as batch_op:
        batch_op.drop_column('accounting')
    op.drop_table('run_loop_items')
