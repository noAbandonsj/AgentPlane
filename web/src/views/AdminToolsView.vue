<script setup lang="ts">
import { onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { api, errorMessage } from '@/api/client'
import type { CallingApplication, ToolCall, ToolMetadata, User } from '@/api/types'
import CopyableId from '@/components/CopyableId.vue'

const tools = ref<ToolMetadata[]>([])
const calls = ref<ToolCall[]>([])
const users = ref<User[]>([])
const applications = ref<CallingApplication[]>([])
const loading = ref(false)
const callsLoading = ref(false)
const saving = ref('')
const tab = ref('tools')
const versions = ref<ToolMetadata[]>([])
const contractVisible = ref(false)
const contractLoading = ref(false)
const detailVisible = ref(false)
const selectedCall = ref<ToolCall | null>(null)
const page = ref(1)
const limit = 50
const filters = reactive({ toolKey: '', runId: '', userId: '', applicationId: '', status: '' })
const statusLabels: Record<string, string> = {
  STARTED: '执行中', SUCCEEDED: '成功', DENIED: '已拒绝', FAILED: '失败',
  TIMED_OUT: '超时', CANCELLED: '已取消', INTERRUPTED: '执行中断',
}

async function loadTools() {
  loading.value = true
  try {
    ;[tools.value, users.value, applications.value] = await Promise.all([
      api.listAdminTools(), api.listUsers(), api.listApplications(),
    ])
  } catch (error) {
    ElMessage.error(errorMessage(error))
  } finally {
    loading.value = false
  }
}

async function toggle(tool: ToolMetadata) {
  saving.value = tool.key
  try {
    const updated = await api.setToolEnabled(tool.key, !tool.enabled)
    tools.value = tools.value.map((item) => item.key === tool.key ? updated : item)
    ElMessage.success(updated.enabled ? '工具已启用' : '工具已停用，后续调用将被拒绝')
  } catch (error) {
    ElMessage.error(errorMessage(error))
  } finally {
    saving.value = ''
  }
}

async function openContract(tool: ToolMetadata) {
  contractVisible.value = true
  contractLoading.value = true
  versions.value = []
  try {
    versions.value = await api.listToolVersions(tool.key)
  } catch (error) {
    ElMessage.error(errorMessage(error))
  } finally {
    contractLoading.value = false
  }
}

async function search(targetPage = 1) {
  callsLoading.value = true
  try {
    const result = await api.listToolCalls({
      ...filters, runId: filters.runId.trim(), limit, offset: (targetPage - 1) * limit,
    })
    calls.value = result
    page.value = targetPage
  } catch (error) {
    ElMessage.error(errorMessage(error))
  } finally {
    callsLoading.value = false
  }
}

function inspect(call: ToolCall) {
  selectedCall.value = call
  detailVisible.value = true
}

function userName(id: string) {
  return users.value.find((user) => user.id === id)?.display_name ?? id
}

function applicationName(id: string | null) {
  return id ? applications.value.find((app) => app.id === id)?.name ?? id : 'Playground'
}

onMounted(async () => {
  await loadTools()
  await search()
})
</script>

<template>
  <section>
    <div class="page-toolbar">
      <div>
        <span class="eyebrow">管理 / 工具</span>
        <h2>工具管理</h2>
        <p>管理当前租户的工具可用性，查看参数契约、版本与调用记录。</p>
      </div>
      <el-button :disabled="loading || callsLoading || Boolean(saving)" @click="tab === 'tools' ? loadTools() : search(page)">刷新</el-button>
    </div>
    <el-tabs v-model="tab">
      <el-tab-pane label="工具目录" name="tools">
        <div class="surface panel">
          <p class="muted">停用会拒绝尚未开始的调用，包括已排队任务中的调用。已经获准执行的工具可以完成。启用后仍需在用户与调用应用中授予权限。</p>
          <el-table v-loading="loading" :data="tools" empty-text="尚无可用工具">
            <el-table-column label="工具" min-width="220">
              <template #default="{ row }"><strong>{{ row.name }}</strong><div class="muted">{{ row.key }}</div></template>
            </el-table-column>
            <el-table-column prop="version" label="当前版本" width="120" />
            <el-table-column label="类型" width="100"><template #default="{ row }">{{ row.read_only ? '只读' : '写操作' }}</template></el-table-column>
            <el-table-column label="超时" width="100"><template #default="{ row }">{{ row.timeout_seconds }} 秒</template></el-table-column>
            <el-table-column label="状态" width="110"><template #default="{ row }"><el-tag :type="row.enabled ? 'success' : 'info'">{{ row.enabled ? '已启用' : '已停用' }}</el-tag></template></el-table-column>
            <el-table-column label="操作" width="230">
              <template #default="{ row }">
                <el-button text type="primary" @click="openContract(row)">契约与版本</el-button>
                <el-button text :type="row.enabled ? 'danger' : 'primary'" :loading="saving === row.key" :disabled="Boolean(saving)" @click="toggle(row)">{{ row.enabled ? '停用' : '启用' }}</el-button>
              </template>
            </el-table-column>
          </el-table>
          <div class="links"><router-link to="/admin/users">配置用户授权</router-link><router-link to="/admin/applications">配置应用授权</router-link></div>
        </div>
      </el-tab-pane>
      <el-tab-pane label="调用审计" name="calls">
        <div class="surface panel">
          <el-form :inline="true" :disabled="callsLoading">
            <el-form-item label="工具"><el-select v-model="filters.toolKey" clearable placeholder="全部工具" style="width: 190px"><el-option v-for="tool in tools" :key="tool.key" :label="tool.name" :value="tool.key" /></el-select></el-form-item>
            <el-form-item label="用户"><el-select v-model="filters.userId" clearable filterable placeholder="全部用户" style="width: 180px"><el-option v-for="user in users" :key="user.id" :label="`${user.display_name}（${user.login_name}）`" :value="user.id" /></el-select></el-form-item>
            <el-form-item label="应用"><el-select v-model="filters.applicationId" clearable placeholder="全部应用" style="width: 180px"><el-option v-for="app in applications" :key="app.id" :label="app.name" :value="app.id" /></el-select></el-form-item>
            <el-form-item label="状态"><el-select v-model="filters.status" clearable placeholder="全部状态" style="width: 130px"><el-option v-for="(label, status) in statusLabels" :key="status" :label="label" :value="status" /></el-select></el-form-item>
            <el-form-item label="Run ID"><el-input v-model="filters.runId" clearable placeholder="完整 Run ID" /></el-form-item>
            <el-form-item><el-button type="primary" @click="search()">查询</el-button></el-form-item>
          </el-form>
          <el-table v-loading="callsLoading" :data="calls" empty-text="没有匹配的工具调用">
            <el-table-column label="工具 / 版本" min-width="200"><template #default="{ row }">{{ row.tool_key }}<div class="muted">{{ row.tool_version }}</div></template></el-table-column>
            <el-table-column label="用户" min-width="140"><template #default="{ row }">{{ userName(row.user_id) }}</template></el-table-column>
            <el-table-column label="来源" min-width="160"><template #default="{ row }">{{ applicationName(row.application_id) }}</template></el-table-column>
            <el-table-column label="状态" width="110"><template #default="{ row }">{{ statusLabels[row.status] }}</template></el-table-column>
            <el-table-column label="耗时" width="100"><template #default="{ row }">{{ row.duration_ms === null ? '—' : `${row.duration_ms} ms` }}</template></el-table-column>
            <el-table-column label="时间" min-width="175"><template #default="{ row }">{{ new Date(row.created_at).toLocaleString('zh-CN', { hour12: false }) }}</template></el-table-column>
            <el-table-column label="操作" width="90"><template #default="{ row }"><el-button text type="primary" @click="inspect(row)">详情</el-button></template></el-table-column>
          </el-table>
          <div class="pager"><span class="muted">第 {{ page }} 页，每页最多 {{ limit }} 条</span><div><el-button :disabled="callsLoading || page === 1" @click="search(page - 1)">上一页</el-button><el-button :disabled="callsLoading || calls.length < limit" @click="search(page + 1)">下一页</el-button></div></div>
        </div>
      </el-tab-pane>
    </el-tabs>
    <el-drawer v-model="contractVisible" title="工具契约与版本" size="55%">
      <div v-loading="contractLoading">
        <el-collapse accordion>
          <el-collapse-item v-for="version in versions" :key="version.version" :title="`${version.name} · ${version.version}`" :name="version.version">
            <p>{{ version.description }}</p>
            <p>风险等级：{{ version.risk_level }} · {{ version.requires_approval ? '需要审批' : '无需审批' }}</p>
            <h3>输入参数</h3><pre>{{ JSON.stringify(version.input_schema, null, 2) }}</pre>
            <h3>返回结果</h3><pre>{{ JSON.stringify(version.output_schema, null, 2) }}</pre>
          </el-collapse-item>
        </el-collapse>
      </div>
    </el-drawer>
    <el-drawer v-model="detailVisible" title="工具调用详情" size="55%">
      <el-descriptions v-if="selectedCall" :column="1" border>
        <el-descriptions-item label="调用 ID"><CopyableId :value="selectedCall.id" /></el-descriptions-item>
        <el-descriptions-item label="Run ID"><CopyableId :value="selectedCall.run_id" /></el-descriptions-item>
        <el-descriptions-item label="Trace ID"><CopyableId :value="selectedCall.trace_id" /></el-descriptions-item>
        <el-descriptions-item label="用户">{{ userName(selectedCall.user_id) }}<CopyableId :value="selectedCall.user_id" /></el-descriptions-item>
        <el-descriptions-item label="应用">{{ applicationName(selectedCall.application_id) }}<CopyableId v-if="selectedCall.application_id" :value="selectedCall.application_id" /></el-descriptions-item>
        <el-descriptions-item label="工具版本">{{ selectedCall.tool_key }} · {{ selectedCall.tool_version }}</el-descriptions-item>
        <el-descriptions-item label="状态">{{ statusLabels[selectedCall.status] }}</el-descriptions-item>
        <el-descriptions-item label="错误码">{{ selectedCall.error_code ?? '无' }}</el-descriptions-item>
        <el-descriptions-item label="输入摘要"><pre>{{ JSON.stringify(selectedCall.input_summary, null, 2) }}</pre></el-descriptions-item>
        <el-descriptions-item label="输出摘要"><pre>{{ JSON.stringify(selectedCall.output_summary, null, 2) }}</pre></el-descriptions-item>
      </el-descriptions>
      <p class="muted">调用审计隐藏参数和结果原文。执行中断表示未取得可确认的工具结果。</p>
    </el-drawer>
  </section>
</template>

<style scoped>
.panel { padding: 20px; }
.muted { color: var(--ink-400); font-size: 12px; margin: 6px 0 16px; }
.links, .pager { display: flex; gap: 20px; margin-top: 20px; }
.links a { color: var(--brand-600); font-size: 13px; text-decoration: none; }
.pager { justify-content: space-between; align-items: center; }
pre { white-space: pre-wrap; overflow-wrap: anywhere; font-size: 12px; }
</style>
