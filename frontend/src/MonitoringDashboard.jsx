import { lazy, Suspense, useState } from 'react'
import {
  ArrowRight, CircleCheck, CircleX, Clock3, Globe2, History, ListFilter,
  Radio, Search, Signal, Zap,
} from 'lucide-react'
import { apiRequest } from './api.js'
import { Busy, displayHost, Empty, ErrorNotice, formatDate, PageHeading, Skeleton, Status, timing } from './Ui.jsx'

const ResponseChart = lazy(() => import('./ResponseChart.jsx'))

export function latestChecks(history, analytics) {
  const latest = new Map()
  for (const check of history.data || []) {
    if (!latest.has(check.url)) latest.set(check.url, check)
  }
  for (const site of analytics.data || []) {
    if (site.latest_check) latest.set(site.url, site.latest_check)
  }
  return latest
}

function Metric({ label, value, detail, icon: Icon, tone, loading }) {
  return <section className="card metric">
    <div className="metric-top"><span>{label}</span><span className={`metric-icon tone-${tone}`}><Icon size={18} aria-hidden="true" /></span></div>
    {loading ? <div className="skeleton metric-skeleton" aria-label="Loading" role="status" /> : <strong>{value}</strong>}
    <p>{detail}</p>
  </section>
}

export function HistoryTable({ records }) {
  return <div className="table-scroll" tabIndex={0} role="region" aria-label="Website check results">
    <table>
      <thead><tr><th scope="col">Website</th><th scope="col">Status</th><th scope="col">HTTP code</th><th scope="col">Response time</th><th scope="col">Checked at</th></tr></thead>
      <tbody>
        {records.map((record) => <tr key={record.id}>
          <td><span className="table-website"><Globe2 size={16} aria-hidden="true" /><strong>{displayHost(record.url)}</strong></span></td>
          <td><Status value={record.status} /></td>
          <td>{record.http_status ?? '—'}</td>
          <td>{timing(record.response_time_ms)}</td>
          <td><time dateTime={record.checked_at}>{formatDate(record.checked_at)}</time></td>
        </tr>)}
      </tbody>
    </table>
  </div>
}

export function Overview({ targets, history, analytics }) {
  const latest = latestChecks(history, analytics)
  const registered = targets.data || []
  const online = registered.filter(({ url }) => latest.get(url)?.status === 'online').length
  const offline = registered.filter(({ url }) => latest.get(url)?.status === 'offline').length
  const checks = (analytics.data || []).reduce((sum, site) => sum + site.total_checks, 0)
  const successful = (analytics.data || []).reduce((sum, site) => sum + site.online_checks, 0)
  const statusReady = targets.state === 'success' && (analytics.state === 'success' || history.state === 'success')
  const statusLoading = targets.state === 'loading' || (!statusReady && (analytics.state === 'loading' || history.state === 'loading'))
  const statusDetail = analytics.state === 'success' ? 'Latest saved observations' : 'Latest in recent history'

  return <>
    <PageHeading title="Monitoring overview" description="Track website availability, response times, and recent checks."
      actions={<a className="button primary" href="#check"><Zap size={17} aria-hidden="true" />Check a website</a>} />
    <div className="metric-grid">
      <Metric label="Registered websites" value={targets.data?.length ?? '—'} detail="Enabled monitoring targets" icon={Globe2} tone="blue" loading={targets.state === 'loading'} />
      <Metric label="Online" value={statusReady ? online : '—'} detail={statusDetail} icon={CircleCheck} tone="green" loading={statusLoading} />
      <Metric label="Offline" value={statusReady ? offline : '—'} detail={statusDetail} icon={CircleX} tone="red" loading={statusLoading} />
      <Metric label="Observed uptime" value={checks ? `${(successful / checks * 100).toFixed(2)}%` : '—'}
        detail={checks ? `${checks.toLocaleString()} saved checks` : analytics.state === 'error' ? 'Analytics unavailable' : 'Sample-based uptime'}
        icon={Signal} tone="amber" loading={analytics.state === 'loading'} />
    </div>
    <div className="overview-grid">
      <Suspense fallback={<section className="card card-body"><Skeleton rows={4} label="Loading response-time chart" /></section>}>
        <ResponseChart history={history} />
      </Suspense>
      <section className="card">
        <header className="card-heading"><div><h2>Website status</h2><p>Latest observations for enabled targets.</p></div><Radio size={18} className="muted" aria-hidden="true" /></header>
        <div className="card-body">
          {statusLoading && <Skeleton label="Loading website status" />}
          {!statusLoading && !statusReady && <ErrorNotice>Website status could not be loaded.</ErrorNotice>}
          {statusReady && !registered.length && <Empty icon={Globe2} title="No registered websites" detail="Registered websites will appear here when monitoring is set up." />}
          {statusReady && registered.length > 0 && <ul className="site-list">
            {registered.slice(0, 5).map(({ id, url }) => <li key={id}>
              <div><strong>{displayHost(url)}</strong><small>{latest.get(url) ? formatDate(latest.get(url).checked_at) : 'Awaiting a recent sample'}</small></div>
              <Status value={latest.get(url)?.status} />
            </li>)}
            <li><a className="text-link" href="#monitored">View all websites <ArrowRight size={15} /></a></li>
          </ul>}
        </div>
      </section>
    </div>
    <section className="card">
      <header className="card-heading"><div><h2>Recent checks</h2><p>Latest saved monitoring results.</p></div><a className="button secondary small" href="#history">View history <ArrowRight size={15} /></a></header>
      {history.state === 'loading' && <div className="card-body"><Skeleton label="Loading saved checks" /></div>}
      {history.state === 'error' && <div className="card-body"><ErrorNotice>Saved checks could not be loaded.</ErrorNotice></div>}
      {history.state === 'success' && (history.data.length
        ? <HistoryTable records={history.data.slice(0, 5)} />
        : <Empty icon={History} title="No saved checks" detail="Scheduled checks and checks saved from Monitored Websites appear here." />)}
    </section>
  </>
}

export function WebsiteCheck() {
  const [url, setUrl] = useState('')
  const [result, setResult] = useState({ state: 'idle' })
  const checking = result.state === 'loading'

  async function check(event) {
    event.preventDefault()
    if (checking || !url.trim()) return
    setResult({ state: 'loading' })
    try {
      const data = await apiRequest('/api/check-once', { method: 'POST', body: { url: url.trim() } })
      setResult({ state: 'success', data })
    } catch (error) {
      setResult({ state: 'error', message: error.status === 429
        ? 'Check limit reached. Wait a moment before trying again.'
        : error.status ? error.message : 'The API could not be reached. Try again.' })
    }
  }

  return <>
    <PageHeading title="Check a website" description="Run a one-time availability check for a public HTTPS website." />
    <section className="card form-card">
      <header className="card-heading"><div><h2>Website health check</h2><p>One-time results stay on this page.</p></div><span className="metric-icon tone-green"><Zap size={18} aria-hidden="true" /></span></header>
      <div className="card-body">
        <form onSubmit={check} className="stack-form">
          <label htmlFor="website-url">Public HTTPS URL</label>
          <div className="inline-form">
            <input id="website-url" type="url" placeholder="https://github.com" autoCapitalize="none" spellCheck={false}
              maxLength={2048} value={url} onChange={(event) => setUrl(event.target.value)} disabled={checking} required />
            <button className="button primary" disabled={checking}>{checking ? <Busy>Checking…</Busy> : <><Zap size={16} />Check Website</>}</button>
          </div>
        </form>
        {result.state === 'error' && <div className="form-feedback"><ErrorNotice>{result.message}</ErrorNotice></div>}
        {result.state === 'success' && <div className="check-result" role="status">
          <div className="result-heading"><strong>{result.data.url}</strong><Status value={result.data.status} /></div>
          <dl><div><dt>HTTP status</dt><dd>{result.data.http_status ?? 'No HTTP response'}</dd></div>
            <div><dt>Response time</dt><dd>{timing(result.data.response_time_ms)}</dd></div></dl>
        </div>}
      </div>
    </section>
  </>
}

export function TargetTable({ targets, latest, onHistory, action, showEnabled = false }) {
  return <div className="table-scroll" tabIndex={0} role="region" aria-label="Registered websites">
    <table>
      <thead><tr><th scope="col">Website</th>{showEnabled && <th scope="col">Monitoring</th>}<th scope="col">Latest status</th><th scope="col">Last checked</th><th scope="col">Actions</th></tr></thead>
      <tbody>{targets.map((target) => {
        const check = latest.get(target.url)
        return <tr key={target.id}>
          <td><span className="table-website"><Globe2 size={16} aria-hidden="true" /><strong>{displayHost(target.url)}</strong></span></td>
          {showEnabled && <td><span className={`badge ${target.enabled ? 'badge-online' : 'badge-neutral'}`}>{target.enabled ? 'Enabled' : 'Disabled'}</span></td>}
          <td><Status value={check?.status} /></td>
          <td>{formatDate(check?.checked_at)}</td>
          <td><div className="table-actions"><button className="button secondary small" onClick={() => onHistory(target.url)} type="button">History</button>{action(target)}</div></td>
        </tr>
      })}</tbody>
    </table>
  </div>
}

function SiteAnalytics({ analytics }) {
  return <section className="card">
    <header className="card-heading"><div><h2>Website analytics</h2><p>Observed uptime and response times from saved checks.</p></div><Signal size={19} className="muted" aria-hidden="true" /></header>
    {analytics.state === 'loading' && <div className="card-body"><Skeleton label="Loading website analytics" /></div>}
    {analytics.state === 'error' && <div className="card-body"><ErrorNotice>Website analytics could not be loaded.</ErrorNotice></div>}
    {analytics.state === 'success' && (analytics.data.length
      ? <div className="table-scroll" tabIndex={0} role="region" aria-label="Website analytics">
        <table>
          <thead><tr><th scope="col">Website</th><th scope="col">Total checks</th><th scope="col">Online</th><th scope="col">Offline</th><th scope="col">Observed uptime</th><th scope="col">Avg. response</th></tr></thead>
          <tbody>{analytics.data.map((site) => <tr key={site.url}>
            <td><span className="table-website"><Globe2 size={16} aria-hidden="true" /><strong>{displayHost(site.url)}</strong></span></td>
            <td>{site.total_checks}</td><td>{site.online_checks}</td><td>{site.offline_checks}</td>
            <td>{site.uptime_percent == null ? 'No samples' : `${site.uptime_percent}%`}</td>
            <td>{timing(site.average_response_time_ms)}</td>
          </tr>)}</tbody>
        </table>
      </div>
      : <Empty icon={Signal} title="No website analytics yet" detail="Register a target or save a check to see per-site results." />)}
  </section>
}

export function MonitoredPage({ targets, history, analytics, onRefresh, onHistory }) {
  const [pending, setPending] = useState(null)
  const [message, setMessage] = useState(null)

  async function check(target) {
    setPending(target.id)
    setMessage(null)
    try {
      const result = await apiRequest('/api/check', { method: 'POST', body: { url: target.url } })
      setMessage({ text: `${displayHost(target.url)} was ${result.status}. The check was saved.` })
      onRefresh()
    } catch (error) {
      setMessage({ error: true, text: error.status ? error.message : 'The API could not be reached.' })
    } finally {
      setPending(null)
    }
  }

  return <>
    <PageHeading title="Monitored websites" description="Review approved monitoring targets and their latest saved observations." />
    <section className="card">
      <header className="card-heading"><div><h2>Registered websites</h2><p>Enabled targets included in automatic monitoring.</p></div><Radio size={19} className="muted" /></header>
      {message && <div className="card-body">{message.error ? <ErrorNotice>{message.text}</ErrorNotice> : <p className="success-note" role="status">{message.text}</p>}</div>}
      {targets.state === 'loading' && <div className="card-body"><Skeleton label="Loading registered websites" /></div>}
      {targets.state === 'error' && <div className="card-body"><ErrorNotice>Registered websites could not be loaded.</ErrorNotice></div>}
      {targets.state === 'success' && (targets.data.length ? <TargetTable targets={targets.data} latest={latestChecks(history, analytics)} onHistory={onHistory}
        action={(target) => <button type="button" className="button secondary small" disabled={Boolean(pending)} onClick={() => check(target)}>
          {pending === target.id ? <Busy>Checking…</Busy> : 'Check and save'}</button>} />
        : <Empty icon={Globe2} title="No registered websites" detail="Enabled monitoring targets will appear here." />)}
    </section>
    <SiteAnalytics analytics={analytics} />
  </>
}

export function HistoryPage({ history, site, onClearSite, search, onSearch }) {
  const [status, setStatus] = useState('all')
  const records = (history.data || []).filter((check) =>
    check.url.toLowerCase().includes(search.trim().toLowerCase()) && (status === 'all' || check.status === status))

  return <>
    <PageHeading title="Check history" description={site ? `Saved checks for ${displayHost(site)}.` : 'Browse and filter the latest 100 saved monitoring checks.'}
      actions={site && <button className="button secondary" type="button" onClick={onClearSite}>Show all websites</button>} />
    <section className="card">
      <header className="card-heading history-heading">
        <div><h2>Saved checks</h2><p>{history.state === 'success' ? `${records.length} of ${history.data.length} results` : 'Saved website observations'}</p></div>
        <div className="filter-bar">
          <label className="search-field"><Search size={16} aria-hidden="true" /><input aria-label="Search websites" placeholder="Search websites" value={search} onChange={(event) => onSearch(event.target.value)} /></label>
          <label className="filter-field"><ListFilter size={16} aria-hidden="true" /><select aria-label="Filter by status" value={status} onChange={(event) => setStatus(event.target.value)}>
            <option value="all">All statuses</option><option value="online">Online</option><option value="offline">Offline</option>
          </select></label>
        </div>
      </header>
      {history.state === 'loading' && <div className="card-body"><Skeleton label="Loading saved checks" /></div>}
      {history.state === 'error' && <div className="card-body"><ErrorNotice>Check history could not be loaded.</ErrorNotice></div>}
      {history.state === 'success' && (records.length ? <HistoryTable records={records} />
        : <Empty icon={Clock3} title={history.data.length ? 'No matching checks' : 'No saved checks'} detail={history.data.length ? 'Adjust your website search or status filter.' : 'Saved monitoring checks will appear here.'} />)}
    </section>
  </>
}
