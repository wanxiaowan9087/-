import { describe, expect, it } from 'vitest'
import type { Message } from '../../api/contracts'
import { commitSessionMessages } from './session-cache'

const message = (id: string) => ({ id, content: id } as Message)

describe('session message cache', () => {
  it('keeps the active session transcript when a stale request completes', () => {
    const original = { second: [message('second-message')] }
    const result = commitSessionMessages(original, 'second', 'first', 1, 2, [message('first-message')])

    expect(result).toBe(original)
    expect(result.second[0].id).toBe('second-message')
  })

  it('stores a response only for the currently selected session', () => {
    const result = commitSessionMessages({}, 'first', 'first', 3, 3, [message('first-message')])

    expect(result.first[0].id).toBe('first-message')
  })
})
