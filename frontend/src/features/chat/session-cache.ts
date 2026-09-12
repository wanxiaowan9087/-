import type { Message, Page } from '../../api/contracts'

export type SessionMessageCache = Record<string, Message[]>
export type SessionRequestTokens = Record<string, number>

export type TranscriptVisibility = {
  currentUserMessageId: string | null
  currentRunId: string | null
  pendingUserContent: string | null
  isStreaming: boolean
}

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

export function visibleTranscriptMessages(
  messages: Message[],
  state: TranscriptVisibility,
): Message[] {
  if (!state.isStreaming) return messages.filter(message => Boolean(message.content))
  // The server persists the user message before the first SSE `meta` frame.
  // A history refresh can win that race. Until `meta` provides the durable ID,
  // hide only the latest exact pending question, not an older identical turn.
  const pendingMessageIndex = state.currentUserMessageId || !state.pendingUserContent
    ? -1
    : messages.reduce((latest, message, index) => (
      message.role === 'user' && message.content.trim() === state.pendingUserContent?.trim()
        ? index
        : latest
    ), -1)
  return messages.filter(message => (
    Boolean(message.content)
    && message.id !== state.currentUserMessageId
    && message.run_id !== state.currentRunId
    && messages.indexOf(message) !== pendingMessageIndex
  ))
}

export function hasPersistedCompletedReply(
  messages: Message[],
  runId: string | null,
): boolean {
  return Boolean(runId && messages.some(message => (
    message.run_id === runId
    && message.role === 'assistant'
    && message.status === 'completed'
    && Boolean(message.content)
  )))
}

export async function loadCompleteTranscript(
  loadPage: (cursor: string | null) => Promise<Page<Message>>,
  isCurrent: () => boolean,
): Promise<Message[] | null> {
  const messages: Message[] = []
  let cursor: string | null = null
  do {
    const page = await loadPage(cursor)
    if (!isCurrent()) return null
    messages.push(...page.items)
    cursor = page.page.next_cursor
  } while (cursor)
  return messages
}
