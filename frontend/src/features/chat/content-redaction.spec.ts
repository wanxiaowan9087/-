import { describe, expect, it } from 'vitest'
import { redactLocalSourcePaths } from './content-redaction'

describe('redactLocalSourcePaths', () => {
  it('removes a labeled local source path from persisted assistant content', () => {
    const content = '当前引用 1 篇资料（文件路径：file://data/catalog/robots.md）。'

    expect(redactLocalSourcePaths(content)).toBe('当前引用 1 篇资料。')
    expect(redactLocalSourcePaths(content)).not.toContain('file://')
  })
})
