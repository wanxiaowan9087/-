import { beforeEach, describe, expect, it } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { useChatStore } from './chat'

const streamEvent = (eventType: 'meta' | 'status' | 'tool_start' | 'tool_end' | 'delta' | 'citation' | 'product_recommendation' | 'review_required' | 'done') => ({
  event_type: eventType,
  sequence: 1,
  request_id: 'request-1',
  session_id: 'session-1',
  run_id: 'run-1',
  timestamp: '2026-01-01T00:00:00Z',
  payload: {},
})

describe('chat preview state', () => {
  beforeEach(() => setActivePinia(createPinia()))
  it('switches preview states without network access', () => {
    const store = useChatStore()
    store.setPreviewState('error')
    expect(store.previewState).toBe('error')
  })

  it('accumulates answer text and deduplicates citations from SSE events', () => {
    const store = useChatStore()
    store.beginRun('session-1')
    store.receiveStreamEvent({ ...streamEvent('delta'), payload: { content: 'First ' } })
    store.receiveStreamEvent({ ...streamEvent('delta'), sequence: 2, payload: { content: 'answer' } })
    const citation = {
      document_id: 'guide', document_version: 'v3', chunk_id: 'chunk-12', title: 'Maintenance guide', source: 'kb://maintenance', page: 3,
    }
    store.receiveStreamEvent({ ...streamEvent('citation'), sequence: 3, payload: citation })
    store.receiveStreamEvent({ ...streamEvent('citation'), sequence: 4, payload: citation })

    expect(store.assistantText).toBe('First answer')
    expect(store.citations).toEqual([{ documentId: 'guide', documentVersion: 'v3', chunkId: 'chunk-12', title: 'Maintenance guide', locator: 'kb://maintenance · page 3' }])
  })

  it('withholds candidate content when the runtime requires review', () => {
    const store = useChatStore()
    store.beginRun('session-1')
    store.receiveStreamEvent({
      ...streamEvent('review_required'),
      payload: { review_id: 'review-1', reason_codes: ['POLICY_R04'], confidence: 0.86 },
    })
    store.receiveStreamEvent(streamEvent('done'))

    expect(store.previewState).toBe('disabled')
    expect(store.review).toEqual({ reviewId: 'review-1', reasonCodes: ['POLICY_R04'], confidence: 0.86 })
  })

  it('keeps the original user message ID for a contract-compliant retry', () => {
    const store = useChatStore()
    store.beginRun('session-1')
    store.receiveStreamEvent({
      ...streamEvent('delta'),
      event_type: 'meta',
      payload: { user_message_id: 'message-1' },
    })

    expect(store.userMessageId).toBe('message-1')
  })

  it('consumes status and tool events, de-duplicates replay, and ignores late events after completion', () => {
    const store = useChatStore()
    store.beginRun('session-1')
    store.receiveStreamEvent({ ...streamEvent('meta'), payload: { user_message_id: 'message-1' } })
    store.receiveStreamEvent({ ...streamEvent('status'), sequence: 2, event_type: 'status', payload: { phase: 'calling_tool' } })
    store.receiveStreamEvent({ ...streamEvent('tool_start'), sequence: 3, event_type: 'tool_start', payload: { tool_call_id: 'tool-1', tool_name: 'search', critical: false } })
    store.receiveStreamEvent({ ...streamEvent('tool_end'), sequence: 4, event_type: 'tool_end', payload: { tool_call_id: 'tool-1', outcome: 'succeeded', result_preview: 'one result' } })
    store.receiveStreamEvent({ ...streamEvent('delta'), sequence: 5, payload: { content: 'answer' } })
    store.receiveStreamEvent({ ...streamEvent('delta'), sequence: 5, payload: { content: 'answer' } })
    store.receiveStreamEvent({ ...streamEvent('done'), sequence: 6, payload: { outcome: 'completed' } })
    store.receiveStreamEvent({ ...streamEvent('delta'), sequence: 7, payload: { content: ' late' } })

    expect(store.lastStatus).toBe('calling_tool')
    expect(store.tools).toEqual([{ toolCallId: 'tool-1', toolName: 'search', outcome: 'succeeded', detail: 'one result' }])
    expect(store.assistantText).toBe('answer')
    expect(store.terminal).toBe(true)
  })

  it('collects distinct product recommendations from the stream', () => {
    const store = useChatStore()
    store.beginRun('session-1')
    store.receiveStreamEvent({ ...streamEvent('product_recommendation'), payload: { product_id: 'x9-edge', reason: '适合墙角较多的户型', score: 0.91 } })
    store.receiveStreamEvent({ ...streamEvent('product_recommendation'), sequence: 2, payload: { product_id: 'x9-edge', reason: 'duplicate' } })

    expect(store.productRecommendations).toEqual([{
      productId: 'x9-edge',
      name: null,
      price: null,
      highlights: [],
      reason: '适合墙角较多的户型',
      score: 0.91,
    }])
  })
})
