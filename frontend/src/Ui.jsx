import { AlertCircle, CircleCheck, CircleMinus, LoaderCircle } from 'lucide-react'

const dateFormat = new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'short' })
const numberFormat = new Intl.NumberFormat(undefined, { maximumFractionDigits: 2 })

export function formatDate(value) {
  const date = new Date(value)
  return value && Number.isFinite(date.getTime()) ? dateFormat.format(date) : '—'
}

export function timing(value) {
  return Number.isFinite(value) ? `${numberFormat.format(value)} ms` : '—'
}

export function displayHost(url) {
  try {
    const value = new URL(url)
    return value.host + (value.pathname === '/' ? '' : value.pathname)
  } catch {
    return url
  }
}

export function Status({ value, label }) {
  const Icon = value === 'online' ? CircleCheck : value === 'offline' ? AlertCircle : CircleMinus
  return <span className={`badge badge-${value || 'neutral'}`}><Icon size={13} aria-hidden="true" />{label || (
    value === 'online' ? 'Online' : value === 'offline' ? 'Offline' : 'No recent check'
  )}</span>
}

export function Empty({ icon: Icon, title, detail, action }) {
  return <div className="empty-state">
    <span className="empty-icon"><Icon size={20} aria-hidden="true" /></span>
    <strong>{title}</strong>
    {detail && <p>{detail}</p>}
    {action}
  </div>
}

export function ErrorNotice({ children }) {
  return <div className="notice" role="alert"><AlertCircle size={17} aria-hidden="true" /><span>{children}</span></div>
}

export function Skeleton({ rows = 3, label = 'Loading data' }) {
  return <div className="skeleton-stack" role="status" aria-label={label}>
    {Array.from({ length: rows }, (_, index) => <div key={index} className="skeleton" />)}
  </div>
}

export function Busy({ children }) {
  return <><LoaderCircle size={16} className="animate-spin" aria-hidden="true" />{children}</>
}

export function PageHeading({ title, description, actions }) {
  return <header className="page-heading">
    <div><h1>{title}</h1><p>{description}</p></div>
    {actions && <div className="page-actions">{actions}</div>}
  </header>
}
