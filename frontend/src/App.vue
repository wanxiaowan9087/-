<script setup lang="ts">
/* global document, window, IntersectionObserver, HTMLElement, File, HTMLInputElement, URL, DragEvent */
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useChatStore } from './stores/chat'
import { mockPreview } from './features/chat/mock-data'
import RobotHero from './features/chat/RobotHero.vue'
import ProductRecommendations from './features/chat/ProductRecommendations.vue'
import { toAssistantParagraphs } from './features/chat/content-redaction'
import {
  commitSessionMessages,
  hasPersistedCompletedReply,
  loadCompleteTranscript,
  visibleTranscriptMessages,
  type SessionMessageCache,
  type SessionRequestTokens,
} from './features/chat/session-cache'
import { ApiClientError, createAgentApi } from './api/client'
import type { AuthSession, AuthUser, ChatRequest, KnowledgeFile, LegalDocument, Memory, Message, Session } from './api/contracts'
import { toProductRecommendationView } from './stores/chat'
import {
  clearStoredAuthSession,
  loadStoredAccessToken,
  loadStoredAuthUser,
  persistAuthSession,
  renewStoredAuthSession,
} from './features/auth/session'

const chat = useChatStore()
const isOverlay = computed(() => ['empty', 'error'].includes(chat.previewState))
const latestTool = computed(() => chat.tools.at(-1) ?? null)
const draft = ref('')
const submittedQuestion = ref('')
const activeAbortController = ref<InstanceType<typeof globalThis.AbortController> | null>(null)
type ProtectedAction = { type: 'agent' } | { type: 'product'; productId: string }
type KnowledgeUploadStatus = 'uploading' | 'indexed' | 'duplicate' | 'conflict' | 'similar' | 'failed' | 'skipped'
type KnowledgeUploadResult = {
  id: string
  file: File
  filename: string
  status: KnowledgeUploadStatus
  reason: string
  uploaded?: KnowledgeFile
  existingFilename?: string
}

const accessToken = ref(loadStoredAccessToken())
const authUser = ref<AuthUser | null>(loadStoredAuthUser())
const authLoading = ref(true)
const authSubmitting = ref(false)
const authDialogOpen = ref(false)
const pendingAuthAction = ref<ProtectedAction | null>(null)
const requestedProductId = ref<string | null>(null)
const authMode = ref<'login' | 'register' | 'reset'>('login')
const loginScope = ref<'user' | 'admin'>('user')
const authError = ref<string | null>(null)
const authForm = ref({ phone: '', password: '', nickname: '', verificationCode: '', agreeUserAgreement: false, agreePrivacyPolicy: false })
const smsCountdown = ref(0)
const smsSending = ref(false)
const legalDocument = ref<LegalDocument | null>(null)
const legalDialogOpen = ref(false)
const legalLoading = ref(false)
let smsTimer: ReturnType<typeof globalThis.setInterval> | undefined
const profileDialogOpen = ref(false)
const profileSubmitting = ref(false)
const profileError = ref<string | null>(null)
const profileForm = ref({ nickname: '', currentPassword: '', newPassword: '', confirmPassword: '' })
const profileAvatarFile = ref<File | null>(null)
const profileAvatarPreview = ref('')
const profileDropActive = ref(false)
const avatarInput = ref<HTMLInputElement | null>(null)
const knowledgeInput = ref<HTMLInputElement | null>(null)
const knowledgeUploading = ref(false)
const knowledgeUploadProgress = ref({ current: 0, total: 0 })
const knowledgeUploadResults = ref<KnowledgeUploadResult[]>([])
const knowledgeUploadDialogOpen = ref(false)
const knowledgeReindexing = ref(false)
const knowledgeFiles = ref<KnowledgeFile[]>([])
const knowledgeMutatingId = ref<string | null>(null)
const knowledgeSearch = ref('')
const knowledgeDetail = ref<KnowledgeFile | null>(null)
const knowledgeDetailLoading = ref(false)
const filteredKnowledgeFiles = computed(() => {
  const query = knowledgeSearch.value.trim().toLocaleLowerCase()
  if (!query) return knowledgeFiles.value
  return knowledgeFiles.value.filter(file => [file.title, file.filename, file.original_filename || '', file.ingest_status || '']
    .some(value => value.toLocaleLowerCase().includes(query)))
})
const toastMessage = ref('')
const toastTone = ref<'success' | 'info' | 'warning' | 'error'>('info')
let toastTimer: ReturnType<typeof globalThis.setTimeout> | undefined
const cancelDialogOpen = ref(false)
const sessionToDelete = ref<Session | null>(null)
const sessionDeleting = ref(false)
const currentView = ref<'showcase' | 'agent' | 'admin'>('showcase')
const defaultAvatarUrl = 'https://images.unsplash.com/photo-1534528741775-53994a69daeb?auto=format&fit=crop&w=160&q=80'
const api = createAgentApi({
  baseUrl: import.meta.env.VITE_API_BASE_URL || '/api/v1',
  accessToken: () => accessToken.value,
  onAuthenticatedResponse: () => renewStoredAuthSession(accessToken.value),
})
const userInitial = computed(() => authUser.value?.nickname.slice(0, 1) || '访')
const isAdmin = computed(() => authUser.value?.role === 'admin')
const passwordPattern = /^(?=.*[A-Za-z])(?=.*\d)(?=.*[^A-Za-z0-9]).{6,20}$/
const sessions = ref<Session[]>([])
const sessionMessages = ref<SessionMessageCache>({})
const memories = ref<Memory[]>([])
const historyLoading = ref(false)
const historyError = ref<string | null>(null)
const activeSessionTitle = computed(() => sessions.value.find(item => item.id === chat.sessionId)?.title || mockPreview.title)
const historicalMessages = computed(() => chat.sessionId ? sessionMessages.value[chat.sessionId] ?? [] : [])
const visibleHistoricalMessages = computed(() => visibleTranscriptMessages(historicalMessages.value, {
  currentUserMessageId: chat.userMessageId,
  currentRunId: chat.runId,
  pendingUserContent: submittedQuestion.value,
  // Keep the persisted pair hidden until the inline run is safely replaced.
  // This includes a review/error terminal state, where the inline card still
  // represents the current turn.
  isStreaming: Boolean(submittedQuestion.value),
}))

let revealObserver: IntersectionObserver | undefined
let conversationLoadVersion = 0
const messageLoadVersions: SessionRequestTokens = {}
let scrollTimer: ReturnType<typeof globalThis.setTimeout> | undefined

function summarizeSessionTitle(content: string): string {
  const compact = content.replace(/\s+/g, ' ').trim().replace(/[。！？!?，,；;：:]+$/g, '')
  return compact.length > 28 ? `${compact.slice(0, 27).trim()}…` : compact || '新会话'
}

function observeReveals() {
  if (!revealObserver) {
    document.querySelectorAll<HTMLElement>('[data-reveal]').forEach(element => element.classList.add('is-revealed'))
    return
  }
  document.querySelectorAll<HTMLElement>('[data-reveal]:not([data-reveal-observed])').forEach(element => {
    element.dataset.revealObserved = 'true'
    revealObserver?.observe(element)
  })
}

async function scrollConversationToEnd() {
  await nextTick()
  const lastMessage = document.querySelector<HTMLElement>('#agent-desk article.message:last-of-type')
  const composer = document.querySelector<HTMLElement>('.workspace--agent .composer-wrap')
  if (!lastMessage || !composer) return
  const lastBounds = lastMessage.getBoundingClientRect()
  const composerBounds = composer.getBoundingClientRect()
  const targetTop = globalThis.scrollY + lastBounds.bottom - composerBounds.top + 24
  globalThis.scrollTo({
    top: Math.max(0, targetTop),
    behavior: globalThis.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth',
  })
}

function scheduleConversationScroll() {
  if (scrollTimer) globalThis.clearTimeout(scrollTimer)
  scrollTimer = globalThis.setTimeout(() => {
    void scrollConversationToEnd()
  }, 80)
}

function isRetryableStreamError(error: unknown): boolean {
  if (error instanceof ApiClientError) return [408, 429, 500, 502, 503, 504].includes(error.status) || error.code === 'STREAM_INCOMPLETE'
  return error instanceof TypeError
}

async function recoverPersistedChat(sessionId: string, question: string): Promise<boolean> {
  try {
    await refreshSessionMessages(sessionId)
    const messages = sessionMessages.value[sessionId] ?? []
    let submittedIndex = -1
    for (let index = messages.length - 1; index >= 0; index -= 1) {
      if (messages[index]?.role === 'user' && messages[index]?.content === question) {
        submittedIndex = index
        break
      }
    }
    const completedReply = submittedIndex < 0
      ? undefined
      : messages.slice(submittedIndex + 1).find(message => message.role === 'assistant' && message.status === 'completed' && message.content)
    if (!completedReply) return false
    chat.$reset()
    chat.sessionId = sessionId
    submittedQuestion.value = ''
    draft.value = ''
    scheduleConversationScroll()
    return true
  } catch {
    return false
  }
}

watch(
  () => [currentView.value, submittedQuestion.value, chat.assistantText, chat.previewState],
  () => {
    if (currentView.value === 'agent') scheduleConversationScroll()
  },
  { flush: 'post' },
)

onMounted(() => {
  void restoreAuthentication()
  if (!('IntersectionObserver' in window)) {
    observeReveals()
    return
  }
  revealObserver = new IntersectionObserver(
    entries => {
      entries.forEach(entry => {
        if (entry.isIntersecting) {
          entry.target.classList.add('is-revealed')
          revealObserver?.unobserve(entry.target)
        }
      })
    },
    { rootMargin: '0px 0px -8% 0px', threshold: 0.08 },
  )
  observeReveals()
})

onBeforeUnmount(() => {
  revealObserver?.disconnect()
  if (scrollTimer) globalThis.clearTimeout(scrollTimer)
  if (smsTimer) globalThis.clearInterval(smsTimer)
  if (toastTimer) globalThis.clearTimeout(toastTimer)
})

function showToast(message: string, tone: typeof toastTone.value = 'info') {
  toastMessage.value = message
  toastTone.value = tone
  if (toastTimer) globalThis.clearTimeout(toastTimer)
  toastTimer = globalThis.setTimeout(() => { toastMessage.value = '' }, 4200)
}

function knowledgeStatusLabel(status: KnowledgeFile['ingest_status']) {
  return status === 'indexed' ? '已入库' : status === 'local' ? '待接入索引' : '异常'
}

function knowledgeDigest(file: KnowledgeFile) {
  return file.sha256 ? `${file.sha256.slice(0, 10)}…` : '未记录'
}

function knowledgeUploadLabel(status: KnowledgeUploadStatus) {
  return {
    uploading: '正在处理', indexed: '已入库', duplicate: '已存在',
    conflict: '名称冲突', similar: '内容相似', failed: '入库失败', skipped: '已跳过',
  }[status]
}

watch(
  () => [chat.runId, chat.assistantText, chat.previewState],
  async () => {
    await nextTick()
    observeReveals()
  },
)

async function restoreAuthentication() {
  if (!accessToken.value) {
    authLoading.value = false
    return
  }
  try {
    authUser.value = await api.currentUser()
    await refreshConversationState()
  } catch (error) {
    if (error instanceof ApiClientError && error.status === 401) {
      clearStoredAuthSession()
      accessToken.value = ''
      authUser.value = null
    } else {
      historyError.value = error instanceof Error ? error.message : '后端暂时不可用，已保留本地登录态'
    }
  } finally {
    authLoading.value = false
    void nextTick(() => observeReveals())
  }
}

watch(isAdmin, admin => {
  if (admin && currentView.value === 'admin') void refreshKnowledgeFiles()
})

function acceptAuthentication(session: AuthSession) {
  accessToken.value = session.access_token
  authUser.value = session.user
  persistAuthSession(session)
  authError.value = null
  authDialogOpen.value = false
  const action = pendingAuthAction.value
  pendingAuthAction.value = null
  authForm.value.password = ''
  authForm.value.verificationCode = ''
  if (loginScope.value === 'admin' && session.user.role === 'admin') {
    currentView.value = 'admin'
    void refreshKnowledgeFiles()
  } else if (action?.type === 'agent') {
    openAgentDesk()
  } else if (action?.type === 'product') {
    requestedProductId.value = action.productId
    void nextTick(() => { requestedProductId.value = null })
  }
  void nextTick(() => observeReveals())
}

async function submitAuthentication() {
  authError.value = null
  authSubmitting.value = true
  try {
    if (authMode.value !== 'login' && !passwordPattern.test(authForm.value.password)) {
      throw new Error('密码需为 6-20 位，且同时包含字母、数字和特殊符号。')
    }
    if (!/^1[3-9]\d{9}$/.test(authForm.value.phone)) throw new Error('请输入有效的 11 位手机号。')
    if (authMode.value === 'register' && (!authForm.value.agreeUserAgreement || !authForm.value.agreePrivacyPolicy)) {
      throw new Error('请先阅读并同意用户协议和隐私政策。')
    }
    if (authMode.value !== 'login' && !/^\d{6}$/.test(authForm.value.verificationCode)) {
      throw new Error('请输入 6 位短信验证码。')
    }
    const session = authMode.value === 'login'
      ? await api.login({ phone: authForm.value.phone, password: authForm.value.password })
      : authMode.value === 'reset'
        ? (await api.resetPassword({ phone: authForm.value.phone, verification_code: authForm.value.verificationCode, new_password: authForm.value.password }), await api.login({ phone: authForm.value.phone, password: authForm.value.password }))
        : await api.register({
          phone: authForm.value.phone,
          password: authForm.value.password,
          nickname: authForm.value.nickname,
          verification_code: authForm.value.verificationCode,
          user_agreement_version: '2026.08.21',
          privacy_policy_version: '2026.08.21',
          agree_user_agreement: true,
          agree_privacy_policy: true,
        })
    if (authMode.value === 'login' && loginScope.value === 'admin' && session.user.role !== 'admin') {
      throw new Error('该账号不是管理员，无法进入知识库管理台。')
    }
    acceptAuthentication(session)
  } catch (error) {
    authError.value = error instanceof Error ? error.message : '身份验证失败，请稍后重试。'
  } finally {
    authSubmitting.value = false
  }
}

function beginSmsCountdown(seconds: number) {
  smsCountdown.value = Math.max(1, seconds || 60)
  if (smsTimer) globalThis.clearInterval(smsTimer)
  smsTimer = globalThis.setInterval(() => {
    smsCountdown.value -= 1
    if (smsCountdown.value <= 0 && smsTimer) {
      globalThis.clearInterval(smsTimer)
      smsTimer = undefined
    }
  }, 1000)
}

async function sendAuthenticationCode() {
  authError.value = null
  if (!/^1[3-9]\d{9}$/.test(authForm.value.phone)) {
    authError.value = '请先输入有效的 11 位手机号。'
    return
  }
  if (smsCountdown.value > 0 || smsSending.value) return
  smsSending.value = true
  try {
    const result = await api.sendSmsCode({ phone: authForm.value.phone, purpose: authMode.value === 'reset' ? 'password_reset' : 'register' })
    beginSmsCountdown(result.retry_after_seconds)
  } catch (error) {
    authError.value = error instanceof Error ? error.message : '验证码发送失败，请稍后重试。'
  } finally {
    smsSending.value = false
  }
}

async function openLegalDocument(type: 'user-agreement' | 'privacy-policy') {
  legalLoading.value = true
  legalDialogOpen.value = true
  try {
    legalDocument.value = await api.getLegalDocument(type)
  } catch (error) {
    authError.value = error instanceof Error ? error.message : '协议暂时无法加载。'
    legalDialogOpen.value = false
  } finally {
    legalLoading.value = false
  }
}

function switchAuthMode(mode: 'login' | 'register' | 'reset') {
  authMode.value = mode
  authError.value = null
  authForm.value.password = ''
  authForm.value.verificationCode = ''
  authForm.value.agreeUserAgreement = false
  authForm.value.agreePrivacyPolicy = false
  if (smsTimer) globalThis.clearInterval(smsTimer)
  smsTimer = undefined
  smsCountdown.value = 0
}

function openProfile() {
  if (!authUser.value) return
  profileForm.value = { nickname: authUser.value.nickname, currentPassword: '', newPassword: '', confirmPassword: '' }
  profileAvatarFile.value = null
  profileAvatarPreview.value = authUser.value.avatar_url
  profileError.value = null
  profileDialogOpen.value = true
}

function selectAvatarFile(file: File | undefined) {
  if (!file) return
  if (!['image/jpeg', 'image/png', 'image/webp'].includes(file.type) || file.size > 2 * 1024 * 1024) {
    profileError.value = '请选择小于 2 MB 的 PNG、JPEG 或 WebP 图片。'
    return
  }
  profileAvatarFile.value = file
  profileAvatarPreview.value = URL.createObjectURL(file)
  profileError.value = null
}

function onAvatarDrop(event: DragEvent) {
  profileDropActive.value = false
  selectAvatarFile(event.dataTransfer?.files.item(0) ?? undefined)
}

async function submitProfile() {
  profileError.value = null
  profileSubmitting.value = true
  try {
    authUser.value = await api.updateProfile({
      nickname: profileForm.value.nickname.trim(),
    })
    if (profileAvatarFile.value) authUser.value = await api.uploadAvatar(profileAvatarFile.value)
    if (profileForm.value.newPassword || profileForm.value.currentPassword || profileForm.value.confirmPassword) {
      if (!passwordPattern.test(profileForm.value.newPassword)) throw new Error('新密码需为 6-20 位，且同时包含字母、数字和特殊符号。')
      if (profileForm.value.newPassword !== profileForm.value.confirmPassword) throw new Error('两次输入的新密码不一致。')
      authUser.value = await api.changePassword({
        current_password: profileForm.value.currentPassword,
        new_password: profileForm.value.newPassword,
      })
    }
    profileDialogOpen.value = false
  } catch (error) {
    profileError.value = error instanceof Error ? error.message : '资料保存失败，请稍后重试。'
  } finally {
    profileSubmitting.value = false
  }
}

function openAuthDialog(action: ProtectedAction | null = null, scope: 'user' | 'admin' = 'user') {
  pendingAuthAction.value = action
  loginScope.value = scope
  authError.value = null
  authDialogOpen.value = true
}

function closeAuthDialog() {
  if (authSubmitting.value) return
  authDialogOpen.value = false
  pendingAuthAction.value = null
  authError.value = null
}

function expireAuthentication(action: ProtectedAction, message = '登录已过期，请重新登录后继续。') {
  clearStoredAuthSession()
  accessToken.value = ''
  authUser.value = null
  currentView.value = 'showcase'
  authError.value = message
  pendingAuthAction.value = action
  authDialogOpen.value = true
  resetConversation()
}

function signOut() {
  clearStoredAuthSession()
  accessToken.value = ''
  authUser.value = null
  currentView.value = 'showcase'
  resetConversation()
}

function resetConversation() {
  chat.$reset()
  submittedQuestion.value = ''
  draft.value = ''
}

async function refreshConversationState() {
  if (!accessToken.value) return
  const loadVersion = ++conversationLoadVersion
  historyLoading.value = true
  historyError.value = null
  try {
    const [sessionPage, memoryPage] = await Promise.all([
      api.listSessions(),
      api.listMemories(),
    ])
    if (loadVersion !== conversationLoadVersion) return
    sessions.value = sessionPage.items
    memories.value = memoryPage.items
    if (chat.sessionId) {
      await refreshSessionMessages(chat.sessionId)
    } else if (sessionPage.items.length) {
      const latest = sessionPage.items[0]
      chat.sessionId = latest.id
      await refreshSessionMessages(latest.id)
      if (loadVersion === conversationLoadVersion) {
        chat.setPreviewState(historicalMessages.value.length ? 'ready' : 'empty')
        if (historicalMessages.value.length) await scrollConversationToEnd()
      }
    }
  } catch (error) {
    historyError.value = error instanceof Error ? error.message : '历史会话加载失败'
  } finally {
    if (loadVersion === conversationLoadVersion) historyLoading.value = false
  }
}

async function refreshSessionMessages(sessionId: string) {
  const loadVersion = (messageLoadVersions[sessionId] ?? 0) + 1
  messageLoadVersions[sessionId] = loadVersion
  const messages = await loadCompleteTranscript(
    cursor => api.listMessages(sessionId, 100, cursor),
    () => loadVersion === messageLoadVersions[sessionId],
  )
  if (messages === null) return
  // Each transcript has an independent request sequence. A background refresh
  // for one session must never invalidate a switch request for another.
  sessionMessages.value = commitSessionMessages(
    sessionMessages.value,
    sessionId,
    loadVersion,
    messageLoadVersions[sessionId],
    messages,
  )
  if (chat.sessionId === sessionId) {
    await nextTick()
    observeReveals()
  }
}

function replaceStreamWithPersistedTranscript(sessionId: string) {
  if (chat.sessionId !== sessionId || !hasPersistedCompletedReply(sessionMessages.value[sessionId] ?? [], chat.runId)) return
  chat.$reset()
  chat.sessionId = sessionId
  submittedQuestion.value = ''
}

async function openSession(sessionId: string) {
  if (chat.previewState === 'loading') return
  conversationLoadVersion += 1
  chat.$reset()
  submittedQuestion.value = ''
  draft.value = ''
  chat.sessionId = sessionId
  historyError.value = null
  const cachedMessages = sessionMessages.value[sessionId]
  chat.setPreviewState(cachedMessages?.length ? 'ready' : 'ready')
  try {
    await refreshSessionMessages(sessionId)
    if (chat.sessionId !== sessionId) return
    chat.setPreviewState(historicalMessages.value.length ? 'ready' : 'empty')
    chat.closeNav()
    if (historicalMessages.value.length) await scrollConversationToEnd()
  } catch (error) {
    historyError.value = error instanceof Error ? error.message : '会话消息加载失败'
    chat.setPreviewState('error')
  }
}

function openAgentDesk() {
  if (!authUser.value) {
    openAuthDialog({ type: 'agent' })
    return
  }
  currentView.value = 'agent'
  chat.closeNav()
  void refreshConversationState()
  void nextTick(() => observeReveals())
  globalThis.scrollTo({ top: 0, behavior: 'smooth' })
}

function openAdminDesk() {
  if (!authUser.value) {
    openAuthDialog(null, 'admin')
    return
  }
  if (!isAdmin.value) {
    authError.value = '当前账号没有管理员权限。'
    loginScope.value = 'admin'
    authDialogOpen.value = true
    return
  }
  currentView.value = 'admin'
  chat.closeNav()
  void refreshKnowledgeFiles()
  void nextTick(() => observeReveals())
  globalThis.scrollTo({ top: 0, behavior: 'smooth' })
}

function requestProductAccess(productId: string) {
  openAuthDialog({ type: 'product', productId })
}

function openShowcase() {
  currentView.value = 'showcase'
  chat.closeNav()
  globalThis.scrollTo({ top: 0, behavior: 'smooth' })
}

function openRecommendedProduct(productId: string) {
  requestedProductId.value = productId
  currentView.value = 'showcase'
  chat.closeNav()
  void nextTick(() => {
    document.querySelector('#robot-lineup')?.scrollIntoView({ behavior: 'smooth', block: 'start' })
  })
}

function recordProductUsageEvent(eventType: 'product_detail_viewed' | 'product_3d_viewed', productId: string) {
  if (!accessToken.value) return
  void api.recordUsageEvent({ event_type: eventType, product_id: productId }).catch(() => {
    // Usage analytics are best-effort and must never block product browsing.
  })
}

function messageRecommendations(message: Message) {
  return (message.product_recommendations ?? []).map(toProductRecommendationView)
}

function messageCitations(message: Message) {
  const seenDocumentIds = new Set<string>()
  return (message.citations ?? []).filter((citation) => {
    if (seenDocumentIds.has(citation.document_id)) return false
    seenDocumentIds.add(citation.document_id)
    return true
  })
}

async function executeChat(request: ChatRequest, question: string) {
  const controller = new globalThis.AbortController()
  activeAbortController.value = controller
  const idempotencyKey = globalThis.crypto.randomUUID()
  try {
    chat.beginRun(request.session_id)
    submittedQuestion.value = question
    scheduleConversationScroll()
    for (let attempt = 0; attempt < 2; attempt += 1) {
      try {
        await api.streamChat(request, {
          idempotencyKey,
          lastEventId: chat.lastEventId ?? undefined,
          signal: controller.signal,
          onEvent: event => chat.receiveStreamEvent(event),
        })
        break
      } catch (error) {
        if (attempt === 0 && isRetryableStreamError(error) && !controller.signal.aborted) continue
        throw error
      }
    }
    draft.value = ''
    await refreshConversationState()
    replaceStreamWithPersistedTranscript(request.session_id)
    scheduleConversationScroll()
  } catch (error) {
    if (!controller.signal.aborted) {
      if (error instanceof ApiClientError && error.status === 401) {
        expireAuthentication({ type: 'agent' })
        return
      }
      if (await recoverPersistedChat(request.session_id, question)) return
      chat.errorMessage = '本次运行未能完成，请检查后端服务与访问令牌后重试。'
      chat.setPreviewState('error')
    }
  } finally {
    if (activeAbortController.value === controller) activeAbortController.value = null
  }
}

async function sendMessage() {
  const content = draft.value.trim()
  if (!content) return
  if (!accessToken.value) {
    openAuthDialog({ type: 'agent' })
    return
  }
  let sessionId = chat.sessionId
  try {
    sessionId = sessionId ?? (await api.createSession(summarizeSessionTitle(content))).id
    if (!chat.sessionId) chat.sessionId = sessionId
    void refreshConversationState()
  } catch (error) {
    if (error instanceof ApiClientError && error.status === 401) {
      expireAuthentication({ type: 'agent' })
      return
    }
    chat.errorMessage = '本次运行未能完成，请检查后端服务与访问令牌后重试。'
    chat.setPreviewState('error')
    return
  }
  await executeChat({ mode: 'new', session_id: sessionId, content }, content)
}

async function uploadKnowledgeFiles(files: File[]) {
  if (!files.length || knowledgeUploading.value) return
  if (!isAdmin.value) {
    showToast('只有管理员可以上传知识文件。', 'error')
    return
  }
  knowledgeUploading.value = true
  knowledgeUploadProgress.value = { current: 0, total: files.length }
  knowledgeUploadResults.value = files.map((file, index) => ({
    id: `${file.name}:${file.size}:${file.lastModified}:${index}`,
    file,
    filename: file.name,
    status: 'uploading',
    reason: '等待校验与入库。',
  }))
  knowledgeUploadDialogOpen.value = true
  historyError.value = null
  try {
    for (const item of knowledgeUploadResults.value) {
      knowledgeUploadProgress.value.current += 1
      await uploadKnowledgeItem(item)
    }
    chat.errorMessage = ''
    if (knowledgeUploadResults.value.some(item => item.status === 'indexed')) chat.setPreviewState('ready')
    await refreshKnowledgeFiles()
  } finally {
    knowledgeUploading.value = false
    knowledgeUploadProgress.value = { current: 0, total: 0 }
    if (knowledgeInput.value) knowledgeInput.value.value = ''
  }
}

async function uploadKnowledgeItem(item: KnowledgeUploadResult, overwrite = false, allowSimilar = false) {
  const filename = item.filename.trim()
  if (!/\.(txt|md|markdown|pdf|xlsx)$/i.test(filename)) {
    item.status = 'failed'
    item.reason = '仅支持 .txt、.md、.markdown、.pdf、.xlsx 文件名。'
    return
  }
  if (item.file.size > 2 * 1024 * 1024) {
    item.status = 'failed'
    item.reason = '文件超过单个 2 MB 的限制。'
    return
  }
  item.status = 'uploading'
  item.reason = '正在切片、Embedding 并写入索引。'
  try {
    const uploaded = await api.uploadKnowledgeFile(item.file, filename, overwrite, allowSimilar)
    item.status = 'indexed'
    item.uploaded = uploaded
    item.reason = `已完成 ${uploaded.chunk_count} 个切片并写入知识库。`
    knowledgeFiles.value = [uploaded, ...knowledgeFiles.value.filter(file => file.id !== uploaded.id)]
    draft.value = `我已经上传了《${uploaded.title}》，请基于这份资料回答：`
  } catch (error) {
    if (error instanceof ApiClientError && error.status === 401) {
      expireAuthentication({ type: 'agent' })
      item.status = 'failed'
      item.reason = '登录已过期。'
      return
    }
    if (error instanceof ApiClientError && error.code === 'KNOWLEDGE_DUPLICATE') {
      item.status = 'duplicate'
      item.reason = '向量数据库已存在相同内容，未重复切片或写入。'
    } else if (error instanceof ApiClientError && error.code === 'KNOWLEDGE_NAME_CONFLICT') {
      item.status = 'conflict'
      item.existingFilename = String(error.data?.existing_filename || filename)
      item.reason = '已存在同名但内容不同的资料；修改名称后可重新入库。'
    } else if (error instanceof ApiClientError && error.code === 'KNOWLEDGE_SIMILAR') {
      item.status = 'similar'
      const similarity = typeof error.data?.similarity === 'number' ? `（相似度 ${(error.data.similarity * 100).toFixed(1)}%）` : ''
      item.reason = `与《${String(error.data?.similar_filename || '已有资料')}》内容高度相似${similarity}；改名不会消除语义重复。`
    } else {
      item.status = 'failed'
      item.reason = error instanceof Error ? error.message : '服务端未完成入库。'
    }
  }
}

function skipKnowledgeItem(item: KnowledgeUploadResult) {
  item.status = 'skipped'
  item.reason = '已跳过，不会写入知识库。'
}

async function retryKnowledgeItem(item: KnowledgeUploadResult) {
  if (knowledgeUploading.value || item.status === 'uploading') return
  await uploadKnowledgeItem(item)
  await refreshKnowledgeFiles()
}

async function overwriteKnowledgeItem(item: KnowledgeUploadResult) {
  if (knowledgeUploading.value || item.status === 'uploading') return
  await uploadKnowledgeItem(item, true)
  await refreshKnowledgeFiles()
}

async function keepSimilarKnowledgeItem(item: KnowledgeUploadResult) {
  if (knowledgeUploading.value || item.status === 'uploading') return
  await uploadKnowledgeItem(item, false, true)
  await refreshKnowledgeFiles()
}

function requestDeleteSession(session: Session) {
  if (chat.previewState === 'loading') {
    showToast('当前正在生成回答，请先停止后再删除会话。', 'warning')
    return
  }
  sessionToDelete.value = session
}

async function confirmDeleteSession() {
  const target = sessionToDelete.value
  if (!target || sessionDeleting.value) return
  sessionDeleting.value = true
  try {
    await api.deleteSession(target.id)
    sessions.value = sessions.value.filter(session => session.id !== target.id)
    const remainingMessages = { ...sessionMessages.value }
    delete remainingMessages[target.id]
    sessionMessages.value = remainingMessages
    if (chat.sessionId === target.id) resetConversation()
    sessionToDelete.value = null
    showToast(`已删除会话《${target.title}》。`, 'success')
  } catch (error) {
    if (error instanceof ApiClientError && error.status === 401) { expireAuthentication({ type: 'agent' }); return }
    showToast(error instanceof Error ? error.message : '会话删除失败。', 'error')
  } finally {
    sessionDeleting.value = false
  }
}

async function refreshKnowledgeFiles() {
  if (!isAdmin.value) return
  try {
    const page = await api.listKnowledgeFiles()
    knowledgeFiles.value = page.items
  } catch (error) {
    if (error instanceof ApiClientError && error.status === 401) {
      expireAuthentication({ type: 'agent' })
      return
    }
    showToast(error instanceof Error ? error.message : '知识文件列表加载失败', 'error')
  }
}

async function editKnowledgeFile(file: KnowledgeFile) {
  if (!isAdmin.value || knowledgeMutatingId.value) return
  const title = window.prompt('修改文档标题', file.title)?.trim()
  if (!title || title === file.title) return
  knowledgeMutatingId.value = file.id
  try {
    const updated = await api.updateKnowledgeFile(file.id, { title })
    knowledgeFiles.value = knowledgeFiles.value.map(item => item.id === file.id ? updated : item)
    showToast('文档标题已更新并重新索引。', 'success')
  } catch (error) {
    showToast(error instanceof Error ? error.message : '文档修改失败', 'error')
  } finally {
    knowledgeMutatingId.value = null
  }
}

async function viewKnowledgeFile(file: KnowledgeFile) {
  if (knowledgeDetailLoading.value) return
  knowledgeDetailLoading.value = true
  try {
    knowledgeDetail.value = await api.getKnowledgeFile(file.id)
  } catch (error) {
    showToast(error instanceof Error ? error.message : '文档详情加载失败', 'error')
  } finally {
    knowledgeDetailLoading.value = false
  }
}

async function deleteKnowledgeFile(file: KnowledgeFile) {
  if (!isAdmin.value || knowledgeMutatingId.value) return
  if (!window.confirm(`确认删除《${file.title}》？删除后将同时移除向量索引。`)) return
  knowledgeMutatingId.value = file.id
  try {
    await api.deleteKnowledgeFile(file.id)
    knowledgeFiles.value = knowledgeFiles.value.filter(item => item.id !== file.id)
    showToast('知识文档及其索引已删除。', 'success')
  } catch (error) {
    showToast(error instanceof Error ? error.message : '文档删除失败', 'error')
  } finally {
    knowledgeMutatingId.value = null
  }
}

async function reindexKnowledgeFiles() {
  if (!isAdmin.value || knowledgeReindexing.value) return
  knowledgeReindexing.value = true
  historyError.value = null
  try {
    const result = await api.reindexKnowledgeFiles()
    showToast(`索引已重建，共处理 ${result.chunks_indexed} 个文本切片。`, 'success')
  } catch (error) {
    if (error instanceof ApiClientError && error.status === 401) {
      expireAuthentication({ type: 'agent' })
      return
    }
    showToast(error instanceof Error ? error.message : '索引重建失败', 'error')
  } finally {
    knowledgeReindexing.value = false
  }
}

async function retryLastMessage() {
  if (!chat.sessionId || !chat.userMessageId || !submittedQuestion.value) return
  await executeChat(
    { mode: 'retry', session_id: chat.sessionId, original_user_message_id: chat.userMessageId },
    submittedQuestion.value,
  )
}

async function cancelActiveRun() {
  if (!chat.runId) return
  try {
    await api.cancelRun(chat.runId)
    activeAbortController.value?.abort()
    chat.markCancellationRequested()
  } catch (error) {
    if (error instanceof ApiClientError && error.status === 401) {
      expireAuthentication({ type: 'agent' })
      return
    }
    chat.errorMessage = '取消请求未能送达服务端，请检查网络后重试。'
    chat.setPreviewState('error')
  }
}

function requestCancelActiveRun() {
  if (chat.runId) cancelDialogOpen.value = true
}

async function confirmCancelActiveRun() {
  cancelDialogOpen.value = false
  await cancelActiveRun()
}
</script>

<template>
  <div class="app-shell" :class="{ 'nav-open': chat.navOpen }">
    <aside class="sidebar" :class="{ 'sidebar--showcase': currentView === 'showcase' }" :aria-label="currentView === 'showcase' ? '产品导航' : '会话导航'">
      <template v-if="currentView === 'showcase'">
        <div class="showcase-brand"><span class="showcase-brand__mark">Z</span><div><b>ZENMOP</b><small>SMART HOME CARE</small></div></div>
        <p class="showcase-nav__label">产品展厅</p>
        <nav class="showcase-nav" aria-label="产品展厅导航">
          <a href="#robot-lineup"><span>01</span> 全系产品</a>
          <a href="#robot-lineup"><span>02</span> 清洁方案</a>
          <button type="button" @click="openAgentDesk"><span>03</span> 问问小智</button>
        </nav>
        <div class="showcase-aside__footer"><p>为生活留白，<br />把清洁交给秩序。</p><button type="button" @click="openAgentDesk">进入智能问答 <span>→</span></button></div>
      </template>
      <template v-else-if="currentView === 'agent'">
      <button class="showcase-return" type="button" @click="openShowcase"><span>←</span> 返回产品展厅</button>
      <div class="brand-lockup"><span class="brand-mark">智</span><span>小智问答</span><small>AGENT DESK</small></div>
      <button class="new-session" type="button" @click="resetConversation"><span>＋</span>新建会话 <kbd>⌘ K</kbd></button>
      <div class="sidebar-label">近期会话</div>
      <nav class="session-list">
        <div v-for="session in sessions" :key="session.id" class="session-entry" :class="{ active: session.id === chat.sessionId }"><button class="session" type="button" @click="openSession(session.id)"><b>{{ session.title }}</b><span>{{ session.last_message_at ? new Date(session.last_message_at).toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' }) : '尚未开始' }}</span></button><button class="session-delete" type="button" :aria-label="`删除会话 ${session.title}`" title="删除会话" @click.stop="requestDeleteSession(session)">×</button></div>
        <p v-if="!sessions.length" class="session-empty">{{ historyLoading ? '正在同步历史...' : '暂无历史会话' }}</p>
      </nav>
      <p v-if="historyError" class="session-error">{{ historyError }}</p>
      <div class="sidebar-foot"><span class="presence"></span><div><b>{{ authUser?.nickname || '访客' }}</b><small>{{ memories.length ? `${memories.length} 条长期记忆` : '7 天内自动登录' }}</small></div><button aria-label="退出登录" title="退出登录" @click="signOut">退出</button></div>
      </template>
      <template v-else>
        <button class="showcase-return" type="button" @click="openAgentDesk"><span>←</span> 返回客服工作台</button>
        <div class="brand-lockup brand-lockup--admin"><span class="brand-mark">管</span><div class="brand-lockup__copy"><span>知识库管理</span><small>ADMIN CONSOLE</small></div></div>
        <div class="sidebar-label">管理功能</div>
        <nav class="session-list admin-nav"><button class="session active" type="button"><b>知识文件</b><span>上传并纳入检索</span></button></nav>
        <div class="sidebar-foot"><span class="presence"></span><div><b>{{ authUser?.nickname }}</b><small>管理员</small></div><button type="button" @click="signOut">退出</button></div>
      </template>
    </aside>

    <main class="workspace" :class="{ 'workspace--agent': currentView === 'agent', 'workspace--admin': currentView === 'admin' }">
      <header v-if="currentView === 'showcase'" class="topbar topbar--showcase">
        <div class="crumb"><span>ZENMOP /</span> 智能清洁产品展厅 <em>2026 COLLECTION</em></div>
        <div class="header-actions">
          <span class="secure-dot">{{ authLoading ? '正在检查登录' : '服务已就绪' }}</span>
          <button v-if="!authUser" class="topbar-login-entry" type="button" @click="openAuthDialog()">登录 / 注册</button>
          <button v-else type="button" class="avatar" :aria-label="`${authUser.nickname}，打开个人资料`" @click="openProfile"><img :src="authUser.avatar_url" :alt="`${authUser.nickname}的头像`" /><span>{{ userInitial }}</span></button>
          <button v-if="isAdmin" class="topbar-login-entry" type="button" @click="openAdminDesk">管理台</button>
          <button class="topbar-agent-entry" type="button" @click="openAgentDesk">小智问答 <span>→</span></button>
        </div>
      </header>
      <header v-else-if="currentView === 'agent'" class="topbar">
        <button class="menu-button" type="button" aria-label="打开会话列表" @click="chat.toggleNav">☰</button>
        <div class="crumb"><span>会话 /</span> {{ activeSessionTitle }} <em>v1.0</em></div>
        <div class="header-actions"><span class="secure-dot">已登录</span><button type="button" class="avatar" :aria-label="`${authUser?.nickname || '用户'}，打开个人资料`" @click="openProfile"><img :src="authUser?.avatar_url || defaultAvatarUrl" :alt="`${authUser?.nickname || '用户'}的头像`" /><span>{{ userInitial }}</span></button></div>
      </header>

      <RobotHero v-if="currentView === 'showcase'" :authenticated="Boolean(authUser)" :requested-product-id="requestedProductId" @consult="openAgentDesk" @auth-required="requestProductAccess" @usage-event="recordProductUsageEvent" />
      <template v-else-if="currentView === 'agent'">
      <section id="agent-desk" class="stage" aria-label="聊天工作区">
        <div class="thread-head" data-reveal><div><p class="eyebrow">CASE · {{ mockPreview.caseId }}</p><h1>{{ mockPreview.title }}</h1><p>{{ mockPreview.summary }}</p></div><button class="trace-link" type="button"><span>◉</span>运行追踪 <b>{{ chat.runId || '尚未运行' }}</b><i>↗</i></button></div>
        <div class="thread-rule"></div>
        <article v-for="message in visibleHistoricalMessages" :key="message.id" class="message" :class="message.role === 'user' ? 'customer' : 'agent'" data-reveal>
          <div class="message-meta">
            <span v-if="message.role === 'user'" class="message-avatar user"><img :src="authUser?.avatar_url || defaultAvatarUrl" :alt="`${authUser?.nickname || '用户'}的头像`" /><i>{{ userInitial }}</i></span>
            <span v-else class="message-avatar bot">程</span>
            <b>{{ message.role === 'user' ? authUser?.nickname || '用户' : '小智' }}</b>
            <span v-if="message.role === 'assistant'" class="model-chip">{{ message.status === 'completed' ? '已完成' : message.status }}</span>
            <time>{{ new Date(message.created_at).toLocaleString('zh-CN', { hour: '2-digit', minute: '2-digit' }) }}</time>
          </div>
          <p v-if="message.role === 'user'">{{ message.content }}</p>
          <section v-else class="answer-card"><p v-for="(paragraph, index) in toAssistantParagraphs(message.content)" :key="`${message.id}:${index}`">{{ paragraph }}</p></section>
          <section v-if="messageCitations(message).length" class="sources">
            <div class="sources-head"><span>依据资料</span><small>{{ messageCitations(message).length }} 条可定位引用</small></div>
            <div class="source-grid"><button v-for="citation in messageCitations(message)" :key="`${message.id}:${citation.document_id}`" class="source-card" type="button"><span class="source-index">#</span><div><b>{{ citation.title }}</b><p>{{ citation.page ? `第 ${citation.page} 页` : '知识库资料' }}</p></div><i>↗</i></button></div>
          </section>
          <ProductRecommendations :recommendations="messageRecommendations(message)" @select="openRecommendedProduct" />
        </article>

        <article v-if="submittedQuestion" class="message customer" data-reveal><div class="message-meta"><span class="message-avatar user"><img :src="authUser?.avatar_url || defaultAvatarUrl" :alt="`${authUser?.nickname || '用户'}的头像`" /><i>{{ userInitial }}</i></span><b>{{ authUser?.nickname || '用户' }}</b><time>刚刚</time></div><p>{{ submittedQuestion }}</p></article>

        <article v-if="chat.runId" class="message agent" data-reveal :class="{ withheld: Boolean(chat.review) }">
          <div class="message-meta"><span class="message-avatar bot">程</span><b>小智</b><span class="model-chip">{{ chat.previewState === 'loading' ? '正在生成' : chat.review ? '等待审核' : chat.runOutcome === 'cancelled' ? '已取消' : '已完成' }}</span><time>刚刚</time></div>
          <section v-if="chat.review" class="withheld-card" aria-label="候选答案已扣留，等待人工审核">
            <div class="withheld-seal" aria-hidden="true"><span></span><span></span><span></span></div>
            <div><p class="eyebrow">DRAFT WITHHELD</p><h2>候选答案等待人工审核</h2><p>{{ chat.review.reasonCodes.join(' · ') || '运行策略要求人工审核' }}</p></div>
            <span class="withheld-code">{{ chat.review.reviewId || 'PENDING' }}</span>
          </section>
          <section v-else-if="chat.assistantText" class="answer-card" aria-live="polite"><p v-for="(paragraph, index) in toAssistantParagraphs(chat.assistantText)" :key="`stream:${index}`">{{ paragraph }}</p></section>
          <section v-else class="tool-card"><div class="tool-top"><span class="tool-icon">↻</span><div><b>{{ chat.runOutcome === 'cancelled' ? '本次运行已取消' : latestTool ? `工具：${latestTool.toolName}` : '正在调用受控 Agent' }}</b><small>{{ chat.runOutcome === 'cancelled' ? '已通知服务端停止执行，候选内容不会发布。' : latestTool?.detail || chat.lastStatus || '检索、重排与安全策略检查中' }}</small></div><span class="tool-ok">{{ chat.runOutcome === 'cancelled' ? '已取消' : latestTool?.outcome || '运行中' }}</span></div></section>
          <section v-if="chat.citations.length" class="sources"><div class="sources-head"><span>依据资料</span><small>{{ chat.citations.length }} 条可定位引用</small></div><div class="source-grid"><button v-for="(citation, index) in chat.citations" :key="`${citation.documentVersion}:${citation.chunkId}`" class="source-card" type="button"><span class="source-index">{{ String(index + 1).padStart(2, '0') }}</span><div><b>{{ citation.title }}</b><p>{{ citation.locator }}</p></div><i>↗</i></button></div></section>
          <ProductRecommendations :recommendations="chat.productRecommendations" @select="openRecommendedProduct" />
          <section v-if="chat.review" class="review-card"><div class="review-mark">◉</div><div><p class="eyebrow">HUMAN REVIEW</p><b>审核队列已接收</b><small>候选正文不会向普通用户透露；批准后才会发布。</small></div><span class="review-status">待审核</span></section>
        </article>
        <div v-if="chat.previewState === 'loading'" class="streaming-indicator" role="status" aria-live="polite"><span class="runner" aria-hidden="true">🏃</span><span>小智正在整理资料并生成回答</span></div>

        <section v-if="isOverlay" class="state-panel" :class="chat.previewState" aria-live="polite">
          <span class="state-glyph">{{ chat.previewState === 'loading' ? '◌' : chat.previewState === 'error' ? '!' : '—' }}</span>
          <h2>{{ chat.previewState === 'loading' ? '正在编排本次运行' : chat.previewState === 'error' ? '本次运行未能完成' : '这里还没有消息' }}</h2>
          <p>{{ chat.previewState === 'loading' ? '正在检索知识库并检查策略，请稍候。' : chat.errorMessage || '输入一个问题即可开始新会话。' }}</p>
          <button v-if="chat.previewState === 'error' && chat.userMessageId" type="button" @click="retryLastMessage">重试本次运行</button><button v-else-if="chat.previewState === 'error'" type="button" @click="chat.setPreviewState('ready')">返回对话</button>
        </section>
      </section>
      <footer class="composer-wrap"><form class="composer" @submit.prevent="sendMessage"><textarea v-model="draft" aria-label="消息输入" placeholder="询问知识库，或输入一条客服处理需求…" :disabled="chat.previewState === 'disabled' || chat.previewState === 'loading'" @keydown.enter.exact.prevent="sendMessage"></textarea><p v-if="chat.previewState === 'error' && chat.errorMessage" class="composer-error" role="alert">{{ chat.errorMessage }}</p><div class="composer-bar"><span>回答仅基于受控知识库；需更新资料请联系管理员。</span><button v-if="chat.previewState === 'loading' && chat.runId" type="button" class="send" @click="requestCancelActiveRun">停止</button><button v-else type="submit" class="send" :disabled="chat.previewState === 'disabled' || chat.previewState === 'loading' || !draft.trim()">发送 <b>↑</b></button></div></form></footer>
      </template>
      <template v-else>
        <header class="topbar"><button class="menu-button" type="button" aria-label="打开管理导航" @click="chat.toggleNav">☰</button><div class="agent-heading"><span>ADMIN / KNOWLEDGE</span><b>知识库入库管理</b></div><div class="header-actions"><button class="avatar" type="button" :aria-label="`${authUser?.nickname}，打开个人资料`" @click="openProfile"><img :src="authUser?.avatar_url || defaultAvatarUrl" :alt="`${authUser?.nickname}的头像`" /><span>{{ userInitial }}</span></button></div></header>
        <section class="admin-stage">
          <p class="eyebrow">CONTROLLED KNOWLEDGE</p><h1>将经过审核的资料纳入客服检索。</h1><p>上传前会校验原始文件名和 SHA-256；完全重复的文档不会再次切片、Embedding，同名不同内容会被拦截并提示。</p>
          <div class="admin-upload-card"><div class="admin-upload-card__copy"><b>批量上传知识文件</b><small>支持 .txt、.md、.pdf、.xlsx，单个文件不超过 2 MB。PDF 按页解析，Excel 按工作表和行保留字段上下文。</small></div><input ref="knowledgeInput" class="knowledge-file-input" type="file" multiple accept=".txt,.md,.markdown,.pdf,.xlsx,text/plain,text/markdown,application/pdf,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" @change="uploadKnowledgeFiles(Array.from(($event.target as HTMLInputElement).files || []))" /><div class="admin-upload-card__actions"><button type="button" class="send" :disabled="knowledgeUploading" @click="knowledgeInput?.click()">{{ knowledgeUploading ? `入库中 ${knowledgeUploadProgress.current}/${knowledgeUploadProgress.total}` : '选择多个文件' }}</button><button type="button" class="send send--secondary" :disabled="knowledgeReindexing" @click="reindexKnowledgeFiles">{{ knowledgeReindexing ? '重建中…' : '重建索引' }}</button></div></div>
          <div class="admin-file-list"><div class="admin-file-list__head"><div><b>已入库资料</b><small>统一管理已发布到 pgvector 与关键词索引的文档</small></div><div class="admin-file-list__tools"><input v-model.trim="knowledgeSearch" type="search" placeholder="搜索标题、文件名或状态" aria-label="搜索知识文件" /><span>{{ filteredKnowledgeFiles.length }} / {{ knowledgeFiles.length }} 个文件</span></div></div><p v-if="!knowledgeFiles.length" class="admin-file-empty">暂无已入库文件。</p><p v-else-if="!filteredKnowledgeFiles.length" class="admin-file-empty">没有匹配的知识文件。</p><div v-else class="admin-file-table" role="table" aria-label="知识文件清单"><div class="admin-file-table__row admin-file-table__row--header" role="row"><span>资料</span><span>索引状态</span><span>切片</span><span>摘要</span><span>最近更新</span><span>操作</span></div><article v-for="file in filteredKnowledgeFiles" :key="file.id" class="admin-file-table__row" role="row"><div class="admin-file-identity"><b>{{ file.title }}</b><small>{{ file.original_filename || file.filename }} · {{ (file.size_bytes / 1024).toFixed(1) }} KB</small></div><span class="knowledge-status" :class="`knowledge-status--${file.ingest_status || 'indexed'}`">{{ knowledgeStatusLabel(file.ingest_status) }}</span><span>{{ file.chunk_count }} 段</span><code>{{ knowledgeDigest(file) }}</code><time>{{ new Date(file.last_indexed_at || file.uploaded_at).toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' }) }}</time><div class="admin-file-actions"><button type="button" class="batch-keep" :disabled="knowledgeDetailLoading" @click="viewKnowledgeFile(file)">查看</button><button type="button" class="batch-retry" :disabled="knowledgeMutatingId === file.id" @click="editKnowledgeFile(file)">修改</button><button type="button" class="batch-skip" :disabled="knowledgeMutatingId === file.id" @click="deleteKnowledgeFile(file)">删除</button></div></article></div></div>
          <p v-if="historyError" class="session-error">{{ historyError }}</p>
        </section>
      </template>
    </main>
    <button class="scrim" aria-label="关闭会话列表" @click="chat.closeNav"></button>
  </div>
  <Teleport to="body">
    <div v-if="toastMessage" class="app-toast" :class="`app-toast--${toastTone}`" role="status" aria-live="polite"><span aria-hidden="true">{{ toastTone === 'success' ? '✓' : toastTone === 'warning' ? '!' : toastTone === 'error' ? '×' : 'i' }}</span>{{ toastMessage }}</div>
    <section v-if="knowledgeUploadDialogOpen" class="profile-overlay" aria-label="批量上传结果" @click.self="!knowledgeUploading && (knowledgeUploadDialogOpen = false)"><article class="knowledge-batch-dialog" role="dialog" aria-modal="true" aria-labelledby="knowledge-batch-title"><button class="auth-close" type="button" aria-label="关闭上传结果" :disabled="knowledgeUploading" @click="knowledgeUploadDialogOpen = false">×</button><p class="eyebrow">KNOWLEDGE INGESTION</p><h2 id="knowledge-batch-title">批量入库结果</h2><p class="knowledge-batch-summary">{{ knowledgeUploading ? `正在处理 ${knowledgeUploadProgress.current} / ${knowledgeUploadProgress.total} 个文件` : `已完成 ${knowledgeUploadResults.length} 个文件的校验与处理` }}</p><section class="knowledge-batch-section"><h3>已入库</h3><p v-if="!knowledgeUploadResults.some(item => item.status === 'indexed')" class="knowledge-batch-empty">本次没有新文件写入。</p><article v-for="item in knowledgeUploadResults.filter(item => item.status === 'indexed')" :key="item.id" class="knowledge-batch-row knowledge-batch-row--success"><div><b>{{ item.filename }}</b><small>{{ item.reason }}</small></div><span class="knowledge-batch-status">{{ knowledgeUploadLabel(item.status) }}</span></article></section><section class="knowledge-batch-section"><h3>需要处理</h3><p v-if="!knowledgeUploadResults.some(item => ['duplicate', 'conflict', 'similar', 'failed', 'skipped'].includes(item.status))" class="knowledge-batch-empty">没有需要处理的文件。</p><article v-for="item in knowledgeUploadResults.filter(item => ['duplicate', 'conflict', 'similar', 'failed', 'skipped'].includes(item.status))" :key="item.id" class="knowledge-batch-row" :class="`knowledge-batch-row--${item.status}`"><div class="knowledge-batch-row__detail"><b>{{ item.file.name }}</b><small>{{ item.reason }}</small><label v-if="item.status === 'conflict'">新的文件名<input v-model.trim="item.filename" maxlength="180" :disabled="knowledgeUploading" /></label></div><div class="knowledge-batch-row__actions"><span class="knowledge-batch-status">{{ knowledgeUploadLabel(item.status) }}</span><button v-if="item.status === 'conflict'" type="button" class="batch-overwrite" :disabled="knowledgeUploading" @click="overwriteKnowledgeItem(item)">覆盖原文档</button><button v-if="item.status === 'similar'" type="button" class="batch-keep" :disabled="knowledgeUploading" @click="keepSimilarKnowledgeItem(item)">仍然保留</button><button v-if="item.status === 'conflict' || item.status === 'failed'" type="button" class="batch-retry" :disabled="knowledgeUploading" @click="retryKnowledgeItem(item)">{{ item.status === 'conflict' ? '修改并入库' : '重试' }}</button><button v-if="item.status === 'conflict' || item.status === 'similar' || item.status === 'failed'" type="button" class="batch-skip" :disabled="knowledgeUploading" @click="skipKnowledgeItem(item)">跳过</button></div></article></section><div class="knowledge-batch-footer"><button type="button" class="auth-browse" :disabled="knowledgeUploading" @click="knowledgeUploadDialogOpen = false">完成</button></div></article></section>
    <section v-if="sessionToDelete" class="profile-overlay" aria-label="确认删除会话" @click.self="!sessionDeleting && (sessionToDelete = null)"><div class="cancel-card" role="dialog" aria-modal="true"><p class="eyebrow">DELETE CONVERSATION</p><h2>删除这个会话？</h2><p>会话中的消息、运行记录、反馈、审核记录和由此生成的记忆会一并删除，操作不可恢复。</p><div><button type="button" class="auth-browse" :disabled="sessionDeleting" @click="sessionToDelete = null">取消</button><button type="button" class="auth-submit auth-submit--danger" :disabled="sessionDeleting" @click="confirmDeleteSession">{{ sessionDeleting ? '删除中…' : '确认删除' }}</button></div></div></section>
    <section v-if="authDialogOpen" class="auth-overlay" :aria-label="authMode === 'register' ? '注册账号' : authMode === 'reset' ? '重置密码' : '账号登录'" @click.self="closeAuthDialog">
      <div class="auth-shell" role="dialog" aria-modal="true" :aria-labelledby="`auth-title-${authMode}`">
        <button class="auth-close" type="button" aria-label="关闭登录窗口" :disabled="authSubmitting" @click="closeAuthDialog">×</button>
        <div class="auth-promo">
          <div class="auth-brand"><span>Z</span><div><b>ZENMOP</b><small>SMART HOME CARE</small></div></div>
          <p class="eyebrow">A QUIETER WAY HOME</p>
          <h1>让清洁更聪明，<br />也更懂你的家。</h1>
          <p>商品展厅可以自由浏览。登录后可查看完整商品方案，并使用小智获得个性化清洁建议。</p>
          <div class="auth-rule"></div>
          <small>一次登录，7 天内持续有效；每次使用自动续期。</small>
        </div>
        <form class="auth-card" @submit.prevent="submitAuthentication">
          <div class="auth-card__top"><img class="auth-avatar" :src="defaultAvatarUrl" alt="默认头像" /><span>{{ authMode === 'login' ? (loginScope === 'admin' ? '管理员登录' : '欢迎回来') : authMode === 'register' ? '成为 ZENMOP 用户' : '找回账号访问权' }}</span></div>
          <p class="eyebrow">{{ authMode === 'login' ? 'WELCOME BACK' : authMode === 'register' ? 'CREATE ACCOUNT' : 'RESET ACCESS' }}</p>
          <h2 :id="`auth-title-${authMode}`">{{ authMode === 'login' ? '登录账号' : authMode === 'register' ? '创建账号' : '重置密码' }}</h2>
          <p class="auth-hint">{{ authMode === 'login' ? (loginScope === 'admin' ? '管理员账号也使用绑定手机号登录。' : '继续查看商品详情，或与小智聊聊你的清洁需求。') : authMode === 'register' ? '使用手机号完成验证后，即可保存对话并获得个性化建议。' : '通过已绑定手机号验证身份，设置新的登录密码。' }}</p>
          <label>手机号<input v-model.trim="authForm.phone" autocomplete="tel" inputmode="tel" required maxlength="11" pattern="1[3-9][0-9]{9}" placeholder="请输入 11 位手机号" /></label>
          <label v-if="authMode === 'register'">昵称<input v-model.trim="authForm.nickname" required maxlength="40" placeholder="用于对话中的称呼" /></label>
          <label v-if="authMode !== 'login'" class="auth-code-label">短信验证码<div class="auth-code-row"><input v-model.trim="authForm.verificationCode" inputmode="numeric" autocomplete="one-time-code" required maxlength="6" pattern="[0-9]{6}" placeholder="6 位验证码" /><button type="button" class="auth-code-button" :disabled="smsCountdown > 0 || smsSending || authSubmitting" @click="sendAuthenticationCode">{{ smsSending ? '发送中…' : smsCountdown > 0 ? `${smsCountdown}s 后重发` : '获取验证码' }}</button></div></label>
          <label>密码<input v-model="authForm.password" type="password" :autocomplete="authMode === 'login' ? 'current-password' : 'new-password'" required minlength="6" maxlength="20" :pattern="authMode !== 'login' ? passwordPattern.source : undefined" :placeholder="authMode !== 'login' ? '6-20 位，含字母、数字、特殊符号' : '请输入密码'" /></label>
          <p v-if="authMode !== 'login'" class="auth-password-note">密码须为 6-20 位，且同时含字母、数字和特殊符号。</p>
          <div v-if="authMode === 'register'" class="auth-consents">
            <label class="auth-consent"><input v-model="authForm.agreeUserAgreement" type="checkbox" /><span>我已阅读并同意 <button type="button" @click="openLegalDocument('user-agreement')">《用户协议》</button></span></label>
            <label class="auth-consent"><input v-model="authForm.agreePrivacyPolicy" type="checkbox" /><span>我已阅读并同意 <button type="button" @click="openLegalDocument('privacy-policy')">《隐私政策》</button></span></label>
          </div>
          <p v-if="authError" class="auth-error" role="alert">{{ authError }}</p>
          <button class="auth-submit" type="submit" :disabled="authSubmitting">{{ authSubmitting ? '请稍候…' : authMode === 'login' ? '登录' : authMode === 'register' ? '注册并登录' : '重置密码并登录' }} <span>→</span></button>
          <button v-if="authMode === 'login'" class="auth-switch" type="button" :disabled="authSubmitting" @click="switchAuthMode('register')">还没有账号？立即注册</button>
          <button v-else class="auth-switch" type="button" :disabled="authSubmitting" @click="switchAuthMode('login')">返回登录</button>
          <button v-if="authMode === 'login'" class="auth-switch" type="button" :disabled="authSubmitting" @click="switchAuthMode('reset')">忘记密码？短信重置</button>
          <button v-if="authMode === 'login'" class="auth-switch" type="button" :disabled="authSubmitting" @click="loginScope = loginScope === 'admin' ? 'user' : 'admin'; authError = null">{{ loginScope === 'admin' ? '返回普通用户登录' : '管理员登录' }}</button>
          <button class="auth-browse" type="button" :disabled="authSubmitting" @click="closeAuthDialog">暂不登录，继续浏览商品</button>
        </form>
      </div>
    </section>
  </Teleport>
  <Teleport to="body">
    <section v-if="legalDialogOpen" class="profile-overlay" aria-label="协议正文" @click.self="legalDialogOpen = false">
      <article class="legal-card" role="dialog" aria-modal="true" aria-labelledby="legal-title">
        <button class="auth-close" type="button" aria-label="关闭协议" @click="legalDialogOpen = false">×</button>
        <p class="eyebrow">LEGAL DOCUMENT</p>
        <h2 id="legal-title">{{ legalLoading ? '正在加载…' : legalDocument?.title }}</h2>
        <p v-if="legalDocument" class="legal-meta">版本 {{ legalDocument.version }} · 生效于 {{ new Date(legalDocument.effective_at).toLocaleDateString('zh-CN') }}</p>
        <div v-if="legalDocument" class="legal-content">{{ legalDocument.content }}</div>
      </article>
    </section>
  </Teleport>
  <Teleport to="body">
    <section v-if="profileDialogOpen" class="profile-overlay" aria-label="个人资料" @click.self="profileDialogOpen = false">
      <form class="profile-card" role="dialog" aria-modal="true" @submit.prevent="submitProfile">
        <button class="auth-close" type="button" aria-label="关闭" :disabled="profileSubmitting" @click="profileDialogOpen = false">×</button>
        <p class="eyebrow">YOUR HOME PROFILE</p>
        <h2>个人资料</h2>
        <p class="auth-hint">修改昵称和头像后，会同步显示在你的对话中。</p>
        <label>昵称<input v-model.trim="profileForm.nickname" required maxlength="40" /></label>
        <input ref="avatarInput" class="avatar-file-input" type="file" accept="image/png,image/jpeg,image/webp" @change="selectAvatarFile(($event.target as HTMLInputElement).files?.item(0) ?? undefined)" />
        <button type="button" class="avatar-dropzone" :class="{ 'is-dragging': profileDropActive }" @click="avatarInput?.click()" @dragenter.prevent="profileDropActive = true" @dragover.prevent="profileDropActive = true" @dragleave.prevent="profileDropActive = false" @drop.prevent="onAvatarDrop">
          <img :src="profileAvatarPreview || defaultAvatarUrl" alt="头像预览" />
          <span><b>上传头像</b><small>点击选择或把图片拖到这里<br />PNG、JPEG、WebP，最大 2 MB</small></span>
        </button>
        <div class="profile-password"><p>修改密码 <small>不修改可留空</small></p><label>当前密码<input v-model="profileForm.currentPassword" type="password" autocomplete="current-password" minlength="6" maxlength="20" /></label><label>新密码<input v-model="profileForm.newPassword" type="password" autocomplete="new-password" minlength="6" maxlength="20" :pattern="passwordPattern.source" /></label><label>确认新密码<input v-model="profileForm.confirmPassword" type="password" autocomplete="new-password" minlength="6" maxlength="20" :pattern="passwordPattern.source" /></label><small class="auth-password-note">6-20 位，需同时含字母、数字和特殊符号。</small></div>
        <p v-if="profileError" class="auth-error" role="alert">{{ profileError }}</p>
        <div class="profile-actions"><button type="button" class="auth-browse" @click="profileDialogOpen = false">取消</button><button class="auth-submit" type="submit" :disabled="profileSubmitting">{{ profileSubmitting ? '保存中…' : '保存资料' }} <span>→</span></button></div>
        <button type="button" class="profile-signout" @click="profileDialogOpen = false; signOut()">退出登录</button>
      </form>
    </section>
  </Teleport>
  <Teleport to="body">
    <section v-if="knowledgeDetail" class="profile-overlay" aria-label="知识文件详情" @click.self="knowledgeDetail = null">
      <article class="legal-card knowledge-detail-card" role="dialog" aria-modal="true" aria-labelledby="knowledge-detail-title">
        <button class="auth-close" type="button" aria-label="关闭文档详情" @click="knowledgeDetail = null">×</button>
        <p class="eyebrow">KNOWLEDGE DOCUMENT</p>
        <h2 id="knowledge-detail-title">{{ knowledgeDetail.title }}</h2>
        <p class="legal-meta">{{ knowledgeDetail.original_filename || knowledgeDetail.filename }} · {{ knowledgeDetail.chunk_count }} 个切片 · {{ (knowledgeDetail.size_bytes / 1024).toFixed(1) }} KB</p>
        <dl class="knowledge-detail-meta"><div><dt>索引状态</dt><dd>{{ knowledgeStatusLabel(knowledgeDetail.ingest_status) }}</dd></div><div><dt>文档版本</dt><dd>{{ knowledgeDetail.document_version || '未记录' }}</dd></div><div><dt>SHA-256</dt><dd>{{ knowledgeDetail.sha256 || '未记录' }}</dd></div><div><dt>最近索引</dt><dd>{{ new Date(knowledgeDetail.last_indexed_at || knowledgeDetail.uploaded_at).toLocaleString('zh-CN') }}</dd></div></dl>
      </article>
    </section>
  </Teleport>
  <Teleport to="body">
    <section v-if="cancelDialogOpen" class="profile-overlay" aria-label="确认停止生成" @click.self="cancelDialogOpen = false"><div class="cancel-card" role="dialog" aria-modal="true"><p class="eyebrow">STOP GENERATION</p><h2>要停止这次回答吗？</h2><p>已生成的内容会保留在当前对话；尚未完成的检索与生成将立即取消。</p><div><button type="button" class="auth-browse" @click="cancelDialogOpen = false">继续生成</button><button type="button" class="auth-submit" @click="confirmCancelActiveRun">停止生成</button></div></div></section>
  </Teleport>
</template>
