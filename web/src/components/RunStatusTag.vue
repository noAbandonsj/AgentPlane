<script setup lang="ts">
import { computed } from 'vue'

import type { Run } from '@/api/types'

const props = defineProps<{ status: Run['status'] | string }>()

type Tone = 'running' | 'success' | 'danger' | 'warning' | 'neutral'

const labels: Record<string, string> = {
  QUEUED: '排队中',
  RUNNING: '运行中',
  WAITING_APPROVAL: '等待审批',
  SUCCEEDED: '已完成',
  FAILED: '失败',
  CANCELLED: '已取消',
  REJECTED: '已拒绝',
  ALLOWED: '允许',
  DENIED: '拒绝',
}

const tones: Record<string, Tone> = {
  QUEUED: 'warning',
  RUNNING: 'running',
  WAITING_APPROVAL: 'warning',
  SUCCEEDED: 'success',
  FAILED: 'danger',
  CANCELLED: 'neutral',
  REJECTED: 'danger',
  ALLOWED: 'success',
  DENIED: 'danger',
}

const tone = computed<Tone>(() => tones[props.status] ?? 'neutral')
const label = computed(() => labels[props.status] ?? props.status)
</script>

<template>
  <span class="status-pill" :data-tone="tone">
    <span class="dot" :class="{ pulse: tone === 'running' }" />
    {{ label }}
  </span>
</template>

<style scoped>
.status-pill {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  height: 24px;
  padding: 0 10px;
  border-radius: var(--radius-pill);
  font-size: 12px;
  font-weight: 500;
  white-space: nowrap;
}

.dot {
  width: 6px;
  height: 6px;
  flex: none;
  border-radius: 50%;
  background: currentcolor;
}

.status-pill[data-tone='running'] { color: var(--st-running); background: var(--st-running-bg); }
.status-pill[data-tone='success'] { color: var(--st-success); background: var(--st-success-bg); }
.status-pill[data-tone='danger'] { color: var(--st-danger); background: var(--st-danger-bg); }
.status-pill[data-tone='warning'] { color: var(--st-warning); background: var(--st-warning-bg); }
.status-pill[data-tone='neutral'] { color: var(--st-neutral); background: var(--st-neutral-bg); }

.pulse { animation: pulse 1.4s ease-in-out infinite; }

@keyframes pulse {
  50% { opacity: 0.35; }
}

@media (prefers-reduced-motion: reduce) {
  .pulse { animation: none; }
}
</style>
