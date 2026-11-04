'use client';

import {
  useCallback,
  useEffect,
  useState,
  type SubmitEvent,
  type ReactNode,
} from 'react';
import { ArrowRight, LogOut, Workflow } from 'lucide-react';
import './auth.css';

type Account = { id: string; email: string; tenant_id: string };
type Status = { enabled: boolean; needs_setup: boolean; registration_mode: 'open' | 'invite' | 'closed' };

export default function AuthGate({ children }: { children: ReactNode }) {
  const [status, setStatus] = useState<Status | null>(null);
  const [account, setAccount] = useState<Account | null>(null);
  const [loading, setLoading] = useState(true);
  const [mode, setMode] = useState<'login' | 'register'>('login');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  const checkSession = useCallback(async () => {
    try {
      const response = await fetch('/api/auth/status', { cache: 'no-store' });
      if (!response.ok)
        throw new Error(
          'Could not connect to Relay. Check that the server is running.',
        );
      const nextStatus: Status = await response.json();
      setStatus(nextStatus);
      if (nextStatus.needs_setup) setMode('register');
      else if (nextStatus.registration_mode === 'closed') setMode('login');
      if (nextStatus.enabled) {
        const me = await fetch('/api/auth/me', { cache: 'no-store' });
        if (!me.ok && me.status !== 401)
          throw new Error('Could not verify your session. Please retry.');
        setAccount(me.ok ? await me.json() : null);
      }
      setError('');
    } catch (cause) {
      setError(
        cause instanceof Error ? cause.message : 'Could not connect to Relay.',
      );
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void Promise.resolve().then(checkSession);
    const refresh = () => {
      void checkSession();
    };
    window.addEventListener('focus', refresh);
    const interval = window.setInterval(refresh, 60_000);
    return () => {
      window.removeEventListener('focus', refresh);
      window.clearInterval(interval);
    };
  }, [checkSession]);

  async function submit(event: SubmitEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = event.currentTarget;
    const data = new FormData(form);
    setBusy(true);
    setError('');
    try {
      const response = await fetch(`/api/auth/${mode}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          email: data.get('email'),
          password: data.get('password'),
          ...(mode === 'register' ? { invite_token: data.get('invite_token') || '' } : {}),
        }),
      });
      const body = (await response.json()) as Account & { detail?: unknown };
      if (!response.ok)
        throw new Error(
          typeof body.detail === 'string'
            ? body.detail
            : 'Check your email and password. New passwords must contain at least 12 characters.',
        );
      form.reset();
      setAccount(body);
      setStatus({ enabled: true, needs_setup: false, registration_mode: status?.registration_mode || 'closed' });
    } catch (cause) {
      setError(
        cause instanceof Error
          ? cause.message
          : 'Sign-in failed. Please try again.',
      );
    } finally {
      setBusy(false);
    }
  }

  async function logout() {
    setBusy(true);
    setError('');
    try {
      const response = await fetch('/api/auth/logout', { method: 'POST' });
      if (!response.ok)
        throw new Error('Could not sign out. Please try again.');
      setAccount(null);
      setMode('login');
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : 'Could not sign out.');
    } finally {
      setBusy(false);
    }
  }

  if (loading)
    return (
      <main className="auth-screen">
        <output>Connecting to your workspace…</output>
      </main>
    );
  if (status && (!status.enabled || account))
    return (
      <>
        <div key={account?.tenant_id || 'local'}>{children}</div>
        {status.enabled && account && (
          <div className="auth-session">
            {error && <span role="alert">{error}</span>}
            <span title={account.email}>{account.email}</span>
            <button
              type="button"
              onClick={() => void logout()}
              disabled={busy}
              aria-label="Sign out"
            >
              <LogOut size={13} /> Sign out
            </button>
          </div>
        )}
      </>
    );

  const setup = status?.needs_setup;
  return (
    <main className="auth-screen">
      <section className="auth-card" aria-labelledby="auth-title">
        <div className="auth-brand">
          <Workflow size={27} /> relay
        </div>
        <p className="auth-kicker">YOUR WORK, CONNECTED</p>
        <h1 id="auth-title">
          {setup
            ? 'Set up your workspace'
            : mode === 'register'
              ? 'Create your workspace'
              : 'Welcome back'}
        </h1>
        <p className="auth-description">
          {setup
            ? 'Create the first account to keep your existing workflows, runs, and credentials in your workspace.'
            : mode === 'register'
              ? 'Start with a private workspace for your workflows and credentials.'
              : 'Sign in to build workflows and pick up where you left off.'}
        </p>
        {error && (
          <p className="auth-error" role="alert">
            {error}
          </p>
        )}
        {!status ? (
          <button
            className="button primary"
            onClick={() => void checkSession()}
          >
            Retry connection
          </button>
        ) : (
          <>
            <form onSubmit={submit}>
              <label htmlFor="auth-email">
                Email
                <input
                  id="auth-email"
                  name="email"
                  type="email"
                  autoComplete="username"
                  maxLength={254}
                  required
                  disabled={busy}
                />
              </label>
              <label htmlFor="auth-password">
                Password
                <input
                  id="auth-password"
                  name="password"
                  type="password"
                  autoComplete={
                    mode === 'register' ? 'new-password' : 'current-password'
                  }
                  minLength={mode === 'register' ? 12 : 1}
                  maxLength={1024}
                  required
                  disabled={busy}
                />
              </label>
              {mode === 'register' && status.registration_mode === 'invite' && (
                <label htmlFor="auth-invite">
                  Invitation token
                  <input id="auth-invite" name="invite_token" type="password" autoComplete="off" maxLength={256} required disabled={busy} />
                </label>
              )}
              {mode === 'register' && (
                <p className="auth-password-hint">
                  Use at least 12 characters. Choose a password you haven’t used
                  elsewhere.
                </p>
              )}
              <button className="button primary" type="submit" disabled={busy}>
                {busy
                  ? 'Please wait…'
                  : mode === 'register'
                    ? 'Create workspace'
                    : 'Sign in'}
                <ArrowRight size={16} />
              </button>
            </form>
            {!setup && status.registration_mode !== 'closed' && (
              <button
                className="auth-switch"
                type="button"
                disabled={busy}
                onClick={() => {
                  setMode(mode === 'login' ? 'register' : 'login');
                  setError('');
                }}
              >
                {mode === 'login'
                  ? 'New here? Create a workspace'
                  : 'Already have an account? Sign in'}
              </button>
            )}
          </>
        )}
      </section>
    </main>
  );
}
