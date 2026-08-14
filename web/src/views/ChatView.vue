<script setup lang="ts">
import { ArrowDown, ChatLineRound, CirclePlus, Promotion, View } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRouter } from 'vue-router'

import { api, errorMessage } from '@/api/client'
import { subscribeToRun } from '@/api/events'
import type { Agent, Capability, Message, Run, RunEvent, Session } from '@/api/types'
import RunStatusTag from '@/components/RunStatusTag.vue'

const router = useRouter()
const loading = ref(false)
const sending = ref(false)
const agents = ref<Agent[]>([])
const sessions = ref<Session[]>([])
const messages = ref<Message[]>([])
const events = ref<RunEvent[]>([])
const capability = ref<Capability>()
const selectedAgentId = ref('')
const selectedSessionId = ref('')
const input = ref('')
const liveOutput = ref('')
const currentRun = ref<Run>()
const streamWarning = ref(false)
const messagePanel = ref<HTMLElement>()
const stepsOpen = ref(true)
let closeStream: (() => void) | undefined

const publishedAgents = computed(() => agents.value.filter((agent) => agent.latest_published_version_id))
const selectedSession = computed(() => sessions.value.find((item) => item.id === selectedSessionId.value))
const canSend = computed(() => Boolean(capability.value?.model_configured && selectedSessionId.value && input.value.trim() && !sending.value))
const runSteps = computed(() => events.value.filter((event) => event.event_type !== 'model.delta'))
const showWelcome = computed(() => Boolean(selectedSessionId.value) && !messages.value.length && !liveOutput.value && !runSteps.value.length)

const suggestions = ['计算 20 + 22', '你现在可以使用哪些工具？', '介绍一下你自己']

function eventTone(eventType: string): 'model' | 'tool' | 'success' | 'danger' | 'neutral' {
  if (eventType === 'run.completed') return 'success'
  if (eventType === 'run.failed' || eventType === 'run.cancelled') return 'danger'
  if (eventType.startsWith('tool.')) return 'tool'
  if (eventType.startsWith('run.')) return 'neutral'
  return 'model'
}

function useSuggestion(text: string) {
  input.value = text
}

async function loadInitial() {
  loading.value = true
  try {
    ;[agents.value, sessions.value, capability.value] = await Promise.all([
      api.listAgents(),
      api.listSessions(),
      api.capabilities(),
    ])
    if (sessions.value[0]) await selectSession(sessions.value[0].id)
    if (publishedAgents.value[0]) selectedAgentId.value = publishedAgents.value[0].id
  } catch (error) {
    ElMessage.error(errorMessage(error))
  } finally {
    loading.value = false
  }
}

async function createSession() {
  if (!selectedAgentId.value) return
  try {
    const agent = agents.value.find((item) => item.id === selectedAgentId.value)
    const session = await api.createSession(selectedAgentId.value, `${agent?.name ?? 'Agent'} 会话`)
    sessions.value.unshift(session)
    await selectSession(session.id)
    ElMessage.success('会话已创建，并固定到当前发布版本')
  } catch (error) {
    ElMessage.error(errorMessage(error))
  }
}

async function selectSession(sessionId: string) {
  closeStream?.()
  selectedSessionId.value = sessionId
  events.value = []
  liveOutput.value = ''
  currentRun.value = undefined
  try {
    messages.value = await api.listMessages(sessionId)
    await scrollToBottom()
  } catch (error) {
    ElMessage.error(errorMessage(error))
  }
}

function handleRunEvent(event: RunEvent) {
  if (!events.value.some((item) => item.sequence === event.sequence)) events.value.push(event)
  if (event.event_type === 'model.delta') liveOutput.value += String(event.payload.delta ?? '')
  if (['run.completed', 'run.failed', 'run.cancelled'].includes(event.event_type)) void finishRun()
  void scrollToBottom()
}

async function finishRun() {
  if (!currentRun.value) return
  try {
    currentRun.value = await api.getRun(currentRun.value.id)
    messages.value = await api.listMessages(selectedSessionId.value)
    liveOutput.value = ''
  } catch (error) {
    ElMessage.error(errorMessage(error))
  } finally {
    sending.value = false
  }
}

async function send() {
  if (!canSend.value) return
  const content = input.value.trim()
  input.value = ''
  sending.value = true
  liveOutput.value = ''
  events.value = []
  stepsOpen.value = true
  streamWarning.value = false
  try {
    currentRun.value = await api.createRun(selectedSessionId.value, content)
    messages.value = await api.listMessages(selectedSessionId.value)
    closeStream = subscribeToRun(currentRun.value.id, handleRunEvent, () => {
      streamWarning.value = true
    })
    await scrollToBottom()
  } catch (error) {
    sending.value = false
    input.value = content
    ElMessage.error(errorMessage(error))
  }
}

async function scrollToBottom() {
  await nextTick()
  if (messagePanel.value) messagePanel.value.scrollTop = messagePanel.value.scrollHeight
}

onMounted(loadInitial)
onBeforeUnmount(() => closeStream?.())
</script>

<template>
  <section v-loading="loading" class="chat-layout">
    <aside class="surface session-panel">
      <div class="panel-title"><div><strong>会话</strong><span>{{ sessions.length }} 条</span></div></div>
      <div class="new-session">
        <el-select v-model="selectedAgentId" placeholder="选择已发布 Agent" style="width: 100%">
          <el-option v-for="agent in publishedAgents" :key="agent.id" :label="agent.name" :value="agent.id" />
        </el-select>
        <el-button type="primary" :icon="CirclePlus" :disabled="!selectedAgentId" @click="createSession">创建会话</el-button>
      </div>
      <div class="session-list">
        <button v-for="session in sessions" :key="session.id" type="button" :class="{ active: session.id === selectedSessionId }" @click="selectSession(session.id)">
          <el-icon><ChatLineRound /></el-icon><span><strong>{{ session.title }}</strong><small class="mono">版本 {{ session.agent_version_id.slice(0, 8) }}</small></span>
        </button>
        <el-empty v-if="!sessions.length" :image-size="70" description="还没有会话，选择一个 Agent 开始吧" />
      </div>
    </aside>

    <main class="surface conversation-panel">
      <header class="conversation-header">
        <div><strong>{{ selectedSession?.title ?? '请选择或创建会话' }}</strong></div>
        <div v-if="currentRun" class="run-link"><RunStatusTag :status="currentRun.status" /><el-button text :icon="View" @click="router.push(`/runs/${currentRun.id}`)">Run 详情</el-button></div>
      </header>

      <el-alert v-if="capability && !capability.model_configured" title="模型尚未配置" description="请在 .env 中配置 MODEL_API_KEY 与 MODEL_NAME；平台管理能力仍可正常使用。" type="warning" :closable="false" show-icon />
      <el-alert v-if="streamWarning" title="事件流暂时中断，浏览器正在自动重连" type="info" :closable="false" show-icon />

      <div ref="messagePanel" class="message-panel">
        <div class="conversation-column">
          <div v-if="showWelcome" class="welcome">
            <span class="welcome-mark"><ChatLineRound /></span>
            <h3>开始新的对话</h3>
            <p>输入任务，Agent 会调用工具并实时返回运行过程。</p>
            <div class="suggestion-chips">
              <button v-for="item in suggestions" :key="item" type="button" @click="useSuggestion(item)">{{ item }}</button>
            </div>
          </div>

          <div v-for="message in messages" :key="message.id" class="message" :class="message.role.toLowerCase()">
            <span class="role"><i class="role-dot" />{{ message.role === 'USER' ? '你' : message.role === 'ASSISTANT' ? 'Agent' : '工具' }}</span>
            <div>{{ message.content }}</div>
          </div>

          <div v-if="runSteps.length" class="run-steps" :class="{ live: sending }">
            <button class="run-steps-head" type="button" @click="stepsOpen = !stepsOpen">
              <span class="steps-title"><i class="steps-dot" />运行过程 · {{ runSteps.length }} 步</span>
              <el-icon class="steps-arrow" :class="{ open: stepsOpen }"><ArrowDown /></el-icon>
            </button>
            <div v-show="stepsOpen" class="run-steps-body">
              <div v-for="event in runSteps" :key="event.sequence" class="step" :data-tone="eventTone(event.event_type)">
                <i class="step-dot" />
                <span class="event-name">{{ event.event_type }}</span>
                <span class="event-seq">#{{ event.sequence }}</span>
              </div>
            </div>
          </div>

          <div v-if="liveOutput" class="message assistant"><span class="role"><i class="role-dot" />Agent</span><div>{{ liveOutput }}<span class="cursor">▋</span></div></div>
          <div v-if="!selectedSessionId" class="empty-block"><el-empty description="从左侧选择或创建一个会话" /></div>
        </div>
      </div>

      <footer class="composer">
        <div class="composer-inner">
          <el-input v-model="input" type="textarea" :rows="3" resize="none" placeholder="输入任务；测试工具可尝试让 Agent 计算 20 + 22" @keydown.ctrl.enter.prevent="send" />
          <div class="composer-meta"><span>Ctrl + Enter 发送 · 同一会话只运行一个 Run</span><el-button type="primary" :icon="Promotion" :loading="sending" :disabled="!canSend" @click="send">发送</el-button></div>
        </div>
      </footer>
    </main>
  </section>
</template>

<style scoped>
.chat-layout { display: grid; grid-template-columns: 290px minmax(0, 1fr); gap: var(--sp-5); height: calc(100vh - 136px); min-height: 620px; }

/* 会话列表 */
.session-panel { overflow: hidden; display: flex; flex-direction: column; }
.panel-title { padding: 18px; border-bottom: 1px solid var(--border); }
.panel-title div { display: flex; justify-content: space-between; }
.panel-title strong { font-family: var(--font-display); font-size: 15px; }
.panel-title span { color: var(--ink-400); font-size: 12px; }
.new-session { display: grid; gap: 9px; padding: 14px; border-bottom: 1px solid var(--border); }
.session-list { padding: var(--sp-2); overflow: auto; }
.session-list button {
  position: relative;
  width: 100%;
  display: flex;
  gap: 10px;
  align-items: flex-start;
  padding: var(--sp-3);
  border: 0;
  border-radius: var(--radius-sm);
  color: var(--ink-600);
  background: transparent;
  text-align: left;
  cursor: pointer;
  transition: background 0.15s, color 0.15s;
}
.session-list button:hover { background: #f2f6fd; color: var(--brand-600); }
.session-list button.active { background: var(--brand-50); color: var(--brand-600); }
.session-list button.active::before {
  content: "";
  position: absolute;
  left: 0;
  top: 10px;
  bottom: 10px;
  width: 3px;
  border-radius: 0 3px 3px 0;
  background: var(--grad-brand);
}
.session-list span { min-width: 0; display: grid; gap: 4px; }
.session-list strong { overflow: hidden; white-space: nowrap; text-overflow: ellipsis; }
.session-list small { color: var(--ink-400); font-size: 11px; }

/* 对话区 */
.conversation-panel { min-width: 0; display: flex; flex-direction: column; overflow: hidden; }
.conversation-header { min-height: 62px; padding: 0 var(--sp-5); display: flex; align-items: center; justify-content: space-between; border-bottom: 1px solid var(--border); }
.conversation-header > div { display: grid; gap: 3px; }
.conversation-header strong { font-family: var(--font-display); font-size: 15px; }
.conversation-header .run-link { display: flex; align-items: center; gap: var(--sp-2); }
.conversation-panel > .el-alert { border-radius: 0; }
.message-panel { flex: 1; overflow: auto; padding: var(--sp-6) 26px 12px; }
.conversation-column { max-width: 800px; margin: 0 auto; }

/* 欢迎卡 */
.welcome {
  margin: 8vh auto 0;
  max-width: 520px;
  display: grid;
  justify-items: center;
  text-align: center;
  animation: msg-in 0.3s ease both;
}
.welcome-mark {
  width: 52px;
  height: 52px;
  border-radius: 15px;
  display: grid;
  place-items: center;
  color: #fff;
  background: var(--grad-brand);
  box-shadow: 0 8px 24px rgb(63 110 253 / 35%);
}
.welcome-mark svg { width: 26px; height: 26px; }
.welcome h3 { margin: 18px 0 6px; font-family: var(--font-display); font-size: 20px; font-weight: 600; }
.welcome p { margin: 0 0 22px; color: var(--ink-400); font-size: 13px; }
.suggestion-chips { display: flex; flex-wrap: wrap; justify-content: center; gap: 10px; }
.suggestion-chips button {
  padding: 8px 16px;
  border: 1px solid var(--border-strong);
  border-radius: var(--radius-pill);
  background: var(--surface);
  color: var(--ink-600);
  font-size: 13px;
  cursor: pointer;
  transition: border-color 0.15s, color 0.15s, box-shadow 0.15s;
}
.suggestion-chips button:hover {
  border-color: var(--brand-500);
  color: var(--brand-600);
  box-shadow: 0 4px 12px rgb(59 91 253 / 12%);
}

/* 消息 */
.message { max-width: 82%; margin-bottom: var(--sp-5); display: grid; gap: 6px; animation: msg-in 0.25s ease both; }
.message > div { padding: 12px 15px; border-radius: 4px 14px 14px 14px; line-height: 1.7; white-space: pre-wrap; }
.message .role { display: inline-flex; align-items: center; gap: 6px; color: var(--ink-400); font-size: 12px; }
.role-dot { width: 6px; height: 6px; border-radius: 50%; background: var(--st-neutral); }
.message.assistant .role-dot { background: var(--brand-500); }
.message.tool .role-dot { background: var(--teal-500); }
.message.user { margin-left: auto; justify-items: end; }
.message.user > div {
  color: #fff;
  background: var(--grad-brand);
  border-radius: 14px 4px 14px 14px;
  box-shadow: 0 4px 14px rgb(63 110 253 / 25%);
}
.message.assistant > div { background: var(--surface); border: 1px solid var(--border); }
.cursor { color: var(--brand-600); animation: blink 1s steps(1) infinite; }
@keyframes blink { 50% { opacity: 0; } }

/* 运行步骤（内联时间线） */
.run-steps {
  margin: 0 0 var(--sp-5);
  border: 1px solid var(--border);
  border-radius: var(--radius-md);
  background: #fbfcfe;
  overflow: hidden;
  animation: msg-in 0.25s ease both;
}
.run-steps.live { border-color: #c9d4ff; box-shadow: 0 0 0 3px rgb(79 107 255 / 8%); }
.run-steps-head {
  width: 100%;
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 10px 14px;
  border: 0;
  background: transparent;
  color: var(--ink-600);
  font-size: 12px;
  font-weight: 600;
  cursor: pointer;
}
.steps-title { display: inline-flex; align-items: center; gap: 8px; }
.steps-dot { width: 7px; height: 7px; border-radius: 50%; background: var(--st-neutral); }
.run-steps.live .steps-dot { background: var(--brand-500); animation: pulse 1.4s ease-in-out infinite; }
@keyframes pulse { 50% { opacity: 0.35; } }
.steps-arrow { transition: transform 0.15s; }
.steps-arrow.open { transform: rotate(180deg); }
.run-steps-body { padding: 2px 14px 12px; display: grid; gap: 2px; }
.step { display: flex; align-items: center; gap: 10px; padding: 5px 0; font-size: 12px; }
.step-dot { width: 6px; height: 6px; flex: none; border-radius: 50%; background: var(--st-neutral); }
.step[data-tone='model'] .step-dot { background: var(--brand-500); }
.step[data-tone='tool'] .step-dot { background: var(--teal-500); }
.step[data-tone='success'] .step-dot { background: var(--st-success); }
.step[data-tone='danger'] .step-dot { background: var(--st-danger); }
.step .event-name { color: var(--ink-600); font-family: var(--font-mono); }
.step .event-seq { margin-left: auto; }

/* 悬浮输入区 */
.composer { padding: 14px 26px 18px; }
.composer-inner {
  max-width: 800px;
  margin: 0 auto;
  padding: 12px 12px 8px;
  border: 1px solid var(--border-strong);
  border-radius: 14px;
  background: var(--surface);
  box-shadow: var(--shadow-sm);
  transition: border-color 0.15s, box-shadow 0.15s;
}
.composer-inner:focus-within {
  border-color: var(--brand-500);
  box-shadow: 0 0 0 3px rgb(79 107 255 / 12%), var(--shadow-sm);
}
.composer :deep(.el-textarea__inner) {
  padding: 2px 4px;
  border-radius: 0;
  background: transparent;
  box-shadow: none;
}
.composer-meta { display: flex; align-items: center; justify-content: space-between; padding: 6px 4px 0; color: var(--ink-400); font-size: 12px; }
</style>
