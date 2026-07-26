import { describe, expect, it } from 'vitest'
import { mockPreview } from './mock-data'

describe('review-safe preview data', () => {
  it('withholds candidate answer content while review is pending', () => {
    expect('answer' in mockPreview).toBe(false)
    expect(mockPreview.reviewReason).toContain('人工审核')
  })
})
