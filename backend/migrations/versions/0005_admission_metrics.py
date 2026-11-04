"""Content-free request admission counters, derived from durable events."""
from alembic import op
import sqlalchemy as sa

revision='0005_admission_metrics'
down_revision='0004_approval_kind'
branch_labels=None
depends_on=None


def upgrade():
    op.create_table('run_admission_metrics',
        sa.Column('run_id',sa.String(64),primary_key=True),
        sa.Column('seq',sa.Integer,primary_key=True),
        sa.Column('stage',sa.String(24),nullable=False),
        sa.Column('status',sa.String(16),nullable=False),
        sa.Column('outcome',sa.String(24),nullable=False),
        sa.Column('replay',sa.Boolean,nullable=False))


def downgrade():
    op.drop_table('run_admission_metrics')
