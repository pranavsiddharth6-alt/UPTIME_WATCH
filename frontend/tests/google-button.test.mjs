import assert from 'node:assert/strict'
import { test } from 'node:test'
import { isGoogleChallenge, renderGoogleButton } from '../src/googleButton.js'

const challenge = { client_id: '1234-unittest.apps.googleusercontent.com', nonce: 'a'.repeat(64) }

test('rejects a malformed challenge before initializing Google', () => {
  assert.equal(isGoogleChallenge(challenge), true)
  for (const invalid of [null, {}, { ...challenge, client_id: 'wrong' }, { ...challenge, nonce: 'short' }]) {
    assert.equal(isGoogleChallenge(invalid), false)
  }
})

test('enables FedCM, keeps GIS responsible for the click, and reports a missing callback', async (t) => {
  t.mock.timers.enable({ apis: ['setTimeout'] })
  const sdk = { initialize: t.mock.fn(), renderButton: t.mock.fn() }
  const handlers = {
    onWaiting: t.mock.fn(), onNoResponse: t.mock.fn(), onCredential: t.mock.fn(async () => true),
    onRejected: t.mock.fn(),
  }
  const cleanup = renderGoogleButton(sdk, {}, challenge, handlers)
  const [config] = sdk.initialize.mock.calls[0].arguments
  const [, button] = sdk.renderButton.mock.calls[0].arguments
  assert.equal(config.use_fedcm_for_button, true)
  assert.equal(config.ux_mode, 'popup')
  assert.equal(config.button_auto_select, false)
  assert.equal(config.nonce, challenge.nonce)
  assert.equal(button.text, 'continue_with')

  button.click_listener()
  assert.equal(handlers.onWaiting.mock.callCount(), 1)
  assert.equal(handlers.onCredential.mock.callCount(), 0)
  t.mock.timers.tick(8000)
  assert.equal(handlers.onNoResponse.mock.callCount(), 1)
  assert.equal(sdk.initialize.mock.callCount(), 1)

  await config.callback({ credential: 'test-credential' })
  await config.callback({ credential: 'duplicate-credential' })
  assert.equal(handlers.onCredential.mock.callCount(), 1)
  assert.deepEqual(handlers.onCredential.mock.calls[0].arguments, ['test-credential'])
  assert.equal(handlers.onRejected.mock.callCount(), 0)
  cleanup()
})

test('a rejected credential enables retry, and a disposed attempt ignores old callbacks', async (t) => {
  const sdk = { initialize: t.mock.fn(), renderButton: t.mock.fn() }
  const handlers = {
    onWaiting: t.mock.fn(), onNoResponse: t.mock.fn(), onCredential: t.mock.fn(async () => false),
    onRejected: t.mock.fn(),
  }
  const cleanup = renderGoogleButton(sdk, {}, challenge, handlers)
  const [config] = sdk.initialize.mock.calls[0].arguments
  await config.callback({ credential: 'test-credential' })
  assert.equal(handlers.onRejected.mock.callCount(), 1)

  const freshChallenge = { ...challenge, nonce: 'b'.repeat(64) }
  const disposeFresh = renderGoogleButton(sdk, {}, freshChallenge, { ...handlers, useFedCM: false })
  const [freshConfig] = sdk.initialize.mock.calls[1].arguments
  assert.equal(freshConfig.use_fedcm_for_button, false)
  assert.equal(freshConfig.ux_mode, 'popup')
  assert.equal(freshConfig.nonce, freshChallenge.nonce)
  disposeFresh()
  await freshConfig.callback({ credential: 'stale-credential' })
  assert.equal(handlers.onCredential.mock.callCount(), 1)
  cleanup()
})
