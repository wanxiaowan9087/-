import { describe, expect, it } from 'vitest'
import type { Message } from '../../api/contracts'
import { commitSessionMessages } from './session-cache'

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
})
