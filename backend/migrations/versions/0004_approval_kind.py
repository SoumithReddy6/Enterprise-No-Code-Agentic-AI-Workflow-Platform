"""Typed verification/decision reviews, retaining legacy write approvals."""
from alembic import op
import sqlalchemy as sa

revision='0004_approval_kind'
down_revision='0003_loop_storage'
branch_labels=None
depends_on=None


def upgrade():
    with op.batch_alter_table('run_approvals') as batch:
        batch.add_column(sa.Column('kind',sa.String(20),nullable=False,server_default='write'))
        batch.alter_column('invocation',existing_type=sa.String(300),type_=sa.String(512),existing_nullable=False)
        batch.create_check_constraint('ck_run_approval_kind',"kind IN ('write','decision','verification')")


def downgrade():
    if op.get_bind().execute(sa.text("SELECT count(*) FROM run_approvals WHERE kind!='write'")).scalar():
        raise RuntimeError('Cannot downgrade typed review records into write approvals. Export/archive those records first.')
    if op.get_bind().execute(sa.text('SELECT count(*) FROM run_approvals WHERE length(invocation)>300')).scalar():
        raise RuntimeError('Cannot downgrade approval invocation ids longer than 300 characters. Export/archive those records first.')
    with op.batch_alter_table('run_approvals') as batch:
        batch.drop_constraint('ck_run_approval_kind',type_='check')
        batch.alter_column('invocation',existing_type=sa.String(512),type_=sa.String(300),existing_nullable=False)
        batch.drop_column('kind')
