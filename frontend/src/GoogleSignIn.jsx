import { useEffect, useRef, useState } from 'react'
import { apiRequest } from './api.js'

let scriptRequest

function loadGoogle() {
  if (window.google?.accounts?.id) return Promise.resolve(window.google.accounts.id)
  if (!scriptRequest) {
    const script = document.createElement('script')
    script.src = 'https://accounts.google.com/gsi/client'
    script.async = true
    scriptRequest = new Promise((resolve, reject) => {
      function finish(api) {
        window.clearTimeout(timer)
        script.onload = script.onerror = null
        if (api) resolve(api)
        else reject(new Error('Google script unavailable'))
      }
      const timer = window.setTimeout(() => finish(), 10000)
      script.onload = () => finish(window.google?.accounts?.id)
      script.onerror = () => finish()
      document.head.append(script)
    }).catch((error) => {
      script.remove()
      scriptRequest = undefined
      throw error
    })
  }
  return scriptRequest
}

export default function GoogleSignIn({ onCredential, disabled }) {
  const target = useRef(null)
  const [attempt, setAttempt] = useState(0)
  const [status, setStatus] = useState('loading')

  useEffect(() => {
    const controller = new AbortController()
    const button = target.current
    let expired

    async function renderButton() {
      try {
        const { client_id, nonce } = await apiRequest('/api/auth/google/challenge', {
          method: 'POST', authenticated: true, signal: controller.signal,
        })
        if (controller.signal.aborted) return
        const google = await loadGoogle()
        if (controller.signal.aborted) return
        google.initialize({
          client_id, nonce, ux_mode: 'popup', auto_select: false,
          callback: async ({ credential }) => {
            if (!controller.signal.aborted && credential) {
              const signedIn = await onCredential(credential)
              if (!signedIn && !controller.signal.aborted) setStatus('retry')
            }
          },
        })
        google.renderButton(button, { theme: 'outline', size: 'large', text: 'continue_with', shape: 'rectangular' })
        setStatus('ready')
        expired = window.setTimeout(() => setStatus('retry'), 9 * 60 * 1000)
      } catch (error) {
        if (!controller.signal.aborted) setStatus(error.status === 429 ? 'limited' : 'unavailable')
      }
    }

    renderButton()
    return () => {
      controller.abort()
      window.clearTimeout(expired)
      button.replaceChildren()
    }
  }, [attempt, onCredential])

  function retry() {
    setStatus('loading')
    setAttempt((value) => value + 1)
  }

  return <div className="google-signin">
    <div ref={target} hidden={status !== 'ready'} inert={disabled} />
    {status === 'loading' && <p className="muted" role="status">Loading Google sign-in…</p>}
    {status === 'unavailable' && <p className="muted" role="status">Google sign-in is currently unavailable.</p>}
    {status === 'limited' && <p className="muted" role="status">Google sign-in is busy. Wait a minute and try again.</p>}
    {['retry', 'unavailable', 'limited'].includes(status) && <button type="button" className="button secondary small"
      disabled={disabled} onClick={retry}>Retry Google sign-in</button>}
  </div>
}
