import assert from 'node:assert/strict'
import { test } from 'node:test'

test('Vercel requires a valid Render HTTPS origin and rewrites only API and health', async () => {
  const previous = process.env.UPTIMEWATCH_RENDER_ORIGIN
  try {
    for (const [index, invalid] of ['', 'http://service.onrender.com', 'https://service.onrender.com/path',
      'https://service.onrender.com.example.net', 'https://user@service.onrender.com'].entries()) {
      process.env.UPTIMEWATCH_RENDER_ORIGIN = invalid
      await assert.rejects(import(`../vercel.mjs?case=${index}`), /UPTIMEWATCH_RENDER_ORIGIN/)
    }

    const origin = 'https://service.onrender.com'
    process.env.UPTIMEWATCH_RENDER_ORIGIN = `${origin}/`
    const { config } = await import('../vercel.mjs?case=valid')
    assert.deepEqual(config.rewrites, [
      { source: '/api/:path*', destination: `${origin}/api/:path*` },
      { source: '/health', destination: `${origin}/health` },
    ])
  } finally {
    if (previous === undefined) delete process.env.UPTIMEWATCH_RENDER_ORIGIN
    else process.env.UPTIMEWATCH_RENDER_ORIGIN = previous
  }
})
