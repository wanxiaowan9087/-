<script setup lang="ts">
/* global document, window, IntersectionObserver, HTMLElement, File, HTMLInputElement, URL, DragEvent */
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useChatStore, type PreviewState } from './stores/chat'
import { mockPreview } from './features/chat/mock-data'
import RobotHero from './features/chat/RobotHero.vue'
import { ApiClientError, createAgentApi } from './api/client'
import type { AuthSession, AuthUser, ChatRequest, Memory, Message, Session } from './api/contracts'
import {
  clearStoredAuthSession,
  loadStoredAccessToken,
  persistAuthSession,
  renewStoredAuthSession,
} from './features/auth/session'

const chat = useChatStore()
const states: { value: PreviewState; label: string }[] = [
  { value: 'ready', label: '对话' },
  { value: 'loading', label: '生成中' },
  { value: 'empty', label: '空会话' },
  { value: 'error', label: '异常' },
  { value: 'disabled', label: '受限' },
]
const isOverlay = computed(() => ['empty', 'loading', 'error'].includes(chat.previewState))
const latestTool = computed(() => chat.tools.at(-1) ?? null)
const draft = ref('')
const submittedQuestion = ref('')
const activeAbortController = ref<InstanceType<typeof globalThis.AbortController> | null>(null)
type ProtectedAction = { type: 'agent' } | { type: 'product'; productId: string }

const accessToken = ref(loadStoredAccessToken())
const authUser = ref<AuthUser | null>(null)
const authLoading = ref(true)
const authSubmitting = ref(false)
const authDialogOpen = ref(false)
const pendingAuthAction = ref<ProtectedAction | null>(null)
const requestedProductId = ref<string | null>(null)
const authMode = ref<'login' | 'register'>('login')
const authError = ref<string | null>(null)
const authForm = ref({ username: '', password: '', nickname: '' })
const profileDialogOpen = ref(false)
const profileSubmitting = ref(false)
const profileError = ref<string | null>(null)
const profileForm = ref({ nickname: '', currentPassword: '', newPassword: '', confirmPassword: '' })
const profileAvatarFile = ref<File | null>(null)
const profileAvatarPreview = ref('')
const profileDropActive = ref(false)
const avatarInput = ref<HTMLInputElement | null>(null)
const cancelDialogOpen = ref(false)
const currentView = ref<'showcase' | 'agent'>('showcase')
const defaultAvatarUrl = 'https://images.unsplash.com/photo-1534528741775-53994a69daeb?auto=format&fit=crop&w=160&q=80'
const api = createAgentApi({
  baseUrl: import.meta.env.VITE_API_BASE_URL || '/api/v1',
  accessToken: () => accessToken.value,
  onAuthenticatedResponse: () => renewStoredAuthSession(accessToken.value),
})
const userInitial = computed(() => authUser.value?.nickname.slice(0, 1) || '访')
const sessions = ref<Session[]>([])
const historicalMessages = ref<Message[]>([])
const memories = ref<Memory[]>([])
const historyLoading = ref(false)
const historyError = ref<string | null>(null)
const activeSessionTitle = computed(() => sessions.value.find(item => item.id === chat.sessionId)?.title || mockPreview.title)
const visibleHistoricalMessages = computed(() => historicalMessages.value.filter(message => {
  if (!message.content) return false
  if (message.id === chat.userMessageId) return false
  if (chat.runId && message.run_id === chat.runId) return false
  return true
}))

let revealObserver: IntersectionObserver | undefined

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

onBeforeUnmount(() => revealObserver?.disconnect())

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
  } catch {
    clearStoredAuthSession()
    accessToken.value = ''
  } finally {
    authLoading.value = false
    void nextTick(() => observeReveals())
  }
}

function acceptAuthentication(session: AuthSession) {
  accessToken.value = session.access_token
  authUser.value = session.user
  persistAuthSession(session)
  authError.value = null
  authDialogOpen.value = false
  const action = pendingAuthAction.value
  pendingAuthAction.value = null
  authForm.value.password = ''
  if (action?.type === 'agent') {
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
    if (authMode.value === 'register' && authForm.value.password.length < 12) {
      throw new Error('为了保护账号，请使用至少 12 位且未在其他网站使用过的密码。')
    }
    const session = authMode.value === 'login'
      ? await api.login({ username: authForm.value.username, password: authForm.value.password })
      : await api.register({
          username: authForm.value.username,
          password: authForm.value.password,
          nickname: authForm.value.nickname,
        })
    acceptAuthentication(session)
  } catch (error) {
    authError.value = error instanceof Error ? error.message : '身份验证失败，请稍后重试。'
  } finally {
    authSubmitting.value = false
  }
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
      if (profileForm.value.newPassword.length < 12) throw new Error('新密码至少需要 12 位。')
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

function openAuthDialog(action: ProtectedAction | null = null) {
  pendingAuthAction.value = action
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
  historicalMessages.value = []
}

async function refreshConversationState() {
  if (!accessToken.value) return
  historyLoading.value = true
  historyError.value = null
  try {
    const [sessionPage, memoryPage] = await Promise.all([
      api.listSessions(),
      api.listMemories(),
    ])
    sessions.value = sessionPage.items
    memories.value = memoryPage.items
    if (chat.sessionId) {
      await refreshSessionMessages(chat.sessionId)
    }
  } catch (error) {
    historyError.value = error instanceof Error ? error.message : '历史会话加载失败'
  } finally {
    historyLoading.value = false
  }
}

async function refreshSessionMessages(sessionId: string) {
  const page = await api.listMessages(sessionId)
  historicalMessages.value = page.items
}

async function openSession(sessionId: string) {
  if (chat.previewState === 'loading') return
  chat.$reset()
  submittedQuestion.value = ''
  draft.value = ''
  chat.sessionId = sessionId
  historyError.value = null
  try {
    await refreshSessionMessages(sessionId)
    chat.setPreviewState(historicalMessages.value.length ? 'ready' : 'empty')
    chat.closeNav()
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

function requestProductAccess(productId: string) {
  openAuthDialog({ type: 'product', productId })
}

function openShowcase() {
  currentView.value = 'showcase'
  chat.closeNav()
  globalThis.scrollTo({ top: 0, behavior: 'smooth' })
}

async function executeChat(request: ChatRequest, question: string) {
  const controller = new globalThis.AbortController()
  activeAbortController.value = controller
  try {
    chat.beginRun(request.session_id)
    submittedQuestion.value = question
    await api.streamChat(request, {
      idempotencyKey: globalThis.crypto.randomUUID(),
      lastEventId: chat.lastEventId ?? undefined,
      signal: controller.signal,
      onEvent: event => chat.receiveStreamEvent(event),
    })
    draft.value = ''
    await refreshConversationState()
  } catch (error) {
    if (!controller.signal.aborted) {
      if (error instanceof ApiClientError && error.status === 401) {
        expireAuthentication({ type: 'agent' })
        return
      }
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
    sessionId = sessionId ?? (await api.createSession('New agent session')).id
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
      <template v-else>
      <button class="showcase-return" type="button" @click="openShowcase"><span>←</span> 返回产品展厅</button>
      <div class="brand-lockup"><span class="brand-mark">智</span><span>小智问答</span><small>AGENT DESK</small></div>
      <button class="new-session" type="button" @click="resetConversation"><span>＋</span>新建会话 <kbd>⌘ K</kbd></button>
      <div class="sidebar-label">近期会话</div>
      <nav class="session-list">
        <button v-for="session in sessions" :key="session.id" class="session" :class="{ active: session.id === chat.sessionId }" type="button" @click="openSession(session.id)"><b>{{ session.title }}</b><span>{{ session.last_message_at ? new Date(session.last_message_at).toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' }) : '尚未开始' }}</span></button>
        <p v-if="!sessions.length" class="session-empty">{{ historyLoading ? '正在同步历史...' : '暂无历史会话' }}</p>
      </nav>
      <p v-if="historyError" class="session-error">{{ historyError }}</p>
      <div class="sidebar-foot"><span class="presence"></span><div><b>{{ authUser?.nickname || '访客' }}</b><small>{{ memories.length ? `${memories.length} 条长期记忆` : '7 天内自动登录' }}</small></div><button aria-label="退出登录" title="退出登录" @click="signOut">退出</button></div>
      </template>
    </aside>

    <main class="workspace" :class="{ 'workspace--agent': currentView === 'agent' }">
      <header v-if="currentView === 'showcase'" class="topbar topbar--showcase">
        <div class="crumb"><span>ZENMOP /</span> 智能清洁产品展厅 <em>2026 COLLECTION</em></div>
        <div class="header-actions">
          <span class="secure-dot">{{ authLoading ? '正在检查登录' : '服务已就绪' }}</span>
          <button v-if="!authUser" class="topbar-login-entry" type="button" @click="openAuthDialog()">登录 / 注册</button>
          <button v-else type="button" class="avatar" :aria-label="`${authUser.nickname}，打开个人资料`" @click="openProfile"><img :src="authUser.avatar_url" :alt="`${authUser.nickname}的头像`" /><span>{{ userInitial }}</span></button>
          <button class="topbar-agent-entry" type="button" @click="openAgentDesk">小智问答 <span>→</span></button>
        </div>
      </header>
      <header v-else class="topbar">
        <button class="menu-button" type="button" aria-label="打开会话列表" @click="chat.toggleNav">☰</button>
        <div class="crumb"><span>会话 /</span> {{ activeSessionTitle }} <em>v1.0</em></div>
        <div class="header-actions"><span class="secure-dot">已登录</span><button type="button" class="avatar" :aria-label="`${authUser?.nickname || '用户'}，打开个人资料`" @click="openProfile"><img :src="authUser?.avatar_url || defaultAvatarUrl" :alt="`${authUser?.nickname || '用户'}的头像`" /><span>{{ userInitial }}</span></button></div>
      </header>

      <RobotHero v-if="currentView === 'showcase'" :authenticated="Boolean(authUser)" :requested-product-id="requestedProductId" @consult="openAgentDesk" @auth-required="requestProductAccess" />
      <template v-else>
      <section id="agent-desk" class="stage" aria-label="聊天工作区">
        <div class="thread-head" data-reveal><div><p class="eyebrow">CASE · {{ mockPreview.caseId }}</p><h1>{{ mockPreview.title }}</h1><p>{{ mockPreview.summary }}</p></div><button class="trace-link" type="button"><span>◉</span>运行追踪 <b>{{ chat.runId || '尚未运行' }}</b><i>↗</i></button></div>
        <div class="thread-rule"></div>
        <article v-for="message in visibleHistoricalMessages" :key="message.id" class="message" :class="message.role === 'user' ? 'customer' : 'agent'" data-reveal>
          <div class="message-meta">
            <span v-if="message.role === 'user'" class="message-avatar user"><img :src="authUser?.avatar_url || defaultAvatarUrl" :alt="`${authUser?.nickname || '用户'}的头像`" /><i>{{ userInitial }}</i></span>
            <span v-else class="message-avatar bot">程</span>
            <b>{{ message.role === 'user' ? authUser?.nickname || '用户' : '规程台助手' }}</b>
            <span v-if="message.role === 'assistant'" class="model-chip">{{ message.status === 'completed' ? '已完成' : message.status }}</span>
            <time>{{ new Date(message.created_at).toLocaleString('zh-CN', { hour: '2-digit', minute: '2-digit' }) }}</time>
          </div>
          <p v-if="message.role === 'user'">{{ message.content }}</p>
          <section v-else class="answer-card">{{ message.content }}</section>
          <section v-if="message.citations?.length" class="sources">
            <div class="sources-head"><span>依据资料</span><small>{{ message.citations.length }} 条可定位引用</small></div>
            <div class="source-grid"><button v-for="citation in message.citations" :key="`${message.id}:${citation.chunk_id}`" class="source-card" type="button"><span class="source-index">#</span><div><b>{{ citation.title }}</b><p>{{ citation.source }}{{ citation.page ? ` · page ${citation.page}` : '' }}</p></div><i>↗</i></button></div>
          </section>
        </article>

        <article v-if="submittedQuestion" class="message customer" data-reveal><div class="message-meta"><span class="message-avatar user"><img :src="authUser?.avatar_url || defaultAvatarUrl" :alt="`${authUser?.nickname || '用户'}的头像`" /><i>{{ userInitial }}</i></span><b>{{ authUser?.nickname || '用户' }}</b><time>刚刚</time></div><p>{{ submittedQuestion }}</p></article>

        <article v-if="chat.runId" class="message agent" data-reveal :class="{ withheld: Boolean(chat.review) }">
          <div class="message-meta"><span class="message-avatar bot">程</span><b>规程台助手</b><span class="model-chip">{{ chat.previewState === 'loading' ? '正在生成' : chat.review ? '等待审核' : chat.runOutcome === 'cancelled' ? '已取消' : '已完成' }}</span><time>刚刚</time></div>
          <section v-if="chat.review" class="withheld-card" aria-label="候选答案已扣留，等待人工审核">
            <div class="withheld-seal" aria-hidden="true"><span></span><span></span><span></span></div>
            <div><p class="eyebrow">DRAFT WITHHELD</p><h2>候选答案等待人工审核</h2><p>{{ chat.review.reasonCodes.join(' · ') || '运行策略要求人工审核' }}</p></div>
            <span class="withheld-code">{{ chat.review.reviewId || 'PENDING' }}</span>
          </section>
          <section v-else-if="chat.assistantText" class="answer-card" aria-live="polite">{{ chat.assistantText }}</section>
          <section v-else class="tool-card"><div class="tool-top"><span class="tool-icon">↻</span><div><b>{{ chat.runOutcome === 'cancelled' ? '本次运行已取消' : latestTool ? `工具：${latestTool.toolName}` : '正在调用受控 Agent' }}</b><small>{{ chat.runOutcome === 'cancelled' ? '已通知服务端停止执行，候选内容不会发布。' : latestTool?.detail || chat.lastStatus || '检索、重排与安全策略检查中' }}</small></div><span class="tool-ok">{{ chat.runOutcome === 'cancelled' ? '已取消' : latestTool?.outcome || '运行中' }}</span></div></section>
          <section v-if="chat.citations.length" class="sources"><div class="sources-head"><span>依据资料</span><small>{{ chat.citations.length }} 条可定位引用</small></div><div class="source-grid"><button v-for="(citation, index) in chat.citations" :key="`${citation.documentVersion}:${citation.chunkId}`" class="source-card" type="button"><span class="source-index">{{ String(index + 1).padStart(2, '0') }}</span><div><b>{{ citation.title }}</b><p>{{ citation.locator }} · {{ citation.documentVersion }}</p></div><i>↗</i></button></div></section>
          <section v-if="chat.review" class="review-card"><div class="review-mark">◉</div><div><p class="eyebrow">HUMAN REVIEW</p><b>审核队列已接收</b><small>候选正文不会向普通用户透露；批准后才会发布。</small></div><span class="review-status">待审核</span></section>
        </article>

        <section v-if="isOverlay" class="state-panel" :class="chat.previewState" aria-live="polite">
          <span class="state-glyph">{{ chat.previewState === 'loading' ? '◌' : chat.previewState === 'error' ? '!' : '—' }}</span>
          <h2>{{ chat.previewState === 'loading' ? '正在编排本次运行' : chat.previewState === 'error' ? '本次运行未能完成' : '这里还没有消息' }}</h2>
          <p>{{ chat.previewState === 'loading' ? '正在检索知识库并检查策略，请稍候。' : chat.errorMessage || '输入一个问题即可开始新会话。' }}</p>
          <button v-if="chat.previewState === 'error' && chat.userMessageId" type="button" @click="retryLastMessage">重试本次运行</button><button v-else-if="chat.previewState === 'error'" type="button" @click="chat.setPreviewState('ready')">返回对话</button>
        </section>
      </section>
      <footer class="composer-wrap"><div class="state-switcher" aria-label="静态状态预览"><span>审阅状态</span><button v-for="state in states" :key="state.value" type="button" :class="{ selected: chat.previewState === state.value }" @click="chat.setPreviewState(state.value)">{{ state.label }}</button></div><form class="composer" @submit.prevent="sendMessage"><textarea v-model="draft" aria-label="消息输入" placeholder="询问知识库，或输入一条客服处理需求…" :disabled="chat.previewState === 'disabled' || chat.previewState === 'loading'"></textarea><div class="composer-bar"><button type="button" class="attach" aria-label="添加附件">＋</button><span>仅使用受控知识库 · 不发送隐私信息</span><button v-if="chat.previewState === 'loading' && chat.runId" type="button" class="send" @click="requestCancelActiveRun">停止</button><button v-else type="submit" class="send" :disabled="chat.previewState === 'disabled' || chat.previewState === 'loading' || !draft.trim()">发送 <b>↑</b></button></div></form></footer>
      </template>
    </main>
    <button class="scrim" aria-label="关闭会话列表" @click="chat.closeNav"></button>
  </div>
  <Teleport to="body">
    <section v-if="authDialogOpen" class="auth-overlay" aria-label="账号登录" @click.self="closeAuthDialog">
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
          <div class="auth-card__top"><img class="auth-avatar" :src="defaultAvatarUrl" alt="默认头像" /><span>{{ authMode === 'login' ? '欢迎回来' : '成为 ZENMOP 用户' }}</span></div>
          <p class="eyebrow">{{ authMode === 'login' ? 'WELCOME BACK' : 'CREATE ACCOUNT' }}</p>
          <h2 :id="`auth-title-${authMode}`">{{ authMode === 'login' ? '登录账号' : '创建账号' }}</h2>
          <p class="auth-hint">{{ authMode === 'login' ? '继续查看商品详情，或与小智聊聊你的清洁需求。' : '注册后即可保存你的对话，并获得个性化建议。' }}</p>
          <label>账号<input v-model.trim="authForm.username" autocomplete="username" required minlength="3" maxlength="32" pattern="[A-Za-z0-9_]+" placeholder="请输入账号" /></label>
          <label v-if="authMode === 'register'">昵称<input v-model.trim="authForm.nickname" required maxlength="40" placeholder="用于对话中的称呼" /></label>
          <label>密码<input v-model="authForm.password" type="password" :autocomplete="authMode === 'login' ? 'current-password' : 'new-password'" required :minlength="authMode === 'register' ? 12 : 8" maxlength="128" :placeholder="authMode === 'register' ? '至少 12 位，勿与其他网站重复' : '请输入密码'" /></label>
          <p v-if="authMode === 'register'" class="auth-password-note">请使用只在这里使用过的长密码；浏览器的泄露提示通常来自重复使用的旧密码。</p>
          <p v-if="authError" class="auth-error" role="alert">{{ authError }}</p>
          <button class="auth-submit" type="submit" :disabled="authSubmitting">{{ authSubmitting ? '请稍候…' : authMode === 'login' ? '登录' : '注册并登录' }} <span>→</span></button>
          <button class="auth-switch" type="button" :disabled="authSubmitting" @click="authMode = authMode === 'login' ? 'register' : 'login'; authError = null">{{ authMode === 'login' ? '还没有账号？立即注册' : '已有账号？返回登录' }}</button>
          <button class="auth-browse" type="button" :disabled="authSubmitting" @click="closeAuthDialog">暂不登录，继续浏览商品</button>
        </form>
      </div>
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
        <div class="profile-password"><p>修改密码 <small>不修改可留空</small></p><label>当前密码<input v-model="profileForm.currentPassword" type="password" autocomplete="current-password" minlength="8" /></label><label>新密码<input v-model="profileForm.newPassword" type="password" autocomplete="new-password" minlength="12" /></label><label>确认新密码<input v-model="profileForm.confirmPassword" type="password" autocomplete="new-password" minlength="12" /></label></div>
        <p v-if="profileError" class="auth-error" role="alert">{{ profileError }}</p>
        <div class="profile-actions"><button type="button" class="auth-browse" @click="profileDialogOpen = false">取消</button><button class="auth-submit" type="submit" :disabled="profileSubmitting">{{ profileSubmitting ? '保存中…' : '保存资料' }} <span>→</span></button></div>
        <button type="button" class="profile-signout" @click="profileDialogOpen = false; signOut()">退出登录</button>
      </form>
    </section>
  </Teleport>
  <Teleport to="body">
    <section v-if="cancelDialogOpen" class="profile-overlay" aria-label="确认停止生成" @click.self="cancelDialogOpen = false"><div class="cancel-card" role="dialog" aria-modal="true"><p class="eyebrow">STOP GENERATION</p><h2>要停止这次回答吗？</h2><p>已生成的内容会保留在当前对话；尚未完成的检索与生成将立即取消。</p><div><button type="button" class="auth-browse" @click="cancelDialogOpen = false">继续生成</button><button type="button" class="auth-submit" @click="confirmCancelActiveRun">停止生成</button></div></div></section>
  </Teleport>
</template>
