import { defineStore } from 'pinia'
import type { ProductRecommendation, StreamEvent } from '../api/contracts'

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

export interface ToolView {
  toolCallId: string
  toolName: string
  outcome: 'running' | 'succeeded' | 'failed' | 'timeout' | 'cancelled'
  detail: string | null
}

export interface ProductRecommendationView {
  productId: string
  name: string | null
  price: number | null
  highlights: string[]
  reason: string | null
  score: number | null
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

function readProductRecommendation(payload: Record<string, unknown>): ProductRecommendationView | null {
  const productId = readText(payload, 'product_id')
  if (!productId) return null
  const highlights = Array.isArray(payload.highlights) ? payload.highlights.filter((item): item is string => typeof item === 'string') : []
  return { productId, name: readText(payload, 'name'), price: readNumber(payload, 'price'), highlights, reason: readText(payload, 'reason'), score: readNumber(payload, 'score') }
}

export function toProductRecommendationView(item: ProductRecommendation): ProductRecommendationView {
  return { productId: item.product_id, name: item.name ?? null, price: item.price ?? null, highlights: item.highlights ?? [], reason: item.reason ?? null, score: item.score ?? null }
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
    productRecommendations: [] as ProductRecommendationView[],
    review: null as ReviewView | null,
    errorMessage: null as string | null,
    runOutcome: null as 'completed' | 'needs_review' | 'cancelled' | null,
    lastStatus: null as string | null,
    tools: [] as ToolView[],
    processedEventKeys: [] as string[],
    terminal: false,
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
      this.productRecommendations = []
      this.review = null
      this.errorMessage = null
      this.runOutcome = null
      this.lastStatus = null
      this.tools = []
      this.processedEventKeys = []
      this.terminal = false
      this.previewState = 'loading'
    },
    receiveStreamEvent(event: StreamEvent) {
      if (this.runId && event.run_id !== this.runId) return
      const eventKey = `${event.run_id}:${event.sequence}`
      if (this.terminal || this.processedEventKeys.includes(eventKey)) return
      this.processedEventKeys.push(eventKey)
      this.lastEventId = event.sequence
      this.runId = event.run_id
      if (event.event_type === 'meta') {
        this.userMessageId = readText(event.payload, 'user_message_id')
      }
      if (event.event_type === 'status') {
        this.lastStatus = readText(event.payload, 'phase')
      }
      if (event.event_type === 'tool_start') {
        const toolCallId = readText(event.payload, 'tool_call_id')
        const toolName = readText(event.payload, 'tool_name')
        if (toolCallId && toolName) {
          this.tools.push({ toolCallId, toolName, outcome: 'running', detail: null })
        }
      }
      if (event.event_type === 'tool_end') {
        const toolCallId = readText(event.payload, 'tool_call_id')
        const outcome = readText(event.payload, 'outcome')
        const tool = this.tools.find((item) => item.toolCallId === toolCallId)
        if (tool && (outcome === 'succeeded' || outcome === 'failed' || outcome === 'timeout' || outcome === 'cancelled')) {
          tool.outcome = outcome
          tool.detail = readText(event.payload, 'result_preview') ?? readText(event.payload, 'error_code')
        }
      }
      if (event.event_type === 'delta') {
        this.assistantText += readText(event.payload, 'content') ?? ''
      }
      if (event.event_type === 'citation') {
        const documentId = readText(event.payload, 'document_id')
        const documentVersion = readText(event.payload, 'document_version')
        const chunkId = readText(event.payload, 'chunk_id')
        if (documentId && documentVersion && chunkId) {
          const page = readNumber(event.payload, 'page')
          const citation = {
            documentId,
            documentVersion,
            chunkId,
            title: readText(event.payload, 'title') ?? documentId,
            // Backend source locations are retained for traceability, but must not expose local paths to users.
            locator: page === null ? '知识库资料' : `第 ${page} 页`,
          }
          if (!this.citations.some((item) => item.documentId === citation.documentId)) {
            this.citations.push(citation)
          }
        }
      }
      if (event.event_type === 'product_recommendation') {
        const recommendation = readProductRecommendation(event.payload)
        if (recommendation && !this.productRecommendations.some(item => item.productId === recommendation.productId)) {
          this.productRecommendations.push(recommendation)
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
        this.terminal = true
      }
      if (event.event_type === 'error') {
        this.errorMessage = readText(event.payload, 'message')
        this.previewState = 'error'
        this.terminal = true
      }
    },
    markCancellationRequested() {
      this.runOutcome = 'cancelled'
      this.previewState = 'ready'
      this.terminal = true
    },
  },
})
