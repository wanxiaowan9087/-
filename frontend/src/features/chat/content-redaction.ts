const labeledLocalPath = /(?:文件路径|本地路径|file path|source path)\s*[:：]\s*`?file:\/\/[^\s`)\]）]+`?/gi
const rawLocalPath = /`?file:\/\/[^\s`)\]）]+`?/gi
const documentId = /(?:文档\s*ID|document\s*ID)\s*[:：]\s*`?[0-9a-f-]{8,}`?/gi
const documentVersion = /(?:文档\s*)?(?:版本|version)\s*[:：]\s*`?[A-Za-z0-9._-]+`?/gi

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
  return redactLocalSourcePaths(content)
    .replace(/[ \t]+-[ \t]+(?=(?:\*\*)?[A-Z][A-Z0-9-]{1,})/g, '\n\n- ')
    .replace(/\*\*(.+?)\*\*/g, '$1')
    .replace(/(?<=[。！？!?])(?=[^\n])/g, '\n\n')
    .replace(/\n{3,}/g, '\n\n')
    .replace(/[ \t]{2,}/g, ' ')
    .trim()
}

export function toAssistantParagraphs(content: string): string[] {
  return formatAssistantContent(content)
    .split(/\n\s*\n/)
    .map(paragraph => paragraph.replace(/\s*\n\s*/g, ' ').trim())
    .filter(Boolean)
}
