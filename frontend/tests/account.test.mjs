import assert from 'node:assert/strict'
import { after, before, test } from 'node:test'
import react from '@vitejs/plugin-react'
import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { createServer } from 'vite'

let vite
let DashboardApp

before(async () => {
  vite = await createServer({ configFile: false, plugins: [react()], server: { middlewareMode: true, ws: false } })
  const app = await vite.ssrLoadModule('/src/App.jsx')
  DashboardApp = app.DashboardApp
})

after(async () => {
  await vite?.close()
})

function render(hash, session, auth = {}) {
  const previousWindow = globalThis.window
  globalThis.window = { location: { hash } }
  try {
    return renderToStaticMarkup(createElement(DashboardApp, { auth: { session, ...auth } }))
  } finally {
    globalThis.window = previousWindow
  }
}

test('signed-out navigation offers one sign-in page with no role selector', () => {
  const html = render('#account', { state: 'signed-out' })
  assert.match(html, /Dashboard/)
  assert.match(html, />Sign In</)
  assert.match(html, /Username or email/)
  assert.match(html, /type="password"/)
  assert.match(html, />or</)
  assert.match(html, /Loading Google sign-in/)
  assert.doesNotMatch(html, /Admin|Manage Targets|name="role"/)
})

test('Google users see their profile and cannot open the management route', () => {
  const html = render('#manage', {
    state: 'active', user: { role: 'user', name: 'Jamie', email: 'jamie@example.net' },
  })
  assert.match(html, /Account/)
  assert.match(html, /Jamie/)
  assert.match(html, /jamie@example\.net/)
  assert.match(html, /Sign out/)
  assert.doesNotMatch(html, /Manage Targets|Add monitoring target/)
})

test('authenticated administrators can open target management', () => {
  const html = render('#manage', { state: 'active', user: { role: 'admin', name: 'Operator' } })
  assert.match(html, /Manage Targets/)
  assert.match(html, /Add monitoring target/)
  assert.match(html, /Signed in as Operator/)
})

test('account distinguishes session failure, expiry, sign-in and sign-out errors', () => {
  assert.match(render('#account', { state: 'error' }), /Your session could not be checked/)
  assert.match(render('#account', { state: 'signed-out', notice: 'Your session ended.' }), /Your session ended/)
  assert.match(render('#account', { state: 'signed-out' }, { error: 'Invalid username or password.' }), /Invalid username or password/)
  assert.match(render('#account', { state: 'active', user: { role: 'user', name: 'Jamie' } },
    { pending: 'logout', error: 'Sign-out failed.' }), /Sign-out failed\./)
})
