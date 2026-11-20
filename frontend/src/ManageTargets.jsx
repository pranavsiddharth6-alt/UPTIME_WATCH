import { useEffect, useState } from 'react'
import { Globe2, Plus, ShieldCheck } from 'lucide-react'
import { apiRequest, useResource } from './api.js'
import { latestChecks, TargetTable } from './MonitoringDashboard.jsx'
import { Busy, Empty, ErrorNotice, PageHeading, Skeleton } from './Ui.jsx'

export default function ManageTargets({ session, history, analytics, onRefresh, onHistory, onSessionCheck }) {
  const [revision, setRevision] = useState(0)
  const [pending, setPending] = useState('')
  const [message, setMessage] = useState(null)
  const [url, setUrl] = useState('')
  const targets = useResource('/api/admin/targets', revision, true)
  const status = targets.error?.status

  useEffect(() => {
    if (status === 401 || status === 403) onSessionCheck()
  }, [status, onSessionCheck])

  async function mutate(key, path, method, body, success) {
    setPending(key)
    setMessage(null)
    try {
      await apiRequest(path, { method, body, authenticated: true, csrf: session.csrf_token })
      if (key === 'add') setUrl('')
      setRevision((current) => current + 1)
      onRefresh()
      setMessage({ text: success })
    } catch (error) {
      if (error.status === 401 || error.status === 403) onSessionCheck()
      setMessage({ error: true, text: error.status ? error.message : 'The API could not be reached.' })
    } finally {
      setPending('')
    }
  }

  return <>
    <PageHeading title="Manage Targets" description="Choose which websites are checked by scheduled monitoring." />
    <section className="card form-card">
      <header className="card-heading">
        <div><h2>Add monitoring target</h2><p>Signed in as {session.user.name}</p></div>
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
  </>
}
