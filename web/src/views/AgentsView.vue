<script setup lang="ts">
import { Check, EditPen, Plus, View } from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { onMounted, reactive, ref } from 'vue'

import { api, errorMessage } from '@/api/client'
import type { Agent, AgentVersion, ToolMetadata } from '@/api/types'

const loading = ref(false)
const saving = ref(false)
const agents = ref<Agent[]>([])
const tools = ref<ToolMetadata[]>([])
const editorVisible = ref(false)
const versionsVisible = ref(false)
const versions = ref<AgentVersion[]>([])
const versionAgentName = ref('')
const form = reactive({
  id: '',
  name: '',
  description: '',
  instructions: '',
  modelAlias: 'default',
  toolKeys: [] as string[],
})

async function load() {
  loading.value = true
  try {
    ;[agents.value, tools.value] = await Promise.all([api.listAgents(), api.listTools()])
  } catch (error) {
    ElMessage.error(errorMessage(error))
  } finally {
    loading.value = false
  }
}

function openCreate() {
  Object.assign(form, {
    id: '',
    name: '',
    description: '',
    instructions: '你是企业内部智能助手。请基于可用工具准确、简洁地回答问题。',
    modelAlias: 'default',
    toolKeys: [],
  })
  editorVisible.value = true
}

function openEdit(agent: Agent) {
  Object.assign(form, {
    id: agent.id,
    name: agent.name,
    description: agent.description,
    instructions: agent.instructions,
    modelAlias: agent.model_alias,
    toolKeys: [...agent.tool_keys],
  })
  editorVisible.value = true
}

async function save() {
  if (!form.name.trim() || !form.instructions.trim()) {
    ElMessage.warning('请填写名称和系统指令')
    return
  }
  saving.value = true
  try {
    const payload = {
      name: form.name,
      description: form.description,
      instructions: form.instructions,
      model_alias: form.modelAlias,
      tool_keys: form.toolKeys,
    }
    if (form.id) await api.patchAgent(form.id, payload)
    else await api.createAgent(payload)
    ElMessage.success(form.id ? '草稿已保存' : 'Agent 草稿已创建')
    editorVisible.value = false
    await load()
  } catch (error) {
    ElMessage.error(errorMessage(error))
  } finally {
    saving.value = false
  }
}

async function publish(agent: Agent) {
  try {
    await ElMessageBox.confirm(
      `发布“${agent.name}”当前草稿？发布后将生成不可变版本。`,
      '确认发布',
      { confirmButtonText: '发布', cancelButtonText: '取消', type: 'warning' },
    )
    const version = await api.publishAgent(agent.id)
    ElMessage.success(`已发布 v${version.version_number}`)
    await load()
  } catch (error) {
    if (error !== 'cancel') ElMessage.error(errorMessage(error))
  }
}

async function showVersions(agent: Agent) {
  try {
    versions.value = await api.listVersions(agent.id)
    versionAgentName.value = agent.name
    versionsVisible.value = true
  } catch (error) {
    ElMessage.error(errorMessage(error))
  }
}

onMounted(load)
</script>

<template>
  <section>
    <div class="page-toolbar">
      <div><h2>Agent 定义</h2><p>编辑草稿、选择安全工具并发布不可变版本。</p></div>
      <el-button type="primary" :icon="Plus" @click="openCreate">新建 Agent</el-button>
    </div>

    <div class="surface">
      <el-table v-loading="loading" :data="agents" empty-text="还没有 Agent，请先创建草稿">
        <el-table-column label="名称" min-width="180">
          <template #default="{ row }"><strong>{{ row.name }}</strong><div class="muted">{{ row.description || '暂无说明' }}</div></template>
        </el-table-column>
        <el-table-column prop="model_alias" label="模型别名" width="130" />
        <el-table-column label="内置工具" min-width="180">
          <template #default="{ row }"><el-tag v-for="key in row.tool_keys" :key="key" size="small" type="info">{{ key }}</el-tag><span v-if="!row.tool_keys.length" class="muted">无</span></template>
        </el-table-column>
        <el-table-column label="发布状态" width="150">
          <template #default="{ row }"><el-tag :type="row.latest_published_version_id ? 'success' : 'warning'">{{ row.latest_published_version_id ? '已有发布版本' : '仅草稿' }}</el-tag></template>
        </el-table-column>
        <el-table-column label="操作" width="300" fixed="right">
          <template #default="{ row }">
            <el-button text type="primary" :icon="EditPen" @click="openEdit(row)">编辑</el-button>
            <el-button text type="success" :icon="Check" @click="publish(row)">发布</el-button>
            <el-button text :icon="View" @click="showVersions(row)">版本</el-button>
          </template>
        </el-table-column>
      </el-table>
    </div>

    <el-dialog v-model="editorVisible" :title="form.id ? '编辑 Agent 草稿' : '新建 Agent 草稿'" width="680px">
      <el-form label-position="top">
        <el-form-item label="名称" required><el-input v-model="form.name" maxlength="200" /></el-form-item>
        <el-form-item label="说明"><el-input v-model="form.description" type="textarea" :rows="2" maxlength="4000" show-word-limit /></el-form-item>
        <el-form-item label="系统指令" required><el-input v-model="form.instructions" type="textarea" :rows="8" maxlength="50000" show-word-limit /></el-form-item>
        <el-form-item label="模型别名"><el-input v-model="form.modelAlias" disabled /><div class="muted">首版只开放 default，真实模型从服务端环境变量读取。</div></el-form-item>
        <el-form-item label="启用工具">
          <el-checkbox-group v-model="form.toolKeys">
            <el-checkbox v-for="tool in tools" :key="tool.key" :value="tool.key"><strong>{{ tool.name }}</strong><span class="muted"> — {{ tool.description }}</span></el-checkbox>
          </el-checkbox-group>
        </el-form-item>
      </el-form>
      <template #footer><el-button @click="editorVisible = false">取消</el-button><el-button type="primary" :loading="saving" @click="save">保存草稿</el-button></template>
    </el-dialog>

    <el-drawer v-model="versionsVisible" :title="`${versionAgentName} · 发布版本`" size="48%">
      <el-empty v-if="!versions.length" description="尚未发布版本" />
      <el-collapse v-else accordion>
        <el-collapse-item v-for="version in versions" :key="version.id" :name="version.id">
          <template #title><strong>v{{ version.version_number }}</strong><span class="muted"> · {{ new Date(version.published_at).toLocaleString() }}</span></template>
          <el-descriptions :column="1" border>
            <el-descriptions-item label="名称">{{ version.name }}</el-descriptions-item>
            <el-descriptions-item label="模型">{{ version.model_alias }}</el-descriptions-item>
            <el-descriptions-item label="工具">{{ version.tool_keys.join('、') || '无' }}</el-descriptions-item>
            <el-descriptions-item label="系统指令"><div style="white-space: pre-wrap">{{ version.instructions }}</div></el-descriptions-item>
          </el-descriptions>
        </el-collapse-item>
      </el-collapse>
    </el-drawer>
  </section>
</template>

<style scoped>
.el-tag + .el-tag { margin-left: 6px; }
.el-checkbox-group { display: grid; gap: 10px; }
.muted { margin-top: 4px; font-size: 12px; }
</style>
