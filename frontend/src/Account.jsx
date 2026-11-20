import { useCallback } from 'react'
import { ArrowRight, LockKeyhole, LogOut, UserRound } from 'lucide-react'
import GoogleSignIn from './GoogleSignIn.jsx'
import { Busy, ErrorNotice, PageHeading, Skeleton } from './Ui.jsx'

export default function Account({ auth }) {
  const { session, pending, error, signIn, signOut, refresh } = auth
  const googleLogin = useCallback((credential) => signIn('google', { credential }), [signIn])

  function localLogin(event) {
    event.preventDefault()
    const values = new FormData(event.currentTarget)
    event.currentTarget.elements.password.value = ''
    signIn('local', { username: values.get('username').trim(), password: values.get('password') })
  }

  if (session.state === 'loading') return <section className="card card-body login-card">
    <Skeleton label="Checking your session" />
  </section>

  if (session.state === 'error') return <section className="card card-body login-card">
    <ErrorNotice>Your session could not be checked. Please try again.</ErrorNotice>
    <button type="button" className="button secondary mt-4" onClick={refresh}>Retry</button>
  </section>

  if (session.state === 'active') return <>
    <PageHeading title="Account" description="Manage your UptimeWatch session." />
    <section className="card login-card">
      <header className="card-heading account-profile">
        <span className="metric-icon tone-green"><UserRound size={20} aria-hidden="true" /></span>
        <div><h2>{session.user.name}</h2>{session.user.email && <p>{session.user.email}</p>}</div>
      </header>
      <div className="card-body account-actions">
        {error && <ErrorNotice>{error}</ErrorNotice>}
        <div className="account-links">
          <a className="button primary" href="#overview">Dashboard <ArrowRight size={16} aria-hidden="true" /></a>
          <button type="button" className="button secondary" disabled={Boolean(pending)} onClick={() => signOut(session.csrf_token)}>
            {pending === 'logout' ? <Busy>Signing out…</Busy> : <><LogOut size={16} aria-hidden="true" />Sign out</>}
          </button>
        </div>
      </div>
    </section>
  </>

  return <div className="signin-layout">
    <section className="card login-card" aria-labelledby="signin-title">
      <header className="card-heading">
        <div><h1 id="signin-title">Sign in</h1><p>Continue to your UptimeWatch account.</p></div>
        <span className="metric-icon tone-green"><LockKeyhole size={18} aria-hidden="true" /></span>
      </header>
      <div className="card-body">
        {session.notice && <p className="account-notice" role="status">{session.notice}</p>}
        {error && <div className="account-notice"><ErrorNotice>{error}</ErrorNotice></div>}
        <form className="stack-form" onSubmit={localLogin}>
          <label htmlFor="username">Username or email</label>
          <input id="username" name="username" autoComplete="username" autoCapitalize="none" maxLength={64} required disabled={Boolean(pending)} />
          <label htmlFor="password">Password</label>
          <input id="password" name="password" type="password" autoComplete="current-password" maxLength={1024} required disabled={Boolean(pending)} />
          <button className="button primary" type="submit" disabled={Boolean(pending)}>
            {pending === 'local' ? <Busy>Signing in…</Busy> : 'Sign in'}
          </button>
        </form>
        <div className="sign-in-divider"><span>or</span></div>
        {pending === 'google' && <p role="status" className="account-notice">Signing in with Google…</p>}
        <GoogleSignIn onCredential={googleLogin} disabled={Boolean(pending)} />
      </div>
    </section>
  </div>
}
