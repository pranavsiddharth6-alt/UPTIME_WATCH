const renderOrigin = (process.env.UPTIMEWATCH_RENDER_ORIGIN || '').trim().replace(/\/$/, '')

if (!/^https:\/\/[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.onrender\.com$/.test(renderOrigin)) {
  throw new Error('UPTIMEWATCH_RENDER_ORIGIN must be the HTTPS origin of your Render service.')
}

export const config = {
  framework: 'vite',
  buildCommand: 'npm run build',
  outputDirectory: 'dist',
  rewrites: [
    { source: '/api/:path*', destination: `${renderOrigin}/api/:path*` },
    { source: '/health', destination: `${renderOrigin}/health` },
  ],
}
