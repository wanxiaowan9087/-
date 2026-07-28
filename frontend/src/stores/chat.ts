import { defineStore } from 'pinia'
import type { StreamEvent } from '../api/contracts'

export type PreviewState = 'ready' | 'empty' | 'loading' | 'error' | 'disabled'

export interface CitationView {
  documentId: string
  documentVersion: string
  chunkId: string
  title: string
  locator: string
}

export interface ReviewView {
  reviewId: string | null
  reasonCodes: string[]
  confidence: number | null
}

function readText(payload: Record<string, unknown>, key: string): string | null {
  const value = payload[key]
  return typeof value === 'string' && value.trim() ? value : null
}

function readStringList(payload: Record<string, unknown>, key: string): string[] {
  const value = payload[key]
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === 'string') : []
}

function readNumber(payload: Record<string, unknown>, key: string): number | null {
  const value = payload[key]
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}

export const useChatStore = defineStore('chat', {
  state: () => ({
    previewState: 'ready' as PreviewState,
    navOpen: false,
    sessionId: null as string | null,
    runId: null as string | null,
    userMessageId: null as string | null,
    lastEventId: null as number | null,
    assistantText: '',
    citations: [] as CitationView[],
    review: null as ReviewView | null,
    errorMessage: null as string | null,
    runOutcome: null as 'completed' | 'needs_review' | 'cancelled' | null,
  }),
  actions: {
    setPreviewState(state: PreviewState) { this.previewState = state },
    toggleNav() { this.navOpen = !this.navOpen },
    closeNav() { this.navOpen = false },
    beginRun(sessionId: string) {
      this.sessionId = sessionId
      this.runId = null
      this.userMessageId = null
      this.lastEventId = null
      this.assistantText = ''
      this.citations = []
      this.review = null
      this.errorMessage = null
      this.runOutcome = null
      this.previewState = 'loading'
    },
    receiveStreamEvent(event: StreamEvent) {
      this.lastEventId = event.sequence
      this.runId = event.run_id
      if (event.event_type === 'meta') {
        this.userMessageId = readText(event.payload, 'user_message_id')
      }
      if (event.event_type === 'delta') {
        this.assistantText += readText(event.payload, 'content') ?? ''
      }
      if (event.event_type === 'citation') {
        const documentId = readText(event.payload, 'document_id')
        const documentVersion = readText(event.payload, 'document_version')
        const chunkId = readText(event.payload, 'chunk_id')
        if (documentId && documentVersion && chunkId) {
          const citation = {
            documentId,
            documentVersion,
            chunkId,
            title: readText(event.payload, 'title') ?? documentId,
            locator: readText(event.payload, 'locator') ?? chunkId,
          }
          if (!this.citations.some((item) => item.chunkId === citation.chunkId && item.documentVersion === citation.documentVersion)) {
            this.citations.push(citation)
          }
        }
      }
      if (event.event_type === 'review_required') {
        this.review = {
          reviewId: readText(event.payload, 'review_id'),
          reasonCodes: readStringList(event.payload, 'reason_codes'),
          confidence: readNumber(event.payload, 'confidence'),
        }
        this.previewState = 'disabled'
      }
      if (event.event_type === 'done') {
        const outcome = readText(event.payload, 'outcome')
        this.runOutcome = outcome === 'completed' || outcome === 'needs_review' || outcome === 'cancelled'
          ? outcome
          : null
        this.previewState = this.review ? 'disabled' : 'ready'
      }
      if (event.event_type === 'error') {
        this.errorMessage = readText(event.payload, 'message')
        this.previewState = 'error'
      }
    },
    markCancellationRequested() {
      this.runOutcome = 'cancelled'
      this.previewState = 'ready'
    },
  },
})
