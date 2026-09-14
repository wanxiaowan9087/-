import type { ProductRecommendationView } from '../../stores/chat'

export interface ProductPresentation {
  name: string
  subtitle: string
  imageKey: string
  modelPattern: RegExp
}

export const productPresentations: Record<string, ProductPresentation> = {
  's8-luna': { name: 'S8 皓月', subtitle: '静音深度清洁', imageKey: 'robot-s8-luna', modelPattern: /s8[\s_-]*luna(?![a-z0-9])/i },
  's8-air': { name: 'S8 Air', subtitle: '小户型轻量方案', imageKey: 'robot-s8-air', modelPattern: /s8[\s_-]*air(?![a-z0-9])/i },
  'x9-obsidian': { name: 'X9 曜石', subtitle: '全屋导航旗舰', imageKey: 'robot-x9-obsidian', modelPattern: /x9[\s_-]*obsidian(?![a-z0-9])/i },
  'x9-edge': { name: 'X9 Edge', subtitle: '边角强化清洁', imageKey: 'robot-x9-edge', modelPattern: /x9[\s_-]*edge(?![a-z0-9])/i },
  'm6-terra': { name: 'M6 霞陶', subtitle: '地面精细护理', imageKey: 'robot-m6-terra', modelPattern: /m6[\s_-]*terra(?![a-z0-9])/i },
  'm6-mini': { name: 'M6 Mini', subtitle: '木地板温柔护理', imageKey: 'robot-m6-mini', modelPattern: /m6[\s_-]*mini(?![a-z0-9])/i },
}

export function mergeProductRecommendations(
  structured: ProductRecommendationView[],
  assistantContent: string,
): ProductRecommendationView[] {
  const merged = [...structured]
  const seen = new Set(structured.map(item => item.productId))
  const inferred = Object.entries(productPresentations)
    .flatMap(([productId, presentation]) => {
      const match = presentation.modelPattern.exec(assistantContent)
      return match && !seen.has(productId)
        ? [{ productId, presentation, index: match.index }]
        : []
    })
    .sort((left, right) => left.index - right.index)

  for (const { productId, presentation } of inferred) {
    merged.push({
      productId,
      imageKey: presentation.imageKey,
      imageUrl: null,
      name: presentation.name,
      price: null,
      highlights: [],
      reason: null,
      score: null,
    })
  }
  return merged
}
