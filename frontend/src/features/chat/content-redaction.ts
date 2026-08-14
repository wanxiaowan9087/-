const labeledLocalPath = /(?:文件路径|本地路径|file path|source path)\s*[:：]\s*`?file:\/\/[^\s`)\]）]+`?/gi
const rawLocalPath = /`?file:\/\/[^\s`)\]）]+`?/gi

export function redactLocalSourcePaths(content: string): string {
  return content
    .replace(labeledLocalPath, '')
    .replace(rawLocalPath, '受控知识库资料')
    .replace(/[（(]\s*[）)]/g, '')
    .replace(/\s{2,}/g, ' ')
    .trim()
}
