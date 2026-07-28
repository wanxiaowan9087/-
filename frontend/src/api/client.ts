import type { ApiErrorBody, ChatRequest, Envelope, Session, StreamEvent } from './contracts'
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
  accessToken: string
  fetcher?: typeof fetch
}

export interface StreamOptions {
  idempotencyKey: string
  lastEventId?: number
  onEvent: (event: StreamEvent) => void
}

function isSuccessEnvelope<T>(body: Envelope<T> | ApiErrorBody): body is Envelope<T> {
  return body.code === 'OK' && body.message === 'success' && 'data' in body
}

export function createAgentApi(options: AgentApiOptions) {
  const fetcher = options.fetcher ?? fetch
  const headers = (extra: HeadersInit = {}) => ({
    Authorization: `Bearer ${options.accessToken}`,
    Accept: 'application/json',
    ...extra,
  })

  async function readJson<T>(response: Response): Promise<T> {
    const body = await response.json() as Envelope<T> | ApiErrorBody
    if (!response.ok || !isSuccessEnvelope<T>(body)) {
      const error = body as ApiErrorBody
      throw new ApiClientError(error.message || 'Request failed', response.status, error.code)
    }
    return body.data
  }

  return {
    async createSession(title?: string): Promise<Session> {
      const response = await fetcher(`${options.baseUrl}/sessions`, {
        method: 'POST',
        headers: headers({ 'Content-Type': 'application/json', 'Idempotency-Key': crypto.randomUUID() }),
        body: JSON.stringify({ title }),
      })
      return readJson<Session>(response)
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
      })
      if (!response.ok) await readJson<never>(response)
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
  }
}
