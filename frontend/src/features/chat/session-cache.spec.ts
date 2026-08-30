import { describe, expect, it, vi } from 'vitest'
import type { Message } from '../../api/contracts'
import {
  commitSessionMessages,
  hasPersistedCompletedReply,
  loadCompleteTranscript,
  visibleTranscriptMessages,
} from './session-cache'

const message = (id: string) => ({ id, content: id } as Message)

describe('session message cache', () => {
  it('keeps the active session transcript when a stale request completes', () => {
    const original = { second: [message('second-message')] }
    const result = commitSessionMessages(original, 'first', 1, 2, [message('first-message')])

    expect(result).toBe(original)
    expect(result.second[0].id).toBe('second-message')
  })

  it('stores a response only for the currently selected session', () => {
    const result = commitSessionMessages({}, 'first', 3, 3, [message('first-message')])

    expect(result.first[0].id).toBe('first-message')
  })

  it('caches two session responses independently when they resolve out of order', () => {
    const first = commitSessionMessages({}, 'first', 1, 1, [message('first-message')])
    const result = commitSessionMessages(first, 'second', 1, 1, [message('second-message')])

    expect(result.first[0].id).toBe('first-message')
    expect(result.second[0].id).toBe('second-message')
  })

  it('keeps a completed current run in the durable transcript', () => {
    const messages: Message[] = [
      { ...message('user-message'), role: 'user', run_id: 'run-1' },
      { ...message('assistant-message'), role: 'assistant', run_id: 'run-1' },
    ]

    expect(visibleTranscriptMessages(messages, {
      currentUserMessageId: 'user-message', currentRunId: 'run-1', isStreaming: false,
    })).toEqual(messages)
  })

  it('hides only the in-flight durable pair while the stream renders it separately', () => {
    const messages: Message[] = [
      { ...message('previous'), role: 'assistant', run_id: 'run-0' },
      { ...message('user-message'), role: 'user', run_id: 'run-1' },
      { ...message('assistant-message'), role: 'assistant', run_id: 'run-1' },
    ]

    expect(visibleTranscriptMessages(messages, {
      currentUserMessageId: 'user-message', currentRunId: 'run-1', isStreaming: true,
    }).map(item => item.id)).toEqual(['previous'])
  })

  it('detects when a streamed answer is safe to replace with its durable transcript', () => {
    const messages: Message[] = [
      { ...message('user-message'), role: 'user', run_id: 'run-1' },
      { ...message('assistant-message'), role: 'assistant', status: 'completed', run_id: 'run-1' },
    ]

    expect(hasPersistedCompletedReply(messages, 'run-1')).toBe(true)
    expect(hasPersistedCompletedReply(messages, 'run-2')).toBe(false)
  })

  it('loads every history page before replacing a transcript cache entry', async () => {
    const loadPage = vi.fn()
      .mockResolvedValueOnce({ items: [message('first')], page: { next_cursor: 'second-page', has_more: true } })
      .mockResolvedValueOnce({ items: [message('second')], page: { next_cursor: null, has_more: false } })

    await expect(loadCompleteTranscript(loadPage, () => true)).resolves.toEqual([
      message('first'),
      message('second'),
    ])
    expect(loadPage).toHaveBeenNthCalledWith(1, null)
    expect(loadPage).toHaveBeenNthCalledWith(2, 'second-page')
  })
})
