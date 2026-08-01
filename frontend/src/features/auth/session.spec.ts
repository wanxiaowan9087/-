import { describe, expect, it } from 'vitest'
import {
  AUTH_SESSION_TTL_MS,
  clearStoredAuthSession,
  loadStoredAccessToken,
  persistAuthSession,
  renewStoredAuthSession,
} from './session'

function createStorage() {
  const values = new Map<string, string>()
  return {
    getItem: (key: string) => values.get(key) ?? null,
    setItem: (key: string, value: string) => values.set(key, value),
    removeItem: (key: string) => values.delete(key),
    values,
  }
}

describe('browser authentication session', () => {
  it('persists the backend token and honours its issued expiration', () => {
    const storage = createStorage()
    persistAuthSession({
      access_token: 'token-1',
      token_type: 'Bearer',
      expires_at: '2026-08-08T08:00:00Z',
      user: {
        id: 'user-1', username: 'visitor', nickname: '访客', avatar_url: '', role: 'user', created_at: '2026-08-01T08:00:00Z',
      },
    }, storage, Date.parse('2026-08-01T08:00:00Z'))

    expect(loadStoredAccessToken(storage, Date.parse('2026-08-08T07:59:59Z'))).toBe('token-1')
    expect(loadStoredAccessToken(storage, Date.parse('2026-08-08T08:00:00Z'))).toBe('')
  })

  it('renews a valid browser session for seven days after authenticated use', () => {
    const storage = createStorage()
    renewStoredAuthSession('token-2', storage, 1_000)

    expect(storage.values.get('agent.access-token-expires-at')).toBe(String(1_000 + AUTH_SESSION_TTL_MS))
    expect(loadStoredAccessToken(storage, 1_000 + AUTH_SESSION_TTL_MS - 1)).toBe('token-2')
  })

  it('clears both token fields on logout', () => {
    const storage = createStorage()
    renewStoredAuthSession('token-3', storage, 1_000)
    clearStoredAuthSession(storage)
    expect(storage.values.size).toBe(0)
  })
})
