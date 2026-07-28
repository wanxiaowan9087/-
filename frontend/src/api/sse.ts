export interface SseFrame {
  id?: string
  event?: string
  data: string
}

export async function* parseSseStream(body: ReadableStream<Uint8Array>): AsyncGenerator<SseFrame> {
  const reader = body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  let frame: string[] = []

  const decodeFrame = (lines: string[]): SseFrame | null => {
    const values: Record<string, string[]> = {}
    for (const line of lines) {
      if (!line || line.startsWith(':')) continue
      const separator = line.indexOf(':')
      const field = separator >= 0 ? line.slice(0, separator) : line
      const value = separator >= 0 ? line.slice(separator + 1).replace(/^ /, '') : ''
      values[field] = [...(values[field] ?? []), value]
    }
    if (!values.data) return null
    return { id: values.id?.at(-1), event: values.event?.at(-1), data: values.data.join('\n') }
  }

  while (true) {
    const { done, value } = await reader.read()
    buffer += decoder.decode(value, { stream: !done })
    const lines = buffer.split(/\r?\n/)
    buffer = lines.pop() ?? ''
    for (const line of lines) {
      if (line === '') {
        const parsed = decodeFrame(frame)
        if (parsed) yield parsed
        frame = []
      } else {
        frame.push(line)
      }
    }
    if (done) break
  }
  if (buffer) frame.push(buffer)
  const parsed = decodeFrame(frame)
  if (parsed) yield parsed
}
