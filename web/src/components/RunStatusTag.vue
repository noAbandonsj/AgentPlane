<script setup lang="ts">
import type { Run } from '@/api/types'

defineProps<{ status: Run['status'] }>()

const labels: Record<Run['status'], string> = {
  QUEUED: '排队中',
  RUNNING: '运行中',
  WAITING_APPROVAL: '等待审批',
  SUCCEEDED: '已完成',
  FAILED: '失败',
  CANCELLED: '已取消',
}

const tagTypes: Record<Run['status'], 'info' | 'primary' | 'warning' | 'success' | 'danger'> = {
  QUEUED: 'info',
  RUNNING: 'primary',
  WAITING_APPROVAL: 'warning',
  SUCCEEDED: 'success',
  FAILED: 'danger',
  CANCELLED: 'info',
}
</script>

<template>
  <el-tag :type="tagTypes[status]" effect="light">{{ labels[status] }}</el-tag>
</template>
