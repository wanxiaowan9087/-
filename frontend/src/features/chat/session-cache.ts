import type { Message } from '../../api/contracts'

export type SessionMessageCache = Record<string, Message[]>

export function commitSessionMessages(
  cache: SessionMessageCache,
  activeSessionId: string | null,
  responseSessionId: string,
  requestVersion: number,
  currentVersion: number,
  messages: Message[],
): SessionMessageCache {
  if (requestVersion !== currentVersion || activeSessionId !== responseSessionId) return cache
  return { ...cache, [responseSessionId]: messages }
}
