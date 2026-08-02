export interface Envelope<T> {
  code: 'OK'
  message: 'success'
  data: T
  request_id: string
}

export interface ApiErrorBody {
  code: string
  message: string
  request_id: string
  data?: { retryable?: boolean; retry_after_seconds?: number | null } | null
}

export interface Session {
  id: string
  title: string
  created_at: string
  updated_at: string
  last_message_at: string | null
}

export interface Page<T> {
  items: T[]
  page: {
    next_cursor: string | null
    has_more: boolean
  }
}

export interface Citation {
  citation_id: string
  document_id: string
  document_version: string
  chunk_id: string
  title: string
  source: string
  score: number
  page: number | null
  excerpt: string | null
}

export interface Message {
  id: string
  session_id: string
  role: 'user' | 'assistant'
  status: 'accepted' | 'generating' | 'completed' | 'needs_review' | 'cancelled' | 'failed' | 'rejected'
  content: string
  reply_to_message_id: string | null
  run_id: string | null
  citations: Citation[]
  created_at: string
  updated_at: string
}

export interface Memory {
  id: string
  memory_type: 'user_fact' | 'preference' | 'task_summary'
  content: string
  status: 'active' | 'inactive'
  confidence: number
  source_message_id: string
  corrected_from_version: number | null
  version: number
  created_at: string
  updated_at: string
}

export interface KnowledgeFile {
  id: string
  filename: string
  title: string
  source: string
  size_bytes: number
  chunk_count: number
  uploaded_at: string
}

export interface AuthUser {
  id: string
  username: string
  nickname: string
  avatar_url: string
  role: 'user' | 'reviewer'
  created_at: string
}

export interface AuthSession {
  access_token: string
  token_type: 'Bearer'
  expires_at: string
  user: AuthUser
}

export type ChatRequest =
  | { mode: 'new'; session_id: string; content: string }
  | { mode: 'retry'; session_id: string; original_user_message_id: string }

export type StreamEvent = {
  event_type: 'meta' | 'status' | 'tool_start' | 'tool_end' | 'delta' | 'citation' | 'review_required' | 'done' | 'error'
  sequence: number
  request_id: string
  session_id: string
  run_id: string
  timestamp: string
  payload: Record<string, unknown>
}
