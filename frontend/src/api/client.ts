import type { ApiErrorBody, AuthSession, AuthUser, ChatRequest, Envelope, Memory, Message, Page, Session, StreamEvent } from './contracts'
import { parseSseStream } from './sse'

export class ApiClientError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly code: string = 'NETWORK_ERROR',
  ) {
    super(message)
  }
}

export interface AgentApiOptions {
  baseUrl: string
  accessToken: string | (() => string)
  fetcher?: typeof fetch
  onAuthenticatedResponse?: () => void
}

export interface StreamOptions {
  idempotencyKey: string
  lastEventId?: number
  signal?: AbortSignal
  onEvent: (event: StreamEvent) => void
}

export interface CancelRunResult {
  run_id: string
  status: 'cancellation_requested' | 'already_terminal'
  requested_at: string
}

function isSuccessEnvelope<T>(body: Envelope<T> | ApiErrorBody): body is Envelope<T> {
  return body.code === 'OK' && body.message === 'success' && 'data' in body
}

export function createAgentApi(options: AgentApiOptions) {
  const fetcher = options.fetcher ?? fetch
  const headers = (extra: HeadersInit = {}) => ({
    Authorization: `Bearer ${typeof options.accessToken === 'function' ? options.accessToken() : options.accessToken}`,
    Accept: 'application/json',
    ...extra,
  })

  async function readJson<T>(response: Response, authenticated = false): Promise<T> {
    const body = await response.json() as Envelope<T> | ApiErrorBody
    if (!response.ok || !isSuccessEnvelope<T>(body)) {
      const error = body as ApiErrorBody
      throw new ApiClientError(error.message || 'Request failed', response.status, error.code)
    }
    if (authenticated) options.onAuthenticatedResponse?.()
    return body.data
  }

  return {
    async register(input: { username: string; password: string; nickname: string; avatar_url?: string }): Promise<AuthSession> {
      const response = await fetcher(`${options.baseUrl}/auth/register`, {
        method: 'POST',
        headers: { Accept: 'application/json', 'Content-Type': 'application/json' },
        body: JSON.stringify(input),
      })
      return readJson<AuthSession>(response)
    },

    async login(input: { username: string; password: string }): Promise<AuthSession> {
      const response = await fetcher(`${options.baseUrl}/auth/login`, {
        method: 'POST',
        headers: { Accept: 'application/json', 'Content-Type': 'application/json' },
        body: JSON.stringify(input),
      })
      return readJson<AuthSession>(response)
    },

    async currentUser(): Promise<AuthUser> {
      const response = await fetcher(`${options.baseUrl}/auth/me`, { headers: headers() })
      return readJson<AuthUser>(response, true)
    },

    async updateProfile(input: { nickname?: string; avatar_url?: string }): Promise<AuthUser> {
      const response = await fetcher(`${options.baseUrl}/auth/me`, {
        method: 'PATCH',
        headers: headers({ 'Content-Type': 'application/json' }),
        body: JSON.stringify(input),
      })
      return readJson<AuthUser>(response, true)
    },

    async uploadAvatar(file: File): Promise<AuthUser> {
      const response = await fetcher(`${options.baseUrl}/auth/me/avatar`, {
        method: 'PUT',
        headers: headers({ 'Content-Type': file.type }),
        body: file,
      })
      return readJson<AuthUser>(response, true)
    },

    async changePassword(input: { current_password: string; new_password: string }): Promise<AuthUser> {
      const response = await fetcher(`${options.baseUrl}/auth/me/password`, {
        method: 'PATCH',
        headers: headers({ 'Content-Type': 'application/json' }),
        body: JSON.stringify(input),
      })
      return readJson<AuthUser>(response, true)
    },

    async createSession(title?: string): Promise<Session> {
      const response = await fetcher(`${options.baseUrl}/sessions`, {
        method: 'POST',
        headers: headers({ 'Content-Type': 'application/json', 'Idempotency-Key': crypto.randomUUID() }),
        body: JSON.stringify({ title }),
      })
      return readJson<Session>(response, true)
    },

    async listSessions(limit = 30): Promise<Page<Session>> {
      const response = await fetcher(`${options.baseUrl}/sessions?limit=${limit}`, {
        headers: headers(),
      })
      return readJson<Page<Session>>(response, true)
    },

    async listMessages(sessionId: string, limit = 50): Promise<Page<Message>> {
      const response = await fetcher(`${options.baseUrl}/sessions/${sessionId}/messages?limit=${limit}`, {
        headers: headers(),
      })
      return readJson<Page<Message>>(response, true)
    },

    async listMemories(limit = 20): Promise<Page<Memory>> {
      const response = await fetcher(`${options.baseUrl}/memories?status=active&limit=${limit}`, {
        headers: headers(),
      })
      return readJson<Page<Memory>>(response, true)
    },

    async streamChat(request: ChatRequest, stream: StreamOptions): Promise<void> {
      const response = await fetcher(`${options.baseUrl}/chat/stream`, {
        method: 'POST',
        headers: headers({
          'Content-Type': 'application/json',
          Accept: 'text/event-stream',
          'Idempotency-Key': stream.idempotencyKey,
          ...(stream.lastEventId === undefined ? {} : { 'Last-Event-ID': String(stream.lastEventId) }),
        }),
        body: JSON.stringify(request),
        signal: stream.signal,
      })
      if (!response.ok) await readJson<never>(response)
      options.onAuthenticatedResponse?.()
      if (!response.body) throw new ApiClientError('Stream body is missing', response.status)
      for await (const frame of parseSseStream(response.body)) {
        let event: StreamEvent
        try {
          event = JSON.parse(frame.data) as StreamEvent
        } catch {
          throw new ApiClientError('Invalid SSE event payload', response.status, 'INVALID_STREAM')
        }
        stream.onEvent(event)
      }
    },

    async cancelRun(runId: string, reason = 'Cancelled by user'): Promise<CancelRunResult> {
      const response = await fetcher(`${options.baseUrl}/runs/${runId}/cancel`, {
        method: 'POST',
        headers: headers({
          'Content-Type': 'application/json',
          'Idempotency-Key': crypto.randomUUID(),
        }),
        body: JSON.stringify({ reason }),
      })
      return readJson<CancelRunResult>(response, true)
    },
  }
}
