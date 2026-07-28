import { defineStore } from 'pinia'

export type PreviewState = 'ready' | 'empty' | 'loading' | 'error' | 'disabled'

export const useChatStore = defineStore('chat', {
  state: () => ({
    previewState: 'ready' as PreviewState,
    navOpen: false,
    sessionId: null as string | null,
    runId: null as string | null,
    lastEventId: null as number | null,
  }),
  actions: {
    setPreviewState(state: PreviewState) { this.previewState = state },
    toggleNav() { this.navOpen = !this.navOpen },
    closeNav() { this.navOpen = false },
    beginRun(sessionId: string) {
      this.sessionId = sessionId
      this.runId = null
      this.lastEventId = null
      this.previewState = 'loading'
    },
    receiveStreamEvent(event: { sequence: number; run_id: string; event_type: string }) {
      this.lastEventId = event.sequence
      this.runId = event.run_id
      if (event.event_type === 'done') this.previewState = 'ready'
      if (event.event_type === 'error') this.previewState = 'error'
      if (event.event_type === 'review_required') this.previewState = 'disabled'
    },
  },
})
