<script setup lang="ts">
/* global document, window, IntersectionObserver, HTMLElement */
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useChatStore, type PreviewState } from './stores/chat'
import { mockPreview } from './features/chat/mock-data'
import { createAgentApi } from './api/client'
import type { AuthSession, AuthUser, ChatRequest } from './api/contracts'

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
const accessToken = ref(globalThis.localStorage.getItem('agent.access-token') ?? '')
const authUser = ref<AuthUser | null>(null)
const authLoading = ref(true)
const authMode = ref<'login' | 'register'>('login')
const authError = ref<string | null>(null)
const authForm = ref({ username: '', password: '', nickname: '' })
const defaultAvatarUrl = 'https://images.unsplash.com/photo-1534528741775-53994a69daeb?auto=format&fit=crop&w=160&q=80'
const api = createAgentApi({
  baseUrl: import.meta.env.VITE_API_BASE_URL || '/api/v1',
  accessToken: () => accessToken.value,
})
const userInitial = computed(() => authUser.value?.nickname.slice(0, 1) || '访')

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
  } catch {
    globalThis.localStorage.removeItem('agent.access-token')
    accessToken.value = ''
  } finally {
    authLoading.value = false
  }
}

function acceptAuthentication(session: AuthSession) {
  accessToken.value = session.access_token
  authUser.value = session.user
  globalThis.localStorage.setItem('agent.access-token', session.access_token)
  authError.value = null
}

async function submitAuthentication() {
  authError.value = null
  try {
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
  }
}

function signOut() {
  globalThis.localStorage.removeItem('agent.access-token')
  accessToken.value = ''
  authUser.value = null
  resetConversation()
}

function resetConversation() {
  chat.$reset()
  submittedQuestion.value = ''
  draft.value = ''
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
  } catch {
    if (!controller.signal.aborted) {
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
    chat.errorMessage = '登录状态已失效，请重新登录后继续。'
    chat.setPreviewState('error')
    return
  }
  let sessionId = chat.sessionId
  try {
    sessionId = sessionId ?? (await api.createSession('New agent session')).id
  } catch {
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
  } catch {
    chat.errorMessage = '取消请求未能送达服务端，请检查网络后重试。'
    chat.setPreviewState('error')
  }
}
</script>

<template>
  <div v-if="authLoading" class="auth-loading">正在验证登录状态…</div>
  <section v-else-if="!authUser" class="auth-shell" aria-label="登录">
    <div class="auth-promo"><p class="eyebrow">AGENT WORKSPACE</p><h1>把每一次对话，留在属于你的工作台。</h1><p>注册后会创建独立用户 ID；会话、记忆与运行记录只在你的身份范围内可见。</p><div class="auth-rule"></div><small>CONTROLLED KNOWLEDGE · TRACEABLE RUNS</small></div>
    <form class="auth-card" @submit.prevent="submitAuthentication"><img class="auth-avatar" :src="defaultAvatarUrl" alt="默认头像" /><p class="eyebrow">{{ authMode === 'login' ? 'WELCOME BACK' : 'CREATE ACCOUNT' }}</p><h2>{{ authMode === 'login' ? '登录工作台' : '创建你的工作身份' }}</h2><p class="auth-hint">{{ authMode === 'login' ? '使用已注册账号继续你的对话。' : '昵称与默认头像将展示在你的每次对话中。' }}</p><label>账号<input v-model.trim="authForm.username" autocomplete="username" required minlength="3" maxlength="32" pattern="[A-Za-z0-9_]+" placeholder="例如 agent_user" /></label><label v-if="authMode === 'register'">昵称<input v-model.trim="authForm.nickname" required maxlength="40" placeholder="例如 小王" /></label><label>密码<input v-model="authForm.password" type="password" :autocomplete="authMode === 'login' ? 'current-password' : 'new-password'" required minlength="8" maxlength="128" placeholder="至少 8 位" /></label><p v-if="authError" class="auth-error">{{ authError }}</p><button class="auth-submit" type="submit">{{ authMode === 'login' ? '登录并进入' : '注册并进入' }} <span>↗</span></button><button class="auth-switch" type="button" @click="authMode = authMode === 'login' ? 'register' : 'login'; authError = null">{{ authMode === 'login' ? '没有账号？创建一个' : '已有账号？直接登录' }}</button></form>
  </section>
  <div v-else class="app-shell" :class="{ 'nav-open': chat.navOpen }">
    <aside class="sidebar" aria-label="会话导航">
      <div class="brand-lockup"><span class="brand-mark">程</span><span>规程台</span><small>OPERATION DESK</small></div>
      <button class="new-session" type="button" @click="resetConversation"><span>＋</span>新建会话 <kbd>⌘ K</kbd></button>
      <div class="sidebar-label">近期会话</div>
      <nav class="session-list">
        <button v-for="(session, index) in mockPreview.sessions" :key="session" class="session" :class="{ active: index === 0 }" type="button"><b>{{ session }}</b><span>{{ index === 0 ? '刚刚 · 运行中' : index === 1 ? '今天 10:42' : '昨天' }}</span></button>
      </nav>
      <div class="sidebar-foot"><span class="presence"></span><div><b>{{ authUser.nickname }}</b><small>独立会话空间 · 已连接</small></div><button aria-label="退出登录" title="退出登录" @click="signOut">退出</button></div>
    </aside>

    <main class="workspace">
      <header class="topbar">
        <button class="menu-button" type="button" aria-label="打开会话列表" @click="chat.toggleNav">☰</button>
        <div class="crumb"><span>会话 /</span> {{ mockPreview.title }} <em>v1.0</em></div>
        <div class="header-actions"><span class="secure-dot">受控模式</span><button type="button" class="avatar" :aria-label="`${authUser.nickname}，退出登录`" @click="signOut"><img :src="authUser.avatar_url" :alt="`${authUser.nickname}的头像`" /><span>{{ userInitial }}</span></button></div>
      </header>

      <section class="stage" aria-label="聊天工作区">
        <div class="thread-head" data-reveal><div><p class="eyebrow">CASE · {{ mockPreview.caseId }}</p><h1>{{ mockPreview.title }}</h1><p>{{ mockPreview.summary }}</p></div><button class="trace-link" type="button"><span>◉</span>运行追踪 <b>{{ chat.runId || '尚未运行' }}</b><i>↗</i></button></div>
        <div class="thread-rule"></div>
        <article class="message customer" data-reveal><div class="message-meta"><span class="message-avatar user"><img :src="authUser.avatar_url" :alt="`${authUser.nickname}的头像`" /><i>{{ userInitial }}</i></span><b>{{ authUser.nickname }}</b><time>刚刚</time></div><p>{{ submittedQuestion || mockPreview.question }}</p></article>

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
      <footer class="composer-wrap"><div class="state-switcher" aria-label="静态状态预览"><span>审阅状态</span><button v-for="state in states" :key="state.value" type="button" :class="{ selected: chat.previewState === state.value }" @click="chat.setPreviewState(state.value)">{{ state.label }}</button></div><form class="composer" @submit.prevent="sendMessage"><textarea v-model="draft" aria-label="消息输入" placeholder="询问知识库，或输入一条客服处理需求…" :disabled="chat.previewState === 'disabled' || chat.previewState === 'loading'"></textarea><div class="composer-bar"><button type="button" class="attach" aria-label="添加附件">＋</button><span>仅使用受控知识库 · 不发送隐私信息</span><button v-if="chat.previewState === 'loading' && chat.runId" type="button" class="send" @click="cancelActiveRun">停止</button><button v-else type="submit" class="send" :disabled="chat.previewState === 'disabled' || chat.previewState === 'loading' || !draft.trim()">发送 <b>↑</b></button></div></form></footer>
    </main>
    <button class="scrim" aria-label="关闭会话列表" @click="chat.closeNav"></button>
  </div>
</template>
