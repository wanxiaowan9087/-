import { describe, expect, it } from 'vitest'
import { robotImages } from './robotImages'

describe('robot image catalog', () => {
  it('keeps every product id mapped to a distinct visual variant', () => {
    const variants = Object.values(robotImages).map((image) => image.variant)
    expect(Object.keys(robotImages)).toHaveLength(6)
    expect(new Set(variants)).toHaveLength(6)
  })

  it('keeps every product image source non-empty', () => {
    expect(Object.values(robotImages).every((image) => image.src.length > 0)).toBe(true)
  })
})
