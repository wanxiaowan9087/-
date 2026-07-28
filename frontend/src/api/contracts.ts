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
