<script setup lang="ts">
import { CopyDocument } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'

const props = defineProps<{
  value: string | null | undefined
}>()

async function copy() {
  if (!props.value) return
  try {
    await navigator.clipboard.writeText(props.value)
    ElMessage.success('已复制')
  } catch {
    ElMessage.error('复制失败，请手动选择复制')
  }
}
</script>

<template>
  <span v-if="value" class="copyable-id">
    <code>{{ value }}</code>
    <el-button text :icon="CopyDocument" aria-label="复制标识" @click="copy" />
  </span>
  <span v-else class="muted">—</span>
</template>

<style scoped>
.copyable-id {
  display: inline-flex;
  min-width: 0;
  max-width: 100%;
  align-items: center;
  gap: 2px;
  padding: 2px 4px 2px 8px;
  border-radius: 6px;
  background: var(--st-neutral-bg);
}

code {
  min-width: 0;
  overflow: hidden;
  color: var(--ink-600);
  font-family: var(--font-mono);
  font-size: 12px;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.el-button {
  flex: none;
}
</style>
