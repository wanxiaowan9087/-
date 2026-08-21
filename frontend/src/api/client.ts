import type { ApiErrorBody, AuthSession, AuthUser, ChatRequest, Envelope, KnowledgeFile, LegalDocument, Memory, Message, Page, Session, SmsPurpose, StreamEvent, UsageEvent, UsageSummary } from './contracts'
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
    const raw = await response.text()
    if (!raw.trim()) {
      throw new ApiClientError(
        response.ok ? 'Response body is empty' : `Request failed with status ${response.status}`,
        response.status,
        response.ok ? 'EMPTY_RESPONSE' : 'HTTP_ERROR',
      )
    }
    let body: Envelope<T> | ApiErrorBody
    try {
      body = JSON.parse(raw) as Envelope<T> | ApiErrorBody
    } catch {
      throw new ApiClientError('Response body is not valid JSON', response.status, 'INVALID_JSON')
    }
    if (!response.ok || !isSuccessEnvelope<T>(body)) {
      const error = body as ApiErrorBody
      throw new ApiClientError(error.message || 'Request failed', response.status, error.code)
    }
    if (authenticated) options.onAuthenticatedResponse?.()
    return body.data
  }

  return {
    async sendSmsCode(input: { phone: string; purpose: SmsPurpose }): Promise<{ accepted: boolean; retry_after_seconds: number }> {
      const response = await fetcher(`${options.baseUrl}/auth/sms-codes`, {
        method: 'POST',
        headers: { Accept: 'application/json', 'Content-Type': 'application/json' },
        body: JSON.stringify(input),
      })
      return readJson<{ accepted: boolean; retry_after_seconds: number }>(response)
    },

    async register(input: {
      phone: string
      password: string
      nickname: string
      verification_code: string
      user_agreement_version: string
      privacy_policy_version: string
      agree_user_agreement: true
      agree_privacy_policy: true
      avatar_url?: string
    }): Promise<AuthSession> {
      const response = await fetcher(`${options.baseUrl}/auth/register`, {
        method: 'POST',
        headers: { Accept: 'application/json', 'Content-Type': 'application/json' },
        body: JSON.stringify(input),
      })
      return readJson<AuthSession>(response)
    },

    async login(input: { phone: string; password: string }): Promise<AuthSession> {
      const response = await fetcher(`${options.baseUrl}/auth/login`, {
        method: 'POST',
        headers: { Accept: 'application/json', 'Content-Type': 'application/json' },
        body: JSON.stringify(input),
      })
      return readJson<AuthSession>(response)
    },

    async resetPassword(input: { phone: string; verification_code: string; new_password: string }): Promise<{ reset: boolean }> {
      const response = await fetcher(`${options.baseUrl}/auth/password-resets`, {
        method: 'POST',
        headers: { Accept: 'application/json', 'Content-Type': 'application/json' },
        body: JSON.stringify(input),
      })
      return readJson<{ reset: boolean }>(response)
    },

    async getLegalDocument(type: 'user-agreement' | 'privacy-policy'): Promise<LegalDocument> {
      const response = await fetcher(`${options.baseUrl}/legal/${type}`, {
        headers: { Accept: 'application/json' },
      })
      return readJson<LegalDocument>(response)
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

    async uploadKnowledgeFile(file: File): Promise<KnowledgeFile> {
      const response = await fetcher(
        `${options.baseUrl}/knowledge/files?filename=${encodeURIComponent(file.name)}`,
        {
          method: 'POST',
          headers: headers({ 'Content-Type': file.type || 'text/plain' }),
          body: file,
        },
      )
      return readJson<KnowledgeFile>(response, true)
    },

    async listKnowledgeFiles(): Promise<Page<KnowledgeFile>> {
      const response = await fetcher(`${options.baseUrl}/knowledge/files`, { headers: headers() })
      return readJson<Page<KnowledgeFile>>(response, true)
    },

    async reindexKnowledgeFiles(): Promise<{ chunks_indexed: number }> {
      const response = await fetcher(`${options.baseUrl}/knowledge/reindex`, {
        method: 'POST',
        headers: headers({ 'Content-Type': 'application/json' }),
      })
      return readJson<{ chunks_indexed: number }>(response, true)
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

    async getUsageSummary(): Promise<UsageSummary> {
      const response = await fetcher(`${options.baseUrl}/me/usage-summary`, { headers: headers() })
      return readJson<UsageSummary>(response, true)
    },

    async recordUsageEvent(input: { event_type: 'product_detail_viewed' | 'product_3d_viewed'; product_id: string; model_code?: string | null }): Promise<UsageEvent> {
      const response = await fetcher(`${options.baseUrl}/me/usage-events`, {
        method: 'POST',
        headers: headers({ 'Content-Type': 'application/json', 'Idempotency-Key': crypto.randomUUID() }),
        body: JSON.stringify(input),
      })
      return readJson<UsageEvent>(response, true)
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
      let terminalEventReceived = false
      for await (const frame of parseSseStream(response.body)) {
        let event: StreamEvent
        try {
          event = JSON.parse(frame.data) as StreamEvent
        } catch {
          throw new ApiClientError('Invalid SSE event payload', response.status, 'INVALID_STREAM')
        }
        stream.onEvent(event)
        if (event.event_type === 'done' || event.event_type === 'error') terminalEventReceived = true
      }
      if (!terminalEventReceived) {
        throw new ApiClientError('Stream ended before a terminal event', response.status, 'STREAM_INCOMPLETE')
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
