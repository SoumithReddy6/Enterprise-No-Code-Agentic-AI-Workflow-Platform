"""Durable pre-hash login budgets shared across API workers.

Attempts reserve a slot before expensive work; success releases its reservations.
Concurrent requests count conservatively and cannot erase newer failures.
"""
from contextlib import contextmanager
import hashlib
import ipaddress
import math
import secrets
import time
from fastapi import HTTPException
from sqlalchemy import Column, Integer, String, delete, select, text
from sqlalchemy.orm import Session
from .storage import Base

LOCK_SECONDS = 15 * 60

class LoginBudget(Base):
    __tablename__ = 'auth_login_budgets'
    key = Column(String(80), primary_key=True)
    attempts = Column(Integer, nullable=False, default=0)
    blocked_until = Column(Integer, nullable=False, default=0)
    expires_at = Column(Integer, nullable=False, index=True)
    revision = Column(String(32), nullable=False)
    principal = Column(String(80), nullable=False, default='')


def budget_key(kind, value):
    return kind + ':' + hashlib.sha256(value.encode()).hexdigest()


def client_address(request):
    # Only the ASGI peer is trusted. Proxy headers must be validated by the
    # deployment's explicitly trusted proxy layer, never parsed here.
    value = request.client.host if request.client else 'unknown-peer'
    try:
        address = ipaddress.ip_address(value)
        return str(address.ipv4_mapped or address) if isinstance(address, ipaddress.IPv6Address) else str(address)
    except ValueError:
        return value[:255]


@contextmanager
def security_transaction(engine):
    with Session(engine, expire_on_commit=False) as session:
        # SQLite has no row locks. Take its write lock only for short budget
        # updates, never for password hashing, network calls, or sleeping.
        if engine.dialect.name == 'sqlite':
            session.execute(text('BEGIN IMMEDIATE'))
        try:
            yield session
            session.commit()
        except BaseException:
            session.rollback()
            raise


def reserve_login(engine, email, address):
    now = int(time.time())
    reservations = {}
    with security_transaction(engine) as session:
        expired = select(LoginBudget.key).where(LoginBudget.expires_at <= now).limit(200)
        session.execute(delete(LoginBudget).where(LoginBudget.key.in_(expired)))
        keys = sorted((budget_key('account', email), budget_key('ip', address)))
        from sqlalchemy.dialects.sqlite import insert as sqlite_insert
        from sqlalchemy.dialects.postgresql import insert as pg_insert
        insert = pg_insert if engine.dialect.name == 'postgresql' else sqlite_insert
        rows = []
        for key in keys:
            session.execute(insert(LoginBudget).values(key=key, attempts=0, blocked_until=0,
                expires_at=now+LOCK_SECONDS, revision=secrets.token_hex(16), principal='').on_conflict_do_nothing(index_elements=['key']))
            row = session.scalar(select(LoginBudget).where(LoginBudget.key == key).with_for_update())
            if row.expires_at <= now:
                row.attempts = 0
                row.blocked_until = 0
                row.principal = ''
            rows.append(row)
        remaining = max(row.blocked_until-now for row in rows)
        if remaining > 0:
            raise HTTPException(429, 'Too many sign-in attempts. Try again after the cooldown or ask the workspace operator to unlock access.',
                                headers={'Retry-After': str(math.ceil(remaining)), 'Cache-Control':'no-store'})
        for row in rows:
            principal = budget_key('account', email)
            row.principal = principal if not row.principal else row.principal if row.principal == principal else '*'
            row.attempts += 1
            delay = LOCK_SECONDS if row.attempts >= 10 else 2**(row.attempts-5) if row.attempts >= 5 else 0
            row.blocked_until = now+delay
            row.expires_at = now+LOCK_SECONDS
            row.revision = secrets.token_hex(16)
            reservations[row.key] = row.revision
    return reservations


def clear_success(engine, reservations):
    with security_transaction(engine) as session:
        for key, revision in sorted(reservations.items()):
            # A later reserved attempt may already have failed: do not clear it.
            session.execute(delete(LoginBudget).where(LoginBudget.key == key, LoginBudget.revision == revision, LoginBudget.principal != '*'))


def unlock_login(engine, email=None, address=None):
    """Operator-only recovery. Never exposed as an unauthenticated HTTP route."""
    keys = []
    if email:keys.append(budget_key('account', email.strip().lower()))
    if address:
        parsed = ipaddress.ip_address(address)
        normalized = str(parsed.ipv4_mapped or parsed) if isinstance(parsed, ipaddress.IPv6Address) else str(parsed)
        keys.append(budget_key('ip', normalized))
    if not keys:raise ValueError('An email or IP address is required')
    with security_transaction(engine) as session:
        return session.execute(delete(LoginBudget).where(LoginBudget.key.in_(keys))).rowcount
