"""Workspace accounts and revocable, opaque browser sessions."""
import hashlib
import hmac
import os
import secrets
import time
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import Column, Integer, String, delete, select, update, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .observability import journal
from .auth_security import reserve_login, clear_success, client_address, security_transaction
from .storage import Base, ModelRecord, CredentialRecord, RunRecord, RunEventRecord, WorkflowRecord, new_id

COOKIE_NAME = 'relay_session'
SESSION_SECONDS = 7 * 24 * 60 * 60
PASSWORD_ITERATIONS = 600_000


class AccountRecord(Base):
    __tablename__ = 'auth_accounts'
    id = Column(String(64), primary_key=True)
    email = Column(String(254), unique=True, nullable=False)
    password_hash = Column(String(256), nullable=False)
    tenant_id = Column(String(64), nullable=False)


class BootstrapRecord(Base):
    __tablename__ = 'auth_bootstrap'
    id = Column(Integer, primary_key=True)
    tenant_id = Column(String(64), nullable=False)


class SessionRecord(Base):
    __tablename__ = 'auth_sessions'
    token_hash = Column(String(64), primary_key=True)
    account_id = Column(String(64), nullable=False, index=True)
    expires_at = Column(Integer, nullable=False)


class InviteRecord(Base):
    __tablename__ = 'auth_invites'
    token_hash = Column(String(64), primary_key=True)
    email = Column(String(254), nullable=False, default='')
    expires_at = Column(Integer, nullable=False)
    used_at = Column(Integer, nullable=True)


class LoginInput(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=1024)

    @field_validator('email')
    @classmethod
    def valid_email(cls, value):
        value = value.strip().lower()
        if len(value.split('@')) != 2 or not all(value.split('@')) or any(c.isspace() for c in value):
            raise ValueError('Enter a valid email address.')
        return value


class RegisterInput(LoginInput):
    invite_token: str = Field(default='', max_length=256)
    password: str = Field(min_length=12, max_length=1024)


def password_hash(password, salt=None):
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac('sha256', password.encode(), bytes.fromhex(salt), PASSWORD_ITERATIONS).hex()
    return f'pbkdf2_sha256${PASSWORD_ITERATIONS}${salt}${digest}'


def password_matches(password, encoded):
    _, iterations, salt, expected = encoded.split('$')
    digest = hashlib.pbkdf2_hmac('sha256', password.encode(), bytes.fromhex(salt), int(iterations)).hex()
    return hmac.compare_digest(digest, expected)


def token_hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


def issue_invite(engine, email='', hours=24):
    if not 1 <= hours <= 168:
        raise ValueError('Invite lifetime must be 1–168 hours')
    if email:
        email = LoginInput(email=email, password='validation-only').email
    token = secrets.token_urlsafe(32)
    with security_transaction(engine) as session:
        session.execute(delete(InviteRecord).where(InviteRecord.expires_at <= int(time.time())))
        session.add(InviteRecord(token_hash=token_hash(token), email=email,
                                 expires_at=int(time.time()) + hours * 3600))
    return token


def public_account(account):
    return {'id': account.id, 'email': account.email, 'tenant_id': account.tenant_id}


class AuthController:
    def __init__(self, store, enabled):
        self.store = store
        self.enabled = enabled
        self.registration_mode = os.getenv('AUTH_REGISTRATION_MODE', 'closed').strip().lower()
        if self.registration_mode not in {'open', 'invite', 'closed'}:
            raise ValueError('AUTH_REGISTRATION_MODE must be open, invite, or closed')
        self.dummy_hash = password_hash(secrets.token_urlsafe(24))
        self.origin = os.getenv('AUTH_ORIGIN', '').rstrip('/')

    def require_same_origin(self, request: Request):
        origin = request.headers.get('origin')
        allowed = {self.origin} if self.origin else {'http://127.0.0.1:3000', 'http://localhost:3000'}
        allowed.add(str(request.base_url).rstrip('/'))
        if (origin and origin not in allowed) or request.headers.get('sec-fetch-site') == 'cross-site':
            raise HTTPException(403, 'Cross-origin requests are not allowed.')

    def account(self, request: Request):
        token = request.cookies.get(COOKIE_NAME, '')
        if not token or len(token) > 256:
            raise HTTPException(401, 'Sign in to continue.')
        with Session(self.store.engine) as session:
            record = session.get(SessionRecord, token_hash(token))
            if not record or record.expires_at <= int(time.time()):
                raise HTTPException(401, 'Sign in to continue.')
            account = session.get(AccountRecord, record.account_id)
            if not account:
                raise HTTPException(401, 'Sign in to continue.')
            request.state.tenant_id=account.tenant_id
            return public_account(account)

    def tenant(self, request: Request) -> str:
        if self.enabled and request.method in {'POST', 'PUT', 'PATCH', 'DELETE'}:
            self.require_same_origin(request)
        tenant=self.account(request)['tenant_id'] if self.enabled else 'local'
        request.state.tenant_id=tenant
        from .observability import tenant_id
        tenant_id.set(tenant)
        return tenant

    def operator(self,request:Request):
        if not self.enabled:raise HTTPException(401,'Operator authentication is required.')
        account=self.account(request)
        configured=os.environ.get('RELAY_OPERATOR_ACCOUNT_IDS')
        with Session(self.store.engine) as session:
            bootstrap=session.get(BootstrapRecord,1)
            allowed=account['id'] in {value.strip() for value in configured.split(',')} if configured is not None else bool(bootstrap and bootstrap.tenant_id==account['tenant_id'])
        if not allowed:raise HTTPException(403,'Operator access is required.')
        request.state.tenant_id=account['tenant_id']
        return account

    def create_session(self, session, account, request):
        old = request.cookies.get(COOKIE_NAME)
        if old:
            session.execute(delete(SessionRecord).where(SessionRecord.token_hash == token_hash(old)))
        session.execute(delete(SessionRecord).where(SessionRecord.expires_at <= int(time.time())))
        token = secrets.token_urlsafe(32)
        session.add(SessionRecord(token_hash=token_hash(token), account_id=account.id, expires_at=int(time.time()) + SESSION_SECONDS))
        return token

    def set_cookie(self, response, request, token):
        secure = urlsplit(self.origin).scheme == 'https' if self.origin else request.url.scheme == 'https'
        response.set_cookie(COOKIE_NAME, token, max_age=SESSION_SECONDS, httponly=True, secure=secure, samesite='strict', path='/')
        response.headers['Cache-Control'] = 'no-store'


def install_auth(app, store, enabled=True):
    controller = AuthController(store, enabled)
    Base.metadata.create_all(store.engine)
    router = APIRouter(prefix='/api/auth')

    @router.get('/status')
    def status(response: Response):
        response.headers['Cache-Control'] = 'no-store'
        with Session(store.engine) as session:
            return {'enabled': enabled, 'needs_setup': enabled and session.get(BootstrapRecord, 1) is None, 'registration_mode': controller.registration_mode}

    @router.post('/register', status_code=201, dependencies=[Depends(controller.require_same_origin)])
    def register(body: RegisterInput, request: Request, response: Response):
        if not enabled:
            raise HTTPException(403, 'Registration is not permitted.')
        try:
            with security_transaction(store.engine) as session:
                # Serialize mode check, bootstrap ownership, invite consumption and
                # account creation across workers, including the empty database.
                if store.engine.dialect.name == 'postgresql':
                    session.execute(text('SELECT pg_advisory_xact_lock(19482027)'))
                first = session.get(BootstrapRecord, 1) is None
                existing = session.scalar(select(AccountRecord.id).limit(1))
                if controller.registration_mode == 'closed' and (not first or existing):
                    raise HTTPException(403, 'Registration is not permitted.')
                if controller.registration_mode == 'invite':
                    invite = session.get(InviteRecord, token_hash(body.invite_token))
                    if not invite or invite.used_at is not None or invite.expires_at <= int(time.time()) or (invite.email and invite.email != body.email):
                        raise HTTPException(403, 'Registration is not permitted.')
                    invite.used_at = int(time.time())
                # Denied registration must not spend a password hash or expose
                # whether the submitted email belongs to an existing account.
                encoded = password_hash(body.password)
                account = AccountRecord(id=new_id(), email=body.email, password_hash=encoded, tenant_id=new_id())
                if first:
                    session.add(BootstrapRecord(id=1, tenant_id=account.tenant_id))
                    session.flush()
                    from .tool_service import TENANT_MODELS as TOOL_MODELS
                    from .agent_memory import TENANT_MODELS as MEMORY_MODELS
                    from .approvals import TENANT_MODELS as APPROVAL_MODELS
                    for model in (WorkflowRecord, RunRecord, RunEventRecord, CredentialRecord, ModelRecord, *TOOL_MODELS, *MEMORY_MODELS, *APPROVAL_MODELS):
                        session.execute(update(model).where(model.tenant_id == 'local').values(tenant_id=account.tenant_id))
                session.add(account)
                session.flush()
                token = controller.create_session(session, account, request)
                result = public_account(account)
        except IntegrityError:
            raise HTTPException(409, 'Account already exists. Try signing in.') from None
        if controller.registration_mode=='invite':journal(event='auth.invite_consumed',tenant=result['tenant_id'])
        journal(event='auth.register_success',tenant=result['tenant_id'],status=201)
        request.state.tenant_id=result['tenant_id']
        controller.set_cookie(response, request, token)
        return result

    @router.post('/login', dependencies=[Depends(controller.require_same_origin)])
    def login(body: LoginInput, request: Request, response: Response):
        if not enabled:
            raise HTTPException(403, 'Authentication is disabled.')
        try:reservations = reserve_login(store.engine, body.email, client_address(request))
        except HTTPException:
            journal(event='auth.login_blocked',status=429);raise
        with Session(store.engine) as session:
            account = session.scalar(select(AccountRecord).where(AccountRecord.email == body.email))
            matches = password_matches(body.password, account.password_hash if account else controller.dummy_hash)
            if not account or not matches:
                journal(event='auth.login_failure',status=401,tenant=account.tenant_id if account else None)
                from .auth_security import LoginBudget
                for key,revision in reservations.items():
                    budget=session.get(LoginBudget,key)
                    if budget and budget.revision==revision and budget.attempts>=10:
                        journal(event='auth.lockout',scope=key.split(':',1)[0],reason_code='attempt_limit',tenant=account.tenant_id if account else None)
                raise HTTPException(401, 'Invalid email or password.')
            token = controller.create_session(session, account, request)
            result = public_account(account)
            session.commit()
        clear_success(store.engine, reservations)
        request.state.tenant_id=result['tenant_id']
        journal(event='auth.login_success',tenant=result['tenant_id'],status=200)
        controller.set_cookie(response, request, token)
        return result

    @router.post('/logout', dependencies=[Depends(controller.require_same_origin)])
    def logout(request: Request, response: Response):
        with Session(store.engine) as session:
            session.execute(delete(SessionRecord).where(SessionRecord.token_hash == token_hash(request.cookies.get(COOKIE_NAME, ''))))
            session.commit()
        response.delete_cookie(COOKIE_NAME, path='/', httponly=True, samesite='strict')
        response.headers['Cache-Control'] = 'no-store'
        return {'ok': True}

    @router.get('/me')
    def me(request: Request, response: Response):
        response.headers['Cache-Control'] = 'no-store'
        if not enabled:
            return {'id': 'local', 'email': '', 'tenant_id': 'local'}
        return controller.account(request)

    app.include_router(router)
    return controller
