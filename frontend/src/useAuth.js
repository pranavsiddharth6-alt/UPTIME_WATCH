import { useCallback, useEffect, useRef, useState } from 'react'
import { apiRequest } from './api.js'

const expiredMessage = 'Your session ended. Sign in again to continue.'

export function useAuth() {
  const [session, setSession] = useState({ state: 'loading' })
  const [pending, setPending] = useState('')
  const [error, setError] = useState('')
  const activeRequest = useRef(null)
  const changingSession = useRef(false)

  const refresh = useCallback(async () => {
    if (changingSession.current) return
    activeRequest.current?.abort()
    const controller = new AbortController()
    activeRequest.current = controller
    try {
      const account = await apiRequest('/api/auth/session', { authenticated: true, signal: controller.signal })
      if (!controller.signal.aborted) setSession({ state: 'active', ...account })
    } catch (failure) {
      if (controller.signal.aborted) return
      setSession((current) => {
        if (failure.status === 401) {
          if (current.state === 'signed-out') return current
          return { state: 'signed-out', notice: current.state === 'active' ? expiredMessage : '' }
        }
        return current.state === 'active' ? current : { state: 'error' }
      })
    }
  }, [])

  useEffect(() => {
    refresh()
    window.addEventListener('focus', refresh)
    return () => {
      window.removeEventListener('focus', refresh)
      activeRequest.current?.abort()
    }
  }, [refresh])

  useEffect(() => {
    if (session.state !== 'active' || !session.expires_at) return
    const remaining = Date.parse(session.expires_at) - Date.now()
    const timer = window.setTimeout(() => setSession({ state: 'signed-out', notice: expiredMessage }), Math.max(0, remaining))
    return () => window.clearTimeout(timer)
  }, [session.state, session.expires_at])

  const signIn = useCallback(async (method, body) => {
    if (changingSession.current) return false
    changingSession.current = true
    activeRequest.current?.abort()
    setPending(method)
    setError('')
    try {
      await apiRequest(`/api/auth/${method}`, { method: 'POST', authenticated: true, body })
      const account = await apiRequest('/api/auth/session', { authenticated: true }).catch(() => {
        throw new Error('Your browser could not establish a session. Open the configured site and try again.')
      })
      setSession({ state: 'active', ...account })
      return true
    } catch (failure) {
      setError(failure.message || 'Sign-in failed. Please try again.')
      return false
    } finally {
      changingSession.current = false
      setPending('')
    }
  }, [])

  const signOut = useCallback(async (csrf) => {
    if (changingSession.current) return
    changingSession.current = true
    activeRequest.current?.abort()
    setPending('logout')
    setError('')
    try {
      await apiRequest('/api/auth/logout', { method: 'POST', authenticated: true, csrf })
      setSession({ state: 'signed-out', notice: 'You are signed out.' })
    } catch (failure) {
      if (failure.status === 401) setSession({ state: 'signed-out', notice: expiredMessage })
      else setError(failure.message || 'Sign-out failed. Please try again.')
    } finally {
      changingSession.current = false
      setPending('')
    }
  }, [])

  return { session, refresh, pending, error, signIn, signOut }
}
