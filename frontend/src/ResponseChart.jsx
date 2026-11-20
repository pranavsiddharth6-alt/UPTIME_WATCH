import { useState } from 'react'
import { Activity } from 'lucide-react'
import { CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { displayHost, Empty, ErrorNotice, formatDate, Skeleton, timing } from './Ui.jsx'

const shortTime = new Intl.DateTimeFormat(undefined, {
  month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit',
})

export default function ResponseChart({ history }) {
  const [selection, setSelection] = useState('')
  const points = (history.data || []).filter((row) =>
    Number.isFinite(row.response_time_ms) && Number.isFinite(Date.parse(row.checked_at))
  )
  const sites = [...new Set(points.map((row) => row.url))].sort()
  const site = sites.includes(selection) ? selection : sites[0]
  const series = points.filter((row) => row.url === site).reverse().map((row) => ({
    timestamp: Date.parse(row.checked_at), response: row.response_time_ms,
  }))
  const first = series[0]?.timestamp
  const last = series.at(-1)?.timestamp
  const domain = series.length && first === last ? [first - 60000, last + 60000] : ['dataMin', 'dataMax']

  return <section className="card">
    <header className="card-heading">
      <div><h2>Response time</h2><p>Measured timings from recent checks.</p></div>
      {site && <select className="compact-select" aria-label="Website for response-time chart" value={site} onChange={(event) => setSelection(event.target.value)}>
        {sites.map((url) => <option key={url} value={url}>{displayHost(url)}</option>)}
      </select>}
    </header>
    <div className="chart-body">
      {history.state === 'loading' && <Skeleton rows={4} label="Loading response times" />}
      {history.state === 'error' && <ErrorNotice>Response times could not be loaded.</ErrorNotice>}
      {history.state === 'success' && !series.length && <Empty icon={Activity} title="No response times yet" detail="Saved checks with measured timings will appear here." />}
      {series.length > 0 && <div className="trend-chart">
        <ResponsiveContainer width="100%" height={236}>
          <LineChart data={series} margin={{ top: 14, right: 12, bottom: 4, left: 0 }}>
            <CartesianGrid stroke="#e2e8f0" vertical={false} strokeDasharray="4 4" />
            <XAxis dataKey="timestamp" type="number" domain={domain} minTickGap={44} tickFormatter={(time) => shortTime.format(time)}
              axisLine={false} tickLine={false} tick={{ fill: '#64748b', fontSize: 11 }} />
            <YAxis unit=" ms" width={58} axisLine={false} tickLine={false} tick={{ fill: '#64748b', fontSize: 11 }} />
            <Tooltip labelFormatter={(label) => formatDate(Number(label))} formatter={(value) => [timing(value), 'Response time']} />
            <Line dataKey="response" stroke="#059669" strokeWidth={2.5} dot={series.length < 20} activeDot={{ r: 5 }} isAnimationActive={false} />
          </LineChart>
        </ResponsiveContainer>
      </div>}
    </div>
  </section>
}
