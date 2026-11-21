import { useEffect, useRef, useState } from 'react'
import { apiRequest } from './api.js'
import { isGoogleChallenge, renderGoogleButton } from './googleButton.js'

let scriptRequest

const ISSUES = {
  unavailable: 'The Google sign-in service is currently unavailable. Please try again.',
  origin: 'This address could not be verified for Google sign-in. Open the configured UptimeWatch site.',
  configuration: 'Google sign-in returned invalid setup information. Contact the site owner.',
  sdk: 'Google’s sign-in button could not load. Check your connection and allow Google Identity Services for this site.',
  limited: 'Google sign-in is busy. Wait a minute and try again.',
  expired: 'This sign-in attempt expired. Start a new one to continue.',
}

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
  const [promptHint, setPromptHint] = useState(false)
  const useFedCM = new URLSearchParams(globalThis.window?.location.search).get('google') !== 'popup'

  useEffect(() => {
    const controller = new AbortController()
    const button = target.current
    let expired
    let disposeButton

    async function prepareButton() {
      let challenge
      try {
        challenge = await apiRequest('/api/auth/google/challenge', {
          method: 'POST', authenticated: true, signal: controller.signal,
        })
      } catch (error) {
        if (!controller.signal.aborted) {
          setStatus(({ 403: 'origin', 429: 'limited' })[error.status] || 'unavailable')
        }
        return
      }
      if (controller.signal.aborted) return
      if (!isGoogleChallenge(challenge)) {
        setStatus('configuration')
        return
      }
      try {
        const google = await loadGoogle()
        if (controller.signal.aborted) return
        disposeButton = renderGoogleButton(google, button, challenge, {
          useFedCM,
          onWaiting: () => setPromptHint(false),
          onNoResponse: () => setPromptHint(true),
          onCredential: (credential) => {
            window.clearTimeout(expired)
            setPromptHint(false)
            return onCredential(credential)
          },
          onRejected: () => setStatus('retry'),
        })
        setStatus('ready')
        expired = window.setTimeout(() => {
          disposeButton()
          setStatus('expired')
        }, 9 * 60 * 1000)
      } catch {
        if (!controller.signal.aborted) setStatus('sdk')
      }
    }

    prepareButton()
    return () => {
      controller.abort()
      window.clearTimeout(expired)
      disposeButton?.()
      button.replaceChildren()
    }
  }, [attempt, onCredential, useFedCM])

  function retry() {
    setPromptHint(false)
    setStatus('loading')
    setAttempt((value) => value + 1)
  }

  return <div className="google-signin">
    <div ref={target} hidden={status !== 'ready'} inert={disabled} />
    {status === 'loading' && <p className="muted" role="status">Loading Google sign-in…</p>}
    {status === 'ready' && promptHint && <p className="muted" role="status">If Google did not open, check pop-up and third-party sign-in permissions. If Google shows a setup error, contact the site owner.</p>}
    {status === 'ready' && useFedCM && !disabled && <a className="button secondary small" href="?google=popup#account">Use Google popup instead</a>}
    {status === 'ready' && !useFedCM && !promptHint && <p className="muted" role="status">Click Continue with Google to open the popup.</p>}
    {ISSUES[status] && <p className="muted" role="status">{ISSUES[status]}</p>}
    {status !== 'loading' && status !== 'ready' && <button type="button" className="button secondary small"
      disabled={disabled} onClick={retry}>Retry Google sign-in</button>}
  </div>
}
