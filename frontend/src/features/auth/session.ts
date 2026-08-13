import type { AuthSession, AuthUser } from '../../api/contracts'

export const AUTH_SESSION_TTL_MS = 7 * 24 * 60 * 60 * 1000

const ACCESS_TOKEN_KEY = 'agent.access-token'
const EXPIRES_AT_KEY = 'agent.access-token-expires-at'
const AUTH_USER_KEY = 'agent.auth-user'

type SessionStorage = Pick<Storage, 'getItem' | 'setItem' | 'removeItem'>

export function loadStoredAccessToken(
  storage: SessionStorage = globalThis.localStorage,
  now = Date.now(),
): string {
  const token = storage.getItem(ACCESS_TOKEN_KEY) ?? ''
  const expiresAt = Number(storage.getItem(EXPIRES_AT_KEY) ?? 0)
  if (!token || !Number.isFinite(expiresAt) || expiresAt <= now) {
    clearStoredAuthSession(storage)
    return ''
  }
  return token
}

export function persistAuthSession(
  session: AuthSession,
  storage: SessionStorage = globalThis.localStorage,
  now = Date.now(),
): void {
  const serverExpiry = Date.parse(session.expires_at)
  const expiresAt = Number.isFinite(serverExpiry) ? serverExpiry : now + AUTH_SESSION_TTL_MS
  storage.setItem(ACCESS_TOKEN_KEY, session.access_token)
  storage.setItem(EXPIRES_AT_KEY, String(expiresAt))
  storage.setItem(AUTH_USER_KEY, JSON.stringify(session.user))
}

export function loadStoredAuthUser(
  storage: SessionStorage = globalThis.localStorage,
): AuthUser | null {
  const raw = storage.getItem(AUTH_USER_KEY)
  if (!raw) return null
  try {
    return JSON.parse(raw) as AuthUser
  } catch {
    storage.removeItem(AUTH_USER_KEY)
    return null
  }
}

export function renewStoredAuthSession(
  token: string,
  storage: SessionStorage = globalThis.localStorage,
  now = Date.now(),
): void {
  if (!token) return
  storage.setItem(ACCESS_TOKEN_KEY, token)
  storage.setItem(EXPIRES_AT_KEY, String(now + AUTH_SESSION_TTL_MS))
}

export function clearStoredAuthSession(
  storage: SessionStorage = globalThis.localStorage,
): void {
  storage.removeItem(ACCESS_TOKEN_KEY)
  storage.removeItem(EXPIRES_AT_KEY)
  storage.removeItem(AUTH_USER_KEY)
}
