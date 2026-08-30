import { describe, expect, it } from 'vitest'
import { formatAssistantContent, redactLocalSourcePaths } from './content-redaction'

describe('redactLocalSourcePaths', () => {
  it('removes a labeled local source path from persisted assistant content', () => {
    const content = '当前引用 1 篇资料（文件路径：file://data/catalog/robots.md）。'

    expect(redactLocalSourcePaths(content)).toBe('当前引用 1 篇资料。')
    expect(redactLocalSourcePaths(content)).not.toContain('file://')
  })

  it('removes document IDs and internal versions while retaining the document name', () => {
    const content = '目前共有 1 篇知识文档，即《ZENMOP 扫地机器人型号目录》（文档 ID：6e241b40-f70f-552e-84ad-e693c9338365，版本：catalog-v1）。'
    const redacted = redactLocalSourcePaths(content)

    expect(redacted).toContain('《ZENMOP 扫地机器人型号目录》')
    expect(redacted).not.toMatch(/文档\s*ID|catalog-v1|6e241b40/i)
  })

  it('splits chained Markdown recommendation items into readable paragraphs', () => {
    const content = '推荐如下： - **M6-MINI** 适合小户型。 - **S8-AIR** 适合日常清洁。'

    expect(formatAssistantContent(content)).toBe(
      '推荐如下：\n\n- M6-MINI 适合小户型。\n\n- S8-AIR 适合日常清洁。',
    )
  })

  it('preserves line breaks and separates consecutive Chinese sentences', () => {
    expect(formatAssistantContent('第一句说明。第二句说明！第三句说明？')).toBe(
      '第一句说明。\n\n第二句说明！\n\n第三句说明？',
    )
  })
})
