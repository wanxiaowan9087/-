<script setup lang="ts">
import { computed } from 'vue'
import { useChatStore, type PreviewState } from './stores/chat'
import { mockPreview } from './features/chat/mock-data'

const chat = useChatStore()
const states: { value: PreviewState; label: string }[] = [
  { value: 'ready', label: '对话' }, { value: 'loading', label: '生成中' },
  { value: 'empty', label: '空会话' }, { value: 'error', label: '异常' }, { value: 'disabled', label: '受限' },
]
const isOverlay = computed(() => ['empty', 'loading', 'error', 'disabled'].includes(chat.previewState))
</script>

<template>
  <div class="app-shell" :class="{ 'nav-open': chat.navOpen }">
    <aside class="sidebar" aria-label="会话导航">
      <div class="brand-lockup"><span class="brand-mark">稽</span><span>规程台</span><small>OPERATION DESK</small></div>
      <button class="new-session" type="button"><span>＋</span>新建会话 <kbd>⌘ K</kbd></button>
      <div class="sidebar-label">近期会话</div>
      <nav class="session-list">
        <button v-for="(session, index) in mockPreview.sessions" :key="session" class="session" :class="{ active: index === 0 }" type="button"><b>{{ session }}</b><span>{{ index === 0 ? '刚刚 · 运行中' : index === 1 ? '今天 10:42' : '昨天' }}</span></button>
      </nav>
      <div class="sidebar-foot"><span class="presence"></span><div><b>客服运营组</b><small>受控知识库 · 已连接</small></div><button aria-label="更多设置">•••</button></div>
    </aside>

    <main class="workspace">
      <header class="topbar">
        <button class="menu-button" type="button" aria-label="打开会话列表" @click="chat.toggleNav">☰</button>
        <div class="crumb"><span>会话 /</span> {{ mockPreview.title }} <em>v1.0 静态预览</em></div>
        <div class="header-actions"><span class="secure-dot">受控模式</span><button type="button" class="avatar" aria-label="当前用户">唐</button></div>
      </header>

      <section class="stage" aria-label="聊天工作区">
        <div class="thread-head"><div><p class="eyebrow">CASE · {{ mockPreview.caseId }}</p><h1>{{ mockPreview.title }}</h1><p>{{ mockPreview.summary }}</p></div><button class="trace-link" type="button"><span>◌</span>运行追踪 <b>run_01HZX…</b><i>→</i></button></div>
        <div class="thread-rule"></div>
        <article class="message customer"><div class="message-meta"><span class="message-avatar user">唐</span><b>唐世均</b><time>10:58</time></div><p>{{ mockPreview.question }}</p></article>
        <article class="message agent withheld"><div class="message-meta"><span class="message-avatar bot">稽</span><b>规程台助手</b><span class="model-chip">内容已扣留</span><time>10:58</time></div>
          <section class="withheld-card" aria-label="候选答案已扣留，等待人工审核">
            <div class="withheld-seal" aria-hidden="true"><span></span><span></span><span></span></div>
            <div><p class="eyebrow">DRAFT WITHHELD</p><h2>候选答案等待人工审核</h2><p>{{ mockPreview.reviewReason }}</p></div>
            <span class="withheld-code">POLICY · R-04</span>
          </section>
          <section class="tool-card"><div class="tool-top"><span class="tool-icon">⌁</span><div><b>知识库检索</b><small>hybrid_search · 842 ms</small></div><span class="tool-ok">已完成</span></div><div class="tool-detail"><span>召回 12 个片段</span><span>重排 Top 3</span><span>置信度 0.86</span></div></section>
          <section class="sources"><div class="sources-head"><span>依据资料</span><small>2 条可定位引用</small></div><div class="source-grid"><button class="source-card" type="button"><span class="source-index">01</span><div><b>扫拖一体机器人 100 问</b><p>第 41 节 · 回充失败排查</p></div><i>↗</i></button><button class="source-card" type="button"><span class="source-index">02</span><div><b>维护保养指南</b><p>第 3.2 节 · 传感器与触点</p></div><i>↗</i></button></div></section>
          <section class="review-card"><div class="review-mark">◇</div><div><p class="eyebrow">HUMAN REVIEW</p><b>审核队列已接收</b><small>候选正文不会向普通用户渲染；批准后再发布。</small></div><span class="review-status">待审核</span></section>
        </article>
        <section v-if="isOverlay" class="state-panel" :class="chat.previewState" aria-live="polite">
          <span class="state-glyph">{{ chat.previewState === 'loading' ? '◌' : chat.previewState === 'error' ? '!' : chat.previewState === 'disabled' ? '⌧' : '—' }}</span>
          <h2>{{ chat.previewState === 'loading' ? '正在编排本次运行' : chat.previewState === 'error' ? '本次演示未能完成' : chat.previewState === 'disabled' ? '当前输入已受策略限制' : '这里还没有消息' }}</h2>
          <p>{{ chat.previewState === 'loading' ? '正在检索知识库并检查策略，请勿将此静态演示视为实时结果。' : chat.previewState === 'error' ? '模拟依赖不可用时的安全反馈。真实接入后可重试或查看运行追踪。' : chat.previewState === 'disabled' ? '模拟高风险操作的禁用态；请修改请求或提交人工审核。' : '输入一个问题即可开始新会话。' }}</p>
          <button v-if="chat.previewState === 'error'" type="button" @click="chat.setPreviewState('ready')">返回对话</button>
        </section>
      </section>
      <footer class="composer-wrap"><div class="state-switcher" aria-label="静态状态预览"><span>审阅状态</span><button v-for="state in states" :key="state.value" type="button" :class="{ selected: chat.previewState === state.value }" @click="chat.setPreviewState(state.value)">{{ state.label }}</button></div><form class="composer" @submit.prevent><textarea aria-label="消息输入" placeholder="询问知识库，或输入一条客服处理需求…" :disabled="chat.previewState === 'disabled'"></textarea><div class="composer-bar"><button type="button" class="attach" aria-label="添加附件">＋</button><span>仅使用受控知识库 · 不发送隐私信息</span><button type="submit" class="send" :disabled="chat.previewState === 'disabled'">发送 <b>↑</b></button></div></form></footer>
    </main>
    <button class="scrim" aria-label="关闭会话列表" @click="chat.closeNav"></button>
  </div>
</template>
