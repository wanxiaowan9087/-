import { describe, expect, it } from 'vitest'
import { mergeProductRecommendations } from './product-recommendation-fallback'

describe('mergeProductRecommendations', () => {
  it('hydrates historical answers that named canonical model codes before cards were persisted', () => {
    const answer = '推荐 M6-MINI 小径、S8-AIR 轻羽、S8-LUNA 皓月和 M6-TERRA 霞陶。'

    expect(mergeProductRecommendations([], answer).map(item => [item.productId, item.imageKey])).toEqual([
      ['m6-mini', 'robot-m6-mini'],
      ['s8-air', 'robot-s8-air'],
      ['s8-luna', 'robot-s8-luna'],
      ['m6-terra', 'robot-m6-terra'],
    ])
  })

  it('keeps structured MCP cards authoritative and does not duplicate them', () => {
    const structured = [{
      productId: 's8-luna',
      imageKey: 'robot-s8-luna',
      imageUrl: null,
      name: 'S8 皓月',
      price: 2999,
      highlights: ['静音运行'],
      reason: '适合夜间清洁',
      score: 0.9,
    }]

    const merged = mergeProductRecommendations(structured, '推荐 S8-LUNA 皓月。')

    expect(merged).toEqual(structured)
  })

  it('does not infer a card from an ambiguous nickname without a model code', () => {
    expect(mergeProductRecommendations([], '我喜欢皓月这个名字。')).toEqual([])
  })
})
