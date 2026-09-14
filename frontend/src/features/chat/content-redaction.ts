const labeledLocalPath = /(?:文件路径|本地路径|file path|source path)\s*[:：]\s*`?file:\/\/[^\s`)\]）]+`?/gi
const rawLocalPath = /`?file:\/\/[^\s`)\]）]+`?/gi
const documentId = /(?:文档\s*ID|document\s*ID)\s*[:：]\s*`?[0-9a-f-]{8,}`?/gi
const documentVersion = /(?:文档\s*)?(?:版本|version)\s*[:：]\s*`?[A-Za-z0-9._-]+`?/gi
export function renumberRepeatedOrderedMarkers(content: string): string {
  const lines = content.split('\n')
  const parseMarker = (line: string): { indent: string; number: number; body: string } | null => {
    const trimmed = line.trimStart()
    const separatorIndex = ['.', '、', ')']
      .map(separator => trimmed.indexOf(separator))
      .filter(index => index >= 0)
      .sort((left, right) => left - right)[0]
    if (separatorIndex === undefined) return null
    const numberPart = trimmed.slice(0, separatorIndex)
    const number = Number.parseInt(numberPart, 10)
    if (!Number.isInteger(number) || String(number) !== numberPart) return null
    return {
      indent: line.slice(0, line.length - trimmed.length),
      number,
      body: trimmed.slice(separatorIndex + 1).trim(),
    }
  }
  const markers = lines.map(parseMarker)
  const markerLines = markers.filter((marker): marker is NonNullable<typeof marker> => Boolean(marker))
  const repeatedOnes = markerLines.length >= 2 && markerLines.every(marker => marker.number === 1)
  if (!repeatedOnes) return content

  let nextNumber = 1
  return lines.map((line, index) => {
    const marker = markers[index]
    if (!marker) return line
    const result = `${marker.indent}${nextNumber}. ${marker.body}`
    nextNumber += 1
    return result
  }).join('\n')
}

export function redactLocalSourcePaths(content: string): string {
  return content
    .replace(labeledLocalPath, '')
    .replace(rawLocalPath, '受控知识库资料')
    .replace(documentId, '')
    .replace(documentVersion, '')
    .replace(/[（(]\s*[，,;；\s]*[）)]/g, '')
    .replace(/[ \t]{2,}/g, ' ')
    .trim()
}

export function formatAssistantContent(content: string): string {
  return renumberRepeatedOrderedMarkers(redactLocalSourcePaths(content))
    .replace(/[ \t]+-[ \t]+(?=(?:\*\*)?[A-Z][A-Z0-9-]{1,})/g, '\n\n- ')
    .replace(/\*\*(.+?)\*\*/g, '$1')
    .replace(/(?<=[。！？!?])(?=[^\n])/g, '\n\n')
    .replace(/\n{3,}/g, '\n\n')
    .replace(/[ \t]{2,}/g, ' ')
    .trim()
}

export function toAssistantParagraphs(content: string): string[] {
  const paragraphs = formatAssistantContent(content)
    .split(/\n\s*\n/)
    .map(paragraph => paragraph.replace(/\s*\n\s*/g, ' ').trim())
    .filter(Boolean)

  let listIndex = 0
  return paragraphs.map(paragraph => {
    const item = paragraph.match(/^(?:[-*•])\s+(.+)$/)
    if (!item) {
      listIndex = 0
      return paragraph
    }
    listIndex += 1
    return `${listIndex}. ${item[1]}`
  })
}
