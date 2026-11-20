import { useEffect, useState } from 'react'
import { Globe2, LockKeyhole, LogOut, Plus, ShieldCheck } from 'lucide-react'
import { apiRequest, useResource } from './api.js'
import { latestChecks, TargetTable } from './MonitoringDashboard.jsx'
import { Busy, Empty, ErrorNotice, PageHeading, Skeleton } from './Ui.jsx'

function Login({ onLogin, pending, message }) {
  return <section className="card login-card">
    <header className="card-heading">
      <div><h2>Administrator sign in</h2><p>Sign in to manage scheduled monitoring targets.</p></div>
      <span className="metric-icon tone-green"><LockKeyhole size={18} aria-hidden="true" /></span>
    </header>
    <div className="card-body">
      <form className="stack-form" onSubmit={onLogin}>
        <label htmlFor="admin-username">Username</label>
        <input id="admin-username" name="username" autoComplete="username" autoCapitalize="none" maxLength={64} required disabled={pending} />
        <label htmlFor="admin-password">Password</label>
        <input id="admin-password" name="password" type="password" autoComplete="current-password" maxLength={1024} required disabled={pending} />
        {message && <ErrorNotice>{message}</ErrorNotice>}
        <button className="button primary" type="submit" disabled={pending}>
          {pending ? <Busy>Signing in…</Busy> : <><LockKeyhole size={16} aria-hidden="true" />Sign in</>}
        </button>
      </form>
    </div>
  </section>
}

export default function Admin({ history, analytics, onRefresh, onHistory }) {
  const [session, setSession] = useState({ state: 'loading' })
  const [attempt, setAttempt] = useState(0)
  const [revision, setRevision] = useState(0)
  const [pending, setPending] = useState('')
  const [message, setMessage] = useState(null)
  const [url, setUrl] = useState('')
  const targets = useResource(session.state === 'active' ? '/api/admin/targets' : null, revision, true)
  const expired = targets.state === 'error' && targets.error.status === 401
  const active = session.state === 'active' && !expired

  useEffect(() => {
    const controller = new AbortController()
    apiRequest('/api/admin/session', { admin: true, signal: controller.signal })
      .then((data) => {
        if (!controller.signal.aborted) setSession({ state: 'active', ...data })
      })
      .catch((error) => {
        if (controller.signal.aborted) return
        setSession(error.status === 401 ? { state: 'signed-out' } : { state: 'error' })
      })
    return () => controller.abort()
  }, [attempt])

  async function login(event) {
    event.preventDefault()
    const values = new FormData(event.currentTarget)
    event.currentTarget.elements.password.value = ''
    setPending('login')
    setMessage(null)
    setSession({ state: 'signed-out' })
    try {
      await apiRequest('/api/admin/login', {
        method: 'POST', admin: true,
        body: { username: values.get('username').trim(), password: values.get('password') },
      })
      // Confirm the browser accepted the session cookie before showing protected controls.
      const data = await apiRequest('/api/admin/session', { admin: true })
        .catch(() => { throw new Error('The browser could not establish a session. Open the configured app origin and try again.') })
      setSession({ state: 'active', ...data })
      setRevision((current) => current + 1)
    } catch (error) {
      setMessage({ error: true, text: error.message })
    } finally {
      setPending('')
    }
  }

  async function mutate(key, path, method, body, success) {
    setPending(key)
    setMessage(null)
    try {
      await apiRequest(path, { method, body, admin: true, csrf: session.csrf_token })
      if (key === 'logout') {
        setSession({ state: 'signed-out' })
      } else {
        if (key === 'add') setUrl('')
        setRevision((current) => current + 1)
        onRefresh()
      }
      setMessage({ text: success })
    } catch (error) {
      if (error.status === 401) {
        setSession({ state: 'signed-out' })
        setMessage({ error: true, text: 'Your session ended. Sign in again.' })
      } else {
        setMessage({ error: true, text: error.status ? error.message : 'The API could not be reached.' })
      }
    } finally {
      setPending('')
    }
  }

  return <>
    <PageHeading title="Administration" description="Manage the websites included in scheduled monitoring."
      actions={active && <button className="button secondary" type="button" disabled={Boolean(pending)}
        onClick={() => mutate('logout', '/api/admin/logout', 'POST', undefined, 'Signed out.')}>
        {pending === 'logout' ? <Busy>Signing out…</Busy> : <><LogOut size={16} aria-hidden="true" />Sign out</>}
      </button>} />
    {session.state === 'loading' && <section className="card card-body"><Skeleton label="Verifying admin session" /></section>}
    {session.state === 'error' && <section className="card card-body">
      <ErrorNotice>The admin session could not be verified.</ErrorNotice>
      <button className="button secondary mt-4" type="button" onClick={() => {
        setSession({ state: 'loading' }); setAttempt((current) => current + 1)
      }}>Retry</button>
    </section>}
    {(session.state === 'signed-out' || expired) &&
      <Login onLogin={login} pending={pending === 'login'} message={expired ? 'Your session ended. Sign in again.' : message?.error ? message.text : null} />}
    {active && <>
      <section className="card form-card">
        <header className="card-heading">
          <div><h2>Add monitoring target</h2><p>Signed in as {session.username}</p></div>
          <ShieldCheck size={20} className="muted" aria-hidden="true" />
        </header>
        <div className="card-body">
          <form className="stack-form" onSubmit={(event) => {
            event.preventDefault()
            mutate('add', '/api/targets', 'POST', { url: url.trim() }, 'Monitoring target added.')
          }}>
            <label htmlFor="target-url">Public HTTPS URL</label>
            <div className="inline-form">
              <input id="target-url" type="url" placeholder="https://example.com" value={url} maxLength={2048}
                onChange={(event) => setUrl(event.target.value)} required disabled={Boolean(pending)} />
              <button className="button primary" type="submit" disabled={Boolean(pending)}>
                {pending === 'add' ? <Busy>Adding…</Busy> : <><Plus size={17} aria-hidden="true" />Add website</>}
              </button>
            </div>
          </form>
          {message && <div className="form-feedback">{message.error ? <ErrorNotice>{message.text}</ErrorNotice> : <p className="success-note" role="status">{message.text}</p>}</div>}
        </div>
      </section>
      <section className="card">
        <header className="card-heading"><div><h2>Monitoring targets</h2><p>Enable or disable scheduled checks and review saved history.</p></div>
          <button className="button secondary small" type="button" onClick={() => setRevision((current) => current + 1)}>Refresh targets</button>
        </header>
        {targets.state === 'loading' && <div className="card-body"><Skeleton label="Loading targets" /></div>}
        {targets.state === 'error' && <div className="card-body"><ErrorNotice>{targets.error.status ? targets.error.message : 'Monitoring targets could not be loaded.'}</ErrorNotice></div>}
        {targets.state === 'success' && (targets.data.length ? <TargetTable targets={targets.data} latest={latestChecks(history, analytics)}
          onHistory={onHistory} showEnabled action={(target) => (
            <button className="button secondary small" type="button" disabled={Boolean(pending)}
              onClick={() => mutate(target.id, `/api/targets/${encodeURIComponent(target.id)}`, 'PATCH', { enabled: !target.enabled },
                target.enabled ? 'Monitoring target disabled.' : 'Monitoring target enabled.')}>
              {pending === target.id ? <Busy>Saving…</Busy> : target.enabled ? 'Disable' : 'Enable'}
            </button>
          )} /> : <Empty icon={Globe2} title="No monitoring targets" detail="Use the form above to add your first website." />)}
      </section>
    </>}
  </>
}
