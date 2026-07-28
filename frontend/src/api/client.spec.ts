import { describe, expect, it, vi } from 'vitest'
import { createAgentApi } from './client'
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
})
