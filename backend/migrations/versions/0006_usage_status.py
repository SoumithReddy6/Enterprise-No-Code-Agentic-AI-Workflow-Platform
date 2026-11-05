"""Count model calls whose usage is incomplete

Revision ID: 0006_usage_status
Revises: 0005_admission_metrics
Create Date: 2026-11-05

A call that reached a provider is counted even when its usage is missing (TA06). These
columns record how many of a row's calls had one count missing (partial), no usable count
(unreported), or no response after dispatch (unanswered). Rows written before this
revision are not backfilled: they read 0, and their calls count only calls with usage.
A downgrade drops the counters; tokens and calls are kept.
"""
from alembic import op
import sqlalchemy as sa


revision = '0006_usage_status'
down_revision = '0005_admission_metrics'
branch_labels = None
depends_on = None

COLUMNS = ('partial_calls', 'unreported_calls', 'unanswered_calls')


def upgrade():
    with op.batch_alter_table('run_token_usage', schema=None) as batch_op:
        for name in COLUMNS:
            batch_op.add_column(sa.Column(name, sa.Integer(), nullable=False, server_default='0'))


def downgrade():
    with op.batch_alter_table('run_token_usage', schema=None) as batch_op:
        for name in reversed(COLUMNS):
            batch_op.drop_column(name)
