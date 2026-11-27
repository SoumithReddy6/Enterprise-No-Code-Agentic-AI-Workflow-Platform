"""Store the request digest of a settled external write

Revision ID: 0007_action_result_digest
Revises: 0006_usage_status
Create Date: 2026-11-26

A settled write is found again by its invocation. The digest of the request that was sent
(payload and connection) lets a resumed run see that the request at that invocation is a
different one, and stop instead of reporting the old result for it (DEF-02). Rows written
before this revision read NULL and are served as before. A downgrade drops the column.
"""
from alembic import op
import sqlalchemy as sa


revision = '0007_action_result_digest'
down_revision = '0006_usage_status'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('run_action_results', schema=None) as batch_op:
        batch_op.add_column(sa.Column('payload_sha256', sa.String(64), nullable=True))


def downgrade():
    with op.batch_alter_table('run_action_results', schema=None) as batch_op:
        batch_op.drop_column('payload_sha256')
