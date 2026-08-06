<script setup lang="ts">
import { ArrowLeft, CloseBold, Refresh } from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { api, errorMessage } from '@/api/client'
import { subscribeToRun } from '@/api/events'
import type { Run, RunEvent } from '@/api/types'
import RunStatusTag from '@/components/RunStatusTag.vue'

const route = useRoute()
const router = useRouter()
const runId = computed(() => String(route.params.runId))
const loading = ref(false)
const cancelling = ref(false)
const reconnecting = ref(false)
const run = ref<Run>()
const events = ref<RunEvent[]>([])
let closeStream: (() => void) | undefined

const terminal = computed(() => run.value && ['SUCCEEDED', 'FAILED', 'CANCELLED'].includes(run.value.status))

async function load() {
  loading.value = true
  try {
    run.value = await api.getRun(runId.value)
    startStream()
  } catch (error) {
    ElMessage.error(errorMessage(error))
  } finally {
    loading.value = false
  }
}

function startStream() {
  closeStream?.()
  closeStream = subscribeToRun(
    runId.value,
    (event) => {
      if (!events.value.some((item) => item.sequence === event.sequence)) {
        events.value.push(event)
        events.value.sort((left, right) => left.sequence - right.sequence)
      }
      reconnecting.value = false
      if (['run.completed', 'run.failed', 'run.cancelled'].includes(event.event_type)) void refresh()
    },
    () => {
      reconnecting.value = true
    },
  )
}

async function refresh() {
  try {
    run.value = await api.getRun(runId.value)
  } catch (error) {
    ElMessage.error(errorMessage(error))
  }
}

async function cancel() {
  if (!run.value) return
  try {
    await ElMessageBox.confirm('取消采用协作式语义，运行中的任务会在模型或工具边界停止。', '确认取消', { type: 'warning' })
    cancelling.value = true
    run.value = await api.cancelRun(run.value.id)
    ElMessage.success(run.value.status === 'CANCELLED' ? 'Run 已取消' : '已提交取消请求')
  } catch (error) {
    if (error !== 'cancel') ElMessage.error(errorMessage(error))
  } finally {
    cancelling.value = false
  }
}

function formatPayload(payload: Record<string, unknown>) {
  return JSON.stringify(payload, null, 2)
}

onMounted(load)
onBeforeUnmount(() => closeStream?.())
</script>

<template>
  <section v-loading="loading">
    <div class="page-toolbar">
      <div><el-button text :icon="ArrowLeft" @click="router.back()">返回</el-button><h2>Run 执行详情</h2><p>数据库事件是审计事实；此页面通过 SSE 从序号 0 重放。</p></div>
      <div v-if="run" class="toolbar-actions"><el-button :icon="Refresh" @click="refresh">刷新状态</el-button><el-button type="danger" plain :icon="CloseBold" :loading="cancelling" :disabled="Boolean(terminal)" @click="cancel">取消 Run</el-button></div>
    </div>

    <el-alert v-if="reconnecting" title="SSE 连接中断，浏览器正在自动重连并携带 Last-Event-ID" type="info" :closable="false" show-icon />

    <div v-if="run" class="run-grid">
      <div class="surface summary-card">
        <div class="summary-title"><div><span class="muted">运行状态</span><RunStatusTag :status="run.status" /></div><span class="mono">{{ run.id }}</span></div>
        <el-descriptions :column="2" border>
          <el-descriptions-item label="Session">{{ run.session_id }}</el-descriptions-item>
          <el-descriptions-item label="Agent Version">{{ run.agent_version_id }}</el-descriptions-item>
          <el-descriptions-item label="Trace ID">{{ run.trace_id }}</el-descriptions-item>
          <el-descriptions-item label="尝试次数">{{ run.attempt_count }}</el-descriptions-item>
          <el-descriptions-item label="创建时间">{{ new Date(run.created_at).toLocaleString() }}</el-descriptions-item>
          <el-descriptions-item label="完成时间">{{ run.completed_at ? new Date(run.completed_at).toLocaleString() : '—' }}</el-descriptions-item>
          <el-descriptions-item label="输入 Token">{{ run.input_tokens ?? '—' }}</el-descriptions-item>
          <el-descriptions-item label="输出 Token">{{ run.output_tokens ?? '—' }}</el-descriptions-item>
        </el-descriptions>
        <h3>输入</h3><pre>{{ run.input_text }}</pre>
        <template v-if="run.output_text"><h3>输出</h3><pre>{{ run.output_text }}</pre></template>
        <el-alert v-if="run.error_code" :title="`${run.error_code}: ${run.error_message}`" type="error" :closable="false" show-icon />
      </div>

      <div class="surface timeline-card">
        <h3>事件时间线 <span>{{ events.length }} 条</span></h3>
        <div v-for="event in events" :key="event.sequence" class="event-line">
          <div class="event-header"><strong>#{{ event.sequence }} · {{ event.event_type }}</strong><span class="muted">{{ new Date(event.created_at).toLocaleTimeString() }}</span></div>
          <pre class="event-payload">{{ formatPayload(event.payload) }}</pre>
        </div>
        <el-empty v-if="!events.length" description="等待 Run 事件" />
      </div>
    </div>
  </section>
</template>

<style scoped>
.page-toolbar > div:first-child { display: flex; align-items: center; gap: 12px; }
.page-toolbar h2 { margin: 0; }
.toolbar-actions { display: flex; gap: 8px; }
.run-grid { margin-top: 16px; display: grid; grid-template-columns: minmax(0, 1fr) 430px; gap: 18px; align-items: start; }
.summary-card, .timeline-card { padding: 20px; }
.summary-title { margin-bottom: 18px; display: flex; justify-content: space-between; align-items: center; }
.summary-title > div { display: flex; gap: 10px; align-items: center; }
.summary-title > span { color: #7e899a; font-size: 12px; }
h3 { margin: 22px 0 10px; font-size: 14px; }
h3 span { color: #8995a7; font-size: 12px; font-weight: 400; }
.summary-card > pre { padding: 13px; border-radius: 8px; background: #f6f8fb; white-space: pre-wrap; word-break: break-word; line-height: 1.6; }
.timeline-card > h3 { margin-top: 0; }
</style>
