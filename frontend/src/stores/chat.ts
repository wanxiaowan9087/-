import { defineStore } from 'pinia'

export type PreviewState = 'ready' | 'empty' | 'loading' | 'error' | 'disabled'

export const useChatStore = defineStore('chat', {
  state: () => ({ previewState: 'ready' as PreviewState, navOpen: false }),
  actions: {
    setPreviewState(state: PreviewState) { this.previewState = state },
    toggleNav() { this.navOpen = !this.navOpen },
    closeNav() { this.navOpen = false },
  },
})
