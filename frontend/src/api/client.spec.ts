import { describe, expect, it, vi } from 'vitest'
import { ApiClientError, createAgentApi } from './client'
import { parseSseStream } from './sse'

const encoder = new TextEncoder()

describe('SSE API client', () => {
  it('parses multi-frame POST SSE responses', async () => {
    const stream = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(encoder.encode('id: 1\nevent: status\ndata: {"event_type":"status","sequence":1}\n\n'))
        controller.enqueue(encoder.encode('id: 2\nevent: done\ndata: {"event_type":"done","sequence":2}\n\n'))
        controller.close()
      },
    })
    const frames = []
    for await (const frame of parseSseStream(stream)) frames.push(frame)
    expect(frames).toEqual([
      { id: '1', event: 'status', data: '{"event_type":"status","sequence":1}' },
      { id: '2', event: 'done', data: '{"event_type":"done","sequence":2}' },
    ])
  })

  it('sends auth, idempotency, and replay headers for chat streams', async () => {
    const fetcher = vi.fn().mockResolvedValue(new Response(
      'id: 1\nevent: done\ndata: {"event_type":"done","sequence":1,"request_id":"req","session_id":"s","run_id":"r","timestamp":"t","payload":{}}\n\n',
      { status: 200, headers: { 'Content-Type': 'text/event-stream' } },
    ))
    const api = createAgentApi({ baseUrl: '/api/v1', accessToken: 'demo:user', fetcher })
    const events: string[] = []

    await api.streamChat(
      { mode: 'new', session_id: 'session', content: 'hello' },
      { idempotencyKey: '0123456789abcdef', lastEventId: 7, onEvent: event => events.push(event.event_type) },
    )

    const [, init] = fetcher.mock.calls[0] as [string, RequestInit]
    expect(init.headers).toMatchObject({
      Authorization: 'Bearer demo:user',
      'Idempotency-Key': '0123456789abcdef',
      'Last-Event-ID': '7',
    })
    expect(events).toEqual(['done'])
  })

  it('cancels an active run through the contract endpoint', async () => {
    const fetcher = vi.fn().mockResolvedValue(new Response(JSON.stringify({
      code: 'OK', message: 'success', request_id: 'request-1',
      data: { run_id: 'run-1', status: 'cancellation_requested', requested_at: '2026-07-28T00:00:00Z' },
    }), { status: 202 }))
    const api = createAgentApi({ baseUrl: '/api/v1', accessToken: 'demo:user', fetcher })

    await expect(api.cancelRun('run-1')).resolves.toMatchObject({ status: 'cancellation_requested' })
    const [url, init] = fetcher.mock.calls[0] as [string, RequestInit]
    expect(url).toBe('/api/v1/runs/run-1/cancel')
    expect(init).toMatchObject({ method: 'POST', body: JSON.stringify({ reason: 'Cancelled by user' }) })
  })

  it('renews the browser session after a successful authenticated response', async () => {
    const fetcher = vi.fn().mockResolvedValue(new Response(JSON.stringify({
      code: 'OK', message: 'success', request_id: 'request-2',
      data: { id: 'user-1', username: 'visitor', nickname: '访客', avatar_url: '', role: 'user', created_at: '2026-08-01T00:00:00Z' },
    }), { status: 200 }))
    const onAuthenticatedResponse = vi.fn()
    const api = createAgentApi({
      baseUrl: '/api/v1', accessToken: 'real-token', fetcher, onAuthenticatedResponse,
    })

    await api.currentUser()

    expect(onAuthenticatedResponse).toHaveBeenCalledOnce()
    expect(fetcher).toHaveBeenCalledWith('/api/v1/auth/me', expect.objectContaining({
      headers: expect.objectContaining({ Authorization: 'Bearer real-token' }),
    }))
  })

  it('loads durable sessions, messages, and memories through authenticated APIs', async () => {
    const payload = { code: 'OK', message: 'success', request_id: 'request-3', data: { items: [], page: { next_cursor: null, has_more: false } } }
    const fetcher = vi.fn().mockImplementation(() => Promise.resolve(
      new Response(JSON.stringify(payload), { status: 200 }),
    ))
    const api = createAgentApi({ baseUrl: '/api/v1', accessToken: 'token', fetcher })

    await api.listSessions()
    await api.listMessages('session-1')
    await api.listMemories()

    expect(fetcher).toHaveBeenNthCalledWith(1, '/api/v1/sessions?limit=30', expect.objectContaining({
      headers: expect.objectContaining({ Authorization: 'Bearer token' }),
    }))
    expect(fetcher).toHaveBeenNthCalledWith(2, '/api/v1/sessions/session-1/messages?limit=50', expect.any(Object))
    expect(fetcher).toHaveBeenNthCalledWith(3, '/api/v1/memories?status=active&limit=20', expect.any(Object))
  })

  it('reports empty authenticated JSON responses clearly', async () => {
    const fetcher = vi.fn().mockResolvedValue(new Response('', { status: 502 }))
    const api = createAgentApi({ baseUrl: '/api/v1', accessToken: 'token', fetcher })

    await expect(api.currentUser()).rejects.toMatchObject({
      status: 502,
      code: 'HTTP_ERROR',
    } satisfies Partial<ApiClientError>)
  })

  it('uploads knowledge files through the contract endpoint', async () => {
    const fetcher = vi.fn().mockResolvedValue(new Response(JSON.stringify({
      code: 'OK', message: 'success', request_id: 'request-4',
      data: {
        id: 'doc-1',
        filename: 'guide.md',
        title: 'guide',
        source: 'file://uploads/knowledge/guide.md',
        size_bytes: 12,
        chunk_count: 1,
        uploaded_at: '2026-08-02T00:00:00Z',
      },
    }), { status: 201 }))
    const api = createAgentApi({ baseUrl: '/api/v1', accessToken: 'token', fetcher })
    const file = new File(['# guide'], 'guide.md', { type: 'text/markdown' })

    await expect(api.uploadKnowledgeFile(file)).resolves.toMatchObject({ filename: 'guide.md' })
    expect(fetcher).toHaveBeenCalledWith(
      '/api/v1/knowledge/files?filename=guide.md',
      expect.objectContaining({ method: 'POST', body: file }),
    )
  })

  it('lists and reindexes knowledge files through admin endpoints', async () => {
    const fetcher = vi.fn()
      .mockResolvedValueOnce(new Response(JSON.stringify({
        code: 'OK', message: 'success', request_id: 'request-5',
        data: { items: [], page: { next_cursor: null, has_more: false } },
      }), { status: 200 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({
        code: 'OK', message: 'success', request_id: 'request-6',
        data: { chunks_indexed: 3 },
      }), { status: 200 }))
    const api = createAgentApi({ baseUrl: '/api/v1', accessToken: 'admin-token', fetcher })

    await expect(api.listKnowledgeFiles()).resolves.toMatchObject({ items: [] })
    await expect(api.reindexKnowledgeFiles()).resolves.toEqual({ chunks_indexed: 3 })
    expect(fetcher.mock.calls[0][0]).toBe('/api/v1/knowledge/files')
    expect(fetcher.mock.calls[1][0]).toBe('/api/v1/knowledge/reindex')
  })
})
