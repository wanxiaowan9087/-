import type { Message } from '../../api/contracts'

export type SessionMessageCache = Record<string, Message[]>
export type SessionRequestTokens = Record<string, number>

export function commitSessionMessages(
  cache: SessionMessageCache,
  responseSessionId: string,
  requestVersion: number,
  currentSessionVersion: number,
  messages: Message[],
): SessionMessageCache {
  if (requestVersion !== currentSessionVersion) return cache
  return { ...cache, [responseSessionId]: messages }
}
