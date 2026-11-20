import assert from 'node:assert/strict'
import { readFile, readdir } from 'node:fs/promises'
import { test } from 'node:test'

test('Vercel uses one static config with the Vite build and same-origin API rewrites', async () => {
  const files = await readdir(new URL('../', import.meta.url))
  assert.deepEqual(files.filter((file) => /^vercel\.(json|[cm]?[jt]s)$/.test(file)), ['vercel.json'])

  const config = JSON.parse(await readFile(new URL('../vercel.json', import.meta.url), 'utf8'))
  const origin = 'https://uptime-watch-q67r.onrender.com'
  assert.equal(config.framework, 'vite')
  assert.equal(config.buildCommand, 'npm run build')
  assert.equal(config.outputDirectory, 'dist')
  assert.deepEqual(config.rewrites, [
    { source: '/api/:path*', destination: `${origin}/api/:path*` },
    { source: '/health', destination: `${origin}/health` },
  ])
})
