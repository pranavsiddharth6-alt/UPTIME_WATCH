const RESPONSE_HINT_DELAY = 8000

export function isGoogleChallenge(value) {
  return typeof value?.client_id === 'string'
    && /^[0-9]+-[A-Za-z0-9_-]+\.apps\.googleusercontent\.com$/.test(value.client_id)
    && typeof value.nonce === 'string' && /^[0-9a-f]{64}$/.test(value.nonce)
}

export function renderGoogleButton(google, target, challenge, {
  useFedCM = true, onWaiting, onNoResponse, onCredential, onRejected,
}) {
  let promptTimer
  let submitted = false
  let disposed = false

  google.initialize({
    client_id: challenge.client_id,
    nonce: challenge.nonce,
    ux_mode: 'popup',
    use_fedcm_for_button: useFedCM,
    auto_select: false,
    button_auto_select: false,
    callback: async ({ credential } = {}) => {
      if (disposed || submitted || !credential) return
      submitted = true
      clearTimeout(promptTimer)
      const signedIn = await onCredential(credential)
      if (!signedIn && !disposed) onRejected()
    },
  })
  google.renderButton(target, {
    theme: 'outline', size: 'large', text: 'continue_with', shape: 'rectangular',
    click_listener: () => {
      if (disposed || submitted) return
      // GIS owns the click and opens the chooser. This listener only updates feedback.
      clearTimeout(promptTimer)
      onWaiting()
      promptTimer = setTimeout(onNoResponse, RESPONSE_HINT_DELAY)
    },
  })

  return () => {
    disposed = true
    clearTimeout(promptTimer)
  }
}
