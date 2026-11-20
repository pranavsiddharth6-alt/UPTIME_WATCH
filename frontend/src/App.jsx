import { useEffect, useState } from 'react'
import {
  Activity, ChevronRight, Globe2, History, LayoutGrid, Menu, Radio, RefreshCw, ShieldCheck, X,
} from 'lucide-react'
import Admin from './Admin.jsx'
import { useResource } from './api.js'
import { HistoryPage, MonitoredPage, Overview, WebsiteCheck } from './MonitoringDashboard.jsx'

const pages = [
  ['overview', 'Overview', LayoutGrid],
  ['check', 'Check Website', Globe2],
  ['monitored', 'Monitored Websites', Radio],
  ['history', 'History', History],
  ['admin', 'Admin', ShieldCheck],
]

function readPage() {
  const page = window.location.hash.slice(1)
  return pages.some(([id]) => id === page) ? page : 'overview'
}

export default function App() {
  const [page, setPage] = useState(readPage)
  const [menuOpen, setMenuOpen] = useState(false)
  const [revision, setRevision] = useState(0)
  const [historySearch, setHistorySearch] = useState('')
  const [historyUrl, setHistoryUrl] = useState('')
  const health = useResource('/health', revision)
  const targets = useResource('/api/targets', revision)
  const history = useResource('/api/history?limit=100', revision)
  const analytics = useResource('/api/analytics?include_latest=1', revision)
  const siteHistory = useResource(
    page === 'history' && historyUrl ? `/api/history?limit=100&url=${encodeURIComponent(historyUrl)}` : null,
    revision,
  )
  const online = health.state === 'success' && health.data.status === 'healthy'
  const pageTitle = pages.find(([id]) => page === id)[1]

  useEffect(() => {
    function update() {
      setPage(readPage())
      setMenuOpen(false)
    }
    window.addEventListener('hashchange', update)
    return () => window.removeEventListener('hashchange', update)
  }, [])

  function refresh() {
    setRevision((current) => current + 1)
  }

  function openHistory(url) {
    setHistorySearch('')
    setHistoryUrl(url)
    window.location.hash = 'history'
  }

  return (
    <div className="app-shell">
      <a href="#main-content" className="skip-link" onClick={(event) => {
        event.preventDefault()
        document.getElementById('main-content')?.focus()
      }}>Skip to content</a>
      <aside id="navigation" className={`sidebar ${menuOpen ? 'sidebar-open' : ''}`}>
        <a href="#overview" className="brand" onClick={() => setMenuOpen(false)}>
          <span className="brand-icon"><Activity size={22} aria-hidden="true" /></span>
          <span>UptimeWatch</span>
        </a>
        <div className="nav-caption">Workspace</div>
        <nav aria-label="Main navigation">
          {pages.map(([id, label, Icon]) => (
            <a
              key={id} href={`#${id}`} className={page === id ? 'nav-link nav-current' : 'nav-link'}
              aria-current={page === id ? 'page' : undefined}
              onClick={() => {
                setMenuOpen(false)
                if (id === 'history') { setHistorySearch(''); setHistoryUrl('') }
              }}
            >
              <Icon size={18} aria-hidden="true" /><span>{label}</span>
            </a>
          ))}
        </nav>
      </aside>
      <div className="workspace">
        <header className="topbar">
          <div className="topbar-path">
            <button
              type="button" className="icon-button mobile-menu" aria-controls="navigation"
              aria-label={menuOpen ? 'Close navigation' : 'Open navigation'}
              aria-expanded={menuOpen} onClick={() => setMenuOpen((open) => !open)}
            >{menuOpen ? <X size={21} /> : <Menu size={21} />}</button>
            <span className="breadcrumb-root">Workspace</span>
            <ChevronRight size={14} className="breadcrumb-root" aria-hidden="true" />
            <strong>{pageTitle}</strong>
          </div>
          <div className="topbar-actions">
            <span className={`health-pill ${online ? 'health-up' : health.state === 'loading' ? '' : 'health-down'}`} role="status">
              <span className="health-dot" />
              {health.state === 'loading' ? 'Checking API' : online ? 'API operational' : 'API unavailable'}
            </span>
            <button type="button" className="icon-button" aria-label="Refresh dashboard data" onClick={refresh}>
              <RefreshCw size={17} aria-hidden="true" />
            </button>
          </div>
        </header>
        <main id="main-content" tabIndex={-1} className="main-content">
          {page === 'overview' && <Overview targets={targets} history={history} analytics={analytics} />}
          {page === 'check' && <WebsiteCheck />}
          {page === 'monitored' && <MonitoredPage targets={targets} history={history} analytics={analytics} onRefresh={refresh} onHistory={openHistory} />}
          {page === 'history' && <HistoryPage history={historyUrl ? siteHistory : history} site={historyUrl}
            onClearSite={() => setHistoryUrl('')} search={historySearch} onSearch={setHistorySearch} />}
          {page === 'admin' && <Admin history={history} analytics={analytics} onRefresh={refresh} onHistory={openHistory} />}
        </main>
      </div>
    </div>
  )
}
