<script setup lang="ts">
import { ChatLineRound, CirclePlus, Promotion, View } from '@element-plus/icons-vue'
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
let closeStream: (() => void) | undefined

const publishedAgents = computed(() => agents.value.filter((agent) => agent.latest_published_version_id))
const selectedSession = computed(() => sessions.value.find((item) => item.id === selectedSessionId.value))
const canSend = computed(() => Boolean(capability.value?.model_configured && selectedSessionId.value && input.value.trim() && !sending.value))

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
          <el-icon><ChatLineRound /></el-icon><span><strong>{{ session.title }}</strong><small>版本 {{ session.agent_version_id.slice(0, 8) }}</small></span>
        </button>
        <el-empty v-if="!sessions.length" :image-size="70" description="还没有会话" />
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
        <div v-for="message in messages" :key="message.id" class="message" :class="message.role.toLowerCase()">
          <span class="role">{{ message.role === 'USER' ? '你' : message.role === 'ASSISTANT' ? 'Agent' : '工具' }}</span>
          <div>{{ message.content }}</div>
        </div>
        <div v-if="liveOutput" class="message assistant"><span class="role">Agent</span><div>{{ liveOutput }}<span class="cursor">▋</span></div></div>
        <div v-if="!messages.length && !liveOutput" class="empty-block"><el-empty description="发送一条消息，观察 Run 和工具事件" /></div>
      </div>

      <div class="event-strip" v-if="events.length">
        <el-tag v-for="event in events.filter((item) => item.event_type !== 'model.delta')" :key="event.sequence" size="small" type="info">#{{ event.sequence }} {{ event.event_type }}</el-tag>
      </div>
      <footer class="composer">
        <el-input v-model="input" type="textarea" :rows="3" resize="none" placeholder="输入任务；测试工具可尝试让 Agent 计算 20 + 22" @keydown.ctrl.enter.prevent="send" />
        <div class="composer-meta"><span>Ctrl + Enter 发送 · 同一会话只运行一个 Run</span><el-button type="primary" :icon="Promotion" :loading="sending" :disabled="!canSend" @click="send">发送</el-button></div>
      </footer>
    </main>
  </section>
</template>

<style scoped>
.chat-layout { display: grid; grid-template-columns: 290px minmax(0, 1fr); gap: 18px; height: calc(100vh - 136px); min-height: 620px; }
.session-panel { overflow: hidden; display: flex; flex-direction: column; }
.panel-title { padding: 18px; border-bottom: 1px solid #edf0f5; }
.panel-title div { display: flex; justify-content: space-between; }
.panel-title span { color: #8b96a8; font-size: 12px; }
.new-session { display: grid; gap: 9px; padding: 14px; border-bottom: 1px solid #edf0f5; }
.session-list { padding: 8px; overflow: auto; }
.session-list button { width: 100%; display: flex; gap: 10px; align-items: flex-start; padding: 12px; border: 0; border-radius: 8px; color: #44516a; background: transparent; text-align: left; cursor: pointer; }
.session-list button:hover, .session-list button.active { background: #edf4ff; color: #2767c8; }
.session-list span { min-width: 0; display: grid; gap: 4px; }
.session-list strong { overflow: hidden; white-space: nowrap; text-overflow: ellipsis; }
.session-list small { color: #939daf; }
.conversation-panel { min-width: 0; display: flex; flex-direction: column; overflow: hidden; }
.conversation-header { min-height: 62px; padding: 0 18px; display: flex; align-items: center; justify-content: space-between; border-bottom: 1px solid #edf0f5; }
.conversation-header > div { display: grid; gap: 3px; }
.conversation-header span { color: #8c96a7; font-size: 11px; }
.conversation-header .run-link { display: flex; align-items: center; }
.conversation-panel > .el-alert { border-radius: 0; }
.message-panel { flex: 1; overflow: auto; padding: 24px 26px; }
.message { max-width: 78%; margin-bottom: 20px; display: grid; gap: 6px; }
.message > div { padding: 12px 15px; border-radius: 4px 14px 14px; line-height: 1.65; white-space: pre-wrap; }
.message .role { color: #7b8798; font-size: 12px; }
.message.user { margin-left: auto; justify-items: end; }
.message.user > div { color: #fff; background: #3478df; border-radius: 14px 4px 14px 14px; }
.message.assistant > div { background: #f0f4fa; }
.cursor { animation: blink 1s steps(1) infinite; }
@keyframes blink { 50% { opacity: 0; } }
.event-strip { padding: 8px 18px; display: flex; gap: 6px; overflow-x: auto; border-top: 1px solid #edf0f5; background: #fafbfd; }
.composer { padding: 14px 18px; border-top: 1px solid #edf0f5; }
.composer-meta { margin-top: 8px; display: flex; align-items: center; justify-content: space-between; color: #8b96a9; font-size: 12px; }
</style>
