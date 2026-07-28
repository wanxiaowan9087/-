import { beforeEach, describe, expect, it } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { useChatStore } from './chat'

describe('chat preview state', () => {
  beforeEach(() => setActivePinia(createPinia()))
  it('switches preview states without network access', () => {
    const store = useChatStore()
    store.setPreviewState('error')
    expect(store.previewState).toBe('error')
  })
})
