import { useEffect, useState } from 'react'

const configured = import.meta.env.DEV ? (import.meta.env.VITE_API_URL || '').replace(/\/+$/, '') : ''
const localApi = /^http:\/\/(localhost|127\.0\.0\.1):5000$/
const apiBase = import.meta.env.DEV && localApi.test(configured) ? '' : configured

export class ApiError extends Error {
  constructor(message, status) {
    super(message)
    this.status = status
  }
}

export async function apiRequest(path, { method = 'GET', body, admin = false, csrf, signal } = {}) {
  const headers = new Headers()
  if (body !== undefined) headers.set('Content-Type', 'application/json')
  if (csrf) headers.set('X-CSRF-Token', csrf)

  const response = await fetch(`${apiBase}${path}`, {
    method, headers, signal, cache: 'no-store', credentials: admin ? 'include' : 'omit',
    body: body === undefined ? undefined : JSON.stringify(body),
  })
  if (response.status === 204) return null
  const data = await response.json().catch(() => null)
  if (!response.ok) throw new ApiError(data?.error || 'The request could not be completed.', response.status)
  if (data === null) throw new ApiError('The server returned an invalid response.', response.status)
  return data
}

export function useResource(path, revision, admin = false) {
  const [result, setResult] = useState(null)
  useEffect(() => {
    if (!path) return
    const controller = new AbortController()
    apiRequest(path, { admin, signal: controller.signal })
      .then((data) => {
        if (!controller.signal.aborted) setResult({ path, revision, state: 'success', data })
      })
      .catch((error) => {
        if (!controller.signal.aborted) setResult({ path, revision, state: 'error', error })
      })
    return () => controller.abort()
  }, [path, revision, admin])
  if (!path) return { state: 'idle' }
  return result?.path === path && result.revision === revision ? result : { state: 'loading' }
}
