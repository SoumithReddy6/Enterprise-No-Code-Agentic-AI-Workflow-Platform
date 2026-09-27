"""Frozen snapshot of schema revision 0001_baseline.

Adoption of a pre-Alembic database must compare and repair against the baseline it
will be stamped as, not against whatever the models happen to declare today. Once a
later revision exists, using live metadata here would create tables at the newer
shape and then stamp them as 0001, so the next migration would collide.

This file is generated once, at 0001, and never edited when a model changes.
A model change gets a new revision; this snapshot stays as it was.
"""
import sqlalchemy as sa

metadata = sa.MetaData()

sa.Table(
    'agent_memories', metadata,
    sa.Column('tenant_id', sa.String(length=64), primary_key=True),
    sa.Column('key', sa.String(length=120), primary_key=True),
    sa.Column('messages', sa.JSON(), nullable=False),
)

sa.Table(
    'allowed_models', metadata,
    sa.Column('id', sa.String(length=64), primary_key=True),
    sa.Column('tenant_id', sa.String(length=64), nullable=False),
    sa.Column('provider', sa.String(length=24), nullable=False),
    sa.Column('model', sa.String(length=100), nullable=False),
    sa.Column('credential_id', sa.String(length=128), nullable=False),
    sa.Column('enabled', sa.Boolean(), nullable=False),
    sa.UniqueConstraint('tenant_id', 'provider', 'model', 'credential_id', name='uq_workspace_model_credential'),
)
sa.Index('ix_allowed_models_tenant_id', metadata.tables['allowed_models'].c['tenant_id'])

sa.Table(
    'approval_rate_limits', metadata,
    sa.Column('tenant_id', sa.String(length=64), primary_key=True),
    sa.Column('window', sa.Integer(), nullable=False),
    sa.Column('attempts', sa.Integer(), nullable=False),
)

sa.Table(
    'auth_accounts', metadata,
    sa.Column('id', sa.String(length=64), primary_key=True),
    sa.Column('email', sa.String(length=254), nullable=False),
    sa.Column('password_hash', sa.String(length=256), nullable=False),
    sa.Column('tenant_id', sa.String(length=64), nullable=False),
    sa.UniqueConstraint('email', name=None),
)

sa.Table(
    'auth_bootstrap', metadata,
    sa.Column('id', sa.Integer(), primary_key=True),
    sa.Column('tenant_id', sa.String(length=64), nullable=False),
)

sa.Table(
    'auth_invites', metadata,
    sa.Column('token_hash', sa.String(length=64), primary_key=True),
    sa.Column('email', sa.String(length=254), nullable=False),
    sa.Column('expires_at', sa.Integer(), nullable=False),
    sa.Column('used_at', sa.Integer(), nullable=True),
)

sa.Table(
    'auth_login_budgets', metadata,
    sa.Column('key', sa.String(length=80), primary_key=True),
    sa.Column('attempts', sa.Integer(), nullable=False),
    sa.Column('blocked_until', sa.Integer(), nullable=False),
    sa.Column('expires_at', sa.Integer(), nullable=False),
    sa.Column('revision', sa.String(length=32), nullable=False),
    sa.Column('principal', sa.String(length=80), nullable=False),
)
sa.Index('ix_auth_login_budgets_expires_at', metadata.tables['auth_login_budgets'].c['expires_at'])

sa.Table(
    'auth_sessions', metadata,
    sa.Column('token_hash', sa.String(length=64), primary_key=True),
    sa.Column('account_id', sa.String(length=64), nullable=False),
    sa.Column('expires_at', sa.Integer(), nullable=False),
)
sa.Index('ix_auth_sessions_account_id', metadata.tables['auth_sessions'].c['account_id'])

sa.Table(
    'credentials', metadata,
    sa.Column('id', sa.String(length=64), primary_key=True),
    sa.Column('tenant_id', sa.String(length=64), nullable=False),
    sa.Column('name', sa.String(length=120), nullable=False),
    sa.Column('provider', sa.String(length=24), nullable=False),
    sa.Column('encrypted', sa.Text(), nullable=False),
)
sa.Index('ix_credentials_tenant_id', metadata.tables['credentials'].c['tenant_id'])

sa.Table(
    'execution_jobs', metadata,
    sa.Column('run_id', sa.String(length=64), primary_key=True),
    sa.Column('status', sa.String(length=24), nullable=False),
    sa.Column('owner', sa.String(length=64), nullable=False),
    sa.Column('lease_until', sa.Float(), nullable=False),
    sa.Column('cancel_requested', sa.Boolean(), nullable=False),
    sa.Column('created', sa.Float(), nullable=False),
)
sa.Index('ix_execution_jobs_status', metadata.tables['execution_jobs'].c['status'])

sa.Table(
    'run_action_results', metadata,
    sa.Column('run_id', sa.String(length=64), primary_key=True),
    sa.Column('invocation', sa.String(length=300), primary_key=True),
    sa.Column('result', sa.Text(), nullable=False),
    sa.Column('created_at', sa.String(length=64), nullable=False),
)

sa.Table(
    'run_approvals', metadata,
    sa.Column('id', sa.String(length=64), primary_key=True),
    sa.Column('run_id', sa.String(length=64), nullable=False),
    sa.Column('tenant_id', sa.String(length=64), nullable=False),
    sa.Column('node_id', sa.String(length=128), nullable=False),
    sa.Column('checkpoint_owner', sa.String(length=128), nullable=False),
    sa.Column('invocation', sa.String(length=300), nullable=False),
    sa.Column('payload', sa.JSON(), nullable=False),
    sa.Column('digest', sa.String(length=64), nullable=False),
    sa.Column('execution', sa.Text(), nullable=False),
    sa.Column('status', sa.String(length=24), nullable=False),
    sa.Column('created_at', sa.Float(), nullable=False),
    sa.Column('expires_at', sa.Float(), nullable=False),
    sa.Column('result', sa.Text(), nullable=True),
    sa.Column('owns_marker', sa.Integer(), nullable=False),
    sa.UniqueConstraint('run_id', 'invocation', name='uq_run_approval_invocation'),
)
sa.Index('ix_run_approvals_expires_at', metadata.tables['run_approvals'].c['expires_at'])
sa.Index('ix_run_approvals_run_id', metadata.tables['run_approvals'].c['run_id'])
sa.Index('ix_run_approvals_status', metadata.tables['run_approvals'].c['status'])
sa.Index('ix_run_approvals_tenant_id', metadata.tables['run_approvals'].c['tenant_id'])

sa.Table(
    'run_events', metadata,
    sa.Column('run_id', sa.String(length=64), primary_key=True),
    sa.Column('seq', sa.Integer(), primary_key=True),
    sa.Column('tenant_id', sa.String(length=64), nullable=False),
    sa.Column('timestamp', sa.String(length=64), nullable=False),
    sa.Column('type', sa.String(length=64), nullable=False),
    sa.Column('payload', sa.JSON(), nullable=False),
)
sa.Index('ix_run_events_tenant_id', metadata.tables['run_events'].c['tenant_id'])

sa.Table(
    'run_guard_decisions', metadata,
    sa.Column('run_id', sa.String(length=64), primary_key=True),
    sa.Column('seq', sa.Integer(), primary_key=True),
    sa.Column('decision', sa.String(length=16), nullable=False),
    sa.Column('reason', sa.String(length=32), nullable=False),
)
sa.Index('ix_run_guard_decisions_decision', metadata.tables['run_guard_decisions'].c['decision'])

sa.Table(
    'run_token_usage', metadata,
    sa.Column('run_id', sa.String(length=64), primary_key=True),
    sa.Column('seq', sa.Integer(), primary_key=True),
    sa.Column('provider', sa.String(length=24), primary_key=True),
    sa.Column('model', sa.String(length=100), primary_key=True),
    sa.Column('prompt_tokens', sa.Integer(), nullable=False),
    sa.Column('completion_tokens', sa.Integer(), nullable=False),
    sa.Column('calls', sa.Integer(), nullable=False),
)

sa.Table(
    'runs', metadata,
    sa.Column('id', sa.String(length=64), primary_key=True),
    sa.Column('tenant_id', sa.String(length=64), nullable=False),
    sa.Column('data', sa.JSON(), nullable=False),
    sa.Column('created_at', sa.String(length=64), nullable=False),
    sa.Column('status', sa.String(length=24), nullable=False),
    sa.Column('name', sa.String(length=240), nullable=False),
    sa.Column('citation_counter', sa.Integer(), nullable=False),
    sa.Column('duration_seconds', sa.Float(), nullable=True),
    sa.Column('grounded', sa.Boolean(), nullable=False),
    sa.Column('abstained', sa.Boolean(), nullable=False),
    sa.Column('truncated', sa.Boolean(), nullable=False),
)
sa.Index('ix_runs_created_at', metadata.tables['runs'].c['created_at'])
sa.Index('ix_runs_status', metadata.tables['runs'].c['status'])
sa.Index('ix_runs_tenant_created_id', metadata.tables['runs'].c['tenant_id'], metadata.tables['runs'].c['created_at'], metadata.tables['runs'].c['id'])
sa.Index('ix_runs_tenant_id', metadata.tables['runs'].c['tenant_id'])

sa.Table(
    'schema_migrations', metadata,
    sa.Column('version', sa.String(length=80), primary_key=True),
)

sa.Table(
    'tool_connections', metadata,
    sa.Column('id', sa.String(length=64), primary_key=True),
    sa.Column('tenant_id', sa.String(length=64), nullable=False),
    sa.Column('encrypted', sa.Text(), nullable=False),
)
sa.Index('ix_tool_connections_tenant_id', metadata.tables['tool_connections'].c['tenant_id'])

sa.Table(
    'worker_heartbeats', metadata,
    sa.Column('id', sa.String(length=64), primary_key=True),
    sa.Column('last_seen', sa.Float(), nullable=False),
)
sa.Index('ix_worker_heartbeats_last_seen', metadata.tables['worker_heartbeats'].c['last_seen'])

sa.Table(
    'workflows', metadata,
    sa.Column('id', sa.String(length=64), primary_key=True),
    sa.Column('tenant_id', sa.String(length=64), nullable=False),
    sa.Column('document', sa.JSON(), nullable=False),
    sa.Column('updated_at', sa.String(length=64), nullable=False),
)
sa.Index('ix_workflows_tenant_id', metadata.tables['workflows'].c['tenant_id'])

