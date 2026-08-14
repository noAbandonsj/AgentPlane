<script setup lang="ts">
import { Refresh, Search, View } from '@element-plus/icons-vue'
import { ElMessage } from 'element-plus'
import { computed, onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'

import { api, errorMessage } from '@/api/client'
import type {
  Agent,
  CallingApplication,
  Invocation,
  InvocationAdminStatus,
  InvocationDecision,
  User,
} from '@/api/types'
import CopyableId from '@/components/CopyableId.vue'
import RunStatusTag from '@/components/RunStatusTag.vue'

const router = useRouter()
const loading = ref(false)
const detailLoading = ref(false)
const applications = ref<CallingApplication[]>([])
const agents = ref<Agent[]>([])
const users = ref<User[]>([])
const invocations = ref<Invocation[]>([])
const selectedInvocation = ref<Invocation | null>(null)
const detailVisible = ref(false)
const page = ref(1)
const limit = 50
const timeRange = ref<[string, string] | [] | null>([])

const filters = reactive({
  applicationId: '',
  externalRequestId: '',
  externalUserId: '',
  agentId: '',
  decision: '' as '' | InvocationDecision,
  status: '' as '' | InvocationAdminStatus,
})

const hasPrevious = computed(() => page.value > 1)
const hasNext = computed(() => invocations.value.length === limit)

function applicationLabel(applicationId: string): string {
  const application = applications.value.find((item) => item.id === applicationId)
  return application ? `${application.name}（${application.code}）` : applicationId
}

function agentLabel(agentId: string): string {
  return agents.value.find((item) => item.id === agentId)?.name ?? agentId
}

function userLabel(userId: string | null): string {
  if (!userId) return '未映射'
  const user = users.value.find((item) => item.id === userId)
  return user ? `${user.display_name}（${user.login_name}）` : userId
}

function formatTime(value: string): string {
  const date = new Date(value)
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`
}

async function loadMetadata() {
  ;[applications.value, agents.value, users.value] = await Promise.all([
    api.listApplications(),
    api.listAdminAgents(),
    api.listUsers(),
  ])
}

async function search(resetPage = true) {
  if (resetPage) page.value = 1
  loading.value = true
  try {
    invocations.value = await api.listAdminInvocations({
      applicationId: filters.applicationId || undefined,
      externalRequestId: filters.externalRequestId.trim() || undefined,
      externalUserId: filters.externalUserId.trim() || undefined,
      agentId: filters.agentId || undefined,
      decision: filters.decision || undefined,
      status: filters.status || undefined,
      createdFrom: timeRange.value?.[0]
        ? new Date(timeRange.value[0]).toISOString()
        : undefined,
      createdTo: timeRange.value?.[1]
        ? new Date(timeRange.value[1]).toISOString()
        : undefined,
      limit,
      offset: (page.value - 1) * limit,
    })
  } catch (error) {
    ElMessage.error(errorMessage(error))
  } finally {
    loading.value = false
  }
}

function resetFilters() {
  Object.assign(filters, {
    applicationId: '',
    externalRequestId: '',
    externalUserId: '',
    agentId: '',
    decision: '',
    status: '',
  })
  timeRange.value = []
  void search()
}

async function openDetail(invocation: Invocation) {
  detailVisible.value = true
  detailLoading.value = true
  selectedInvocation.value = invocation
  try {
    selectedInvocation.value = await api.getAdminInvocation(invocation.id)
  } catch (error) {
    ElMessage.error(errorMessage(error))
  } finally {
    detailLoading.value = false
  }
}

async function changePage(delta: number) {
  page.value += delta
  await search(false)
}

async function load() {
  loading.value = true
  try {
    await loadMetadata()
    await search()
  } catch (error) {
    ElMessage.error(errorMessage(error))
    loading.value = false
  }
}

onMounted(load)
</script>

<template>
  <section>
    <div class="page-toolbar">
      <div>
        <span class="eyebrow">管理 / 调用记录</span>
        <h2>调用记录</h2>
        <p>查看外部应用调用、授权判定、权限快照和最终运行结果。</p>
      </div>
      <el-button :icon="Refresh" @click="search(false)">刷新</el-button>
    </div>

    <div class="surface filter-panel">
      <el-form :inline="true">
        <el-form-item label="调用应用">
          <el-select v-model="filters.applicationId" clearable filterable placeholder="全部应用" style="width: 220px">
            <el-option v-for="application in applications" :key="application.id" :label="applicationLabel(application.id)" :value="application.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="Agent">
          <el-select v-model="filters.agentId" clearable filterable placeholder="全部 Agent" style="width: 200px">
            <el-option v-for="agent in agents" :key="agent.id" :label="agent.name" :value="agent.id" />
          </el-select>
        </el-form-item>
        <el-form-item label="判定">
          <el-select v-model="filters.decision" clearable placeholder="全部" style="width: 130px">
            <el-option label="允许" value="ALLOWED" />
            <el-option label="拒绝" value="DENIED" />
          </el-select>
        </el-form-item>
        <el-form-item label="状态">
          <el-select v-model="filters.status" clearable placeholder="全部" style="width: 170px">
            <el-option label="排队中" value="QUEUED" />
            <el-option label="运行中" value="RUNNING" />
            <el-option label="待审批" value="WAITING_APPROVAL" />
            <el-option label="成功" value="SUCCEEDED" />
            <el-option label="失败" value="FAILED" />
            <el-option label="已取消" value="CANCELLED" />
            <el-option label="已拒绝" value="REJECTED" />
          </el-select>
        </el-form-item>
        <el-form-item label="外部请求 ID"><el-input v-model="filters.externalRequestId" clearable /></el-form-item>
        <el-form-item label="外部用户 ID"><el-input v-model="filters.externalUserId" clearable /></el-form-item>
        <el-form-item label="调用时间">
          <el-date-picker
            v-model="timeRange"
            type="datetimerange"
            value-format="YYYY-MM-DDTHH:mm:ss"
            range-separator="至"
            start-placeholder="开始时间"
            end-placeholder="结束时间"
          />
        </el-form-item>
        <el-form-item>
          <el-button type="primary" :icon="Search" @click="search()">查询</el-button>
          <el-button @click="resetFilters">重置</el-button>
        </el-form-item>
      </el-form>
    </div>

    <div class="surface invocation-table">
      <el-table v-loading="loading" :data="invocations" empty-text="没有匹配的调用记录">
        <el-table-column label="调用应用" min-width="190">
          <template #default="{ row }"><span class="cell-main">{{ applicationLabel(row.application_id) }}</span></template>
        </el-table-column>
        <el-table-column label="外部请求" min-width="190">
          <template #default="{ row }">
            <span class="cell-main mono">{{ row.external_request_id }}</span>
            <span class="cell-sub mono">{{ row.external_user_id }}</span>
          </template>
        </el-table-column>
        <el-table-column label="Agent" min-width="170">
          <template #default="{ row }">{{ agentLabel(row.agent_id) }}</template>
        </el-table-column>
        <el-table-column label="判定" width="110">
          <template #default="{ row }"><RunStatusTag :status="row.decision" /></template>
        </el-table-column>
        <el-table-column label="状态" width="130">
          <template #default="{ row }"><RunStatusTag :status="row.status" /></template>
        </el-table-column>
        <el-table-column label="调用时间" width="130">
          <template #default="{ row }"><span class="mono muted">{{ formatTime(row.created_at) }}</span></template>
        </el-table-column>
        <el-table-column label="操作" width="100" fixed="right">
          <template #default="{ row }"><el-button text type="primary" :icon="View" @click="openDetail(row)">详情</el-button></template>
        </el-table-column>
      </el-table>
      <div class="pager">
        <span class="muted">第 {{ page }} 页，每页最多 {{ limit }} 条</span>
        <div>
          <el-button :disabled="!hasPrevious" @click="changePage(-1)">上一页</el-button>
          <el-button :disabled="!hasNext" @click="changePage(1)">下一页</el-button>
        </div>
      </div>
    </div>

    <el-drawer v-model="detailVisible" title="Invocation 审计详情" size="62%">
      <div v-loading="detailLoading">
        <el-descriptions v-if="selectedInvocation" :column="1" border>
          <el-descriptions-item label="Invocation ID"><CopyableId :value="selectedInvocation.id" /></el-descriptions-item>
          <el-descriptions-item label="调用应用">{{ applicationLabel(selectedInvocation.application_id) }}</el-descriptions-item>
          <el-descriptions-item label="Credential ID"><CopyableId :value="selectedInvocation.credential_id" /></el-descriptions-item>
          <el-descriptions-item label="外部请求 ID">{{ selectedInvocation.external_request_id }}</el-descriptions-item>
          <el-descriptions-item label="外部用户 ID">{{ selectedInvocation.external_user_id }}</el-descriptions-item>
          <el-descriptions-item label="平台用户">{{ userLabel(selectedInvocation.user_id) }}</el-descriptions-item>
          <el-descriptions-item label="会话业务键">{{ selectedInvocation.conversation_key }}</el-descriptions-item>
          <el-descriptions-item label="Agent">{{ agentLabel(selectedInvocation.agent_id) }}</el-descriptions-item>
          <el-descriptions-item label="Agent ID"><CopyableId :value="selectedInvocation.agent_id" /></el-descriptions-item>
          <el-descriptions-item label="Agent Version ID"><CopyableId :value="selectedInvocation.agent_version_id" /></el-descriptions-item>
          <el-descriptions-item label="授权判定">
            <RunStatusTag :status="selectedInvocation.decision" />
            <span class="decision-message">{{ selectedInvocation.decision_code }} · {{ selectedInvocation.decision_message }}</span>
          </el-descriptions-item>
          <el-descriptions-item label="生效 Tool">{{ selectedInvocation.effective_tool_keys.join('、') || '无' }}</el-descriptions-item>
          <el-descriptions-item label="运行状态"><RunStatusTag :status="selectedInvocation.status" /></el-descriptions-item>
          <el-descriptions-item label="Run ID">
            <CopyableId :value="selectedInvocation.run_id" />
            <el-button v-if="selectedInvocation.run_id" text type="primary" @click="router.push(`/runs/${selectedInvocation.run_id}`)">打开 Run 详情</el-button>
          </el-descriptions-item>
          <el-descriptions-item label="输出"><pre>{{ selectedInvocation.output || '—' }}</pre></el-descriptions-item>
          <el-descriptions-item label="错误">{{ selectedInvocation.error_code ? `${selectedInvocation.error_code} · ${selectedInvocation.error_message || ''}` : '—' }}</el-descriptions-item>
          <el-descriptions-item label="调用时间">{{ formatTime(selectedInvocation.created_at) }}</el-descriptions-item>
        </el-descriptions>
      </div>
    </el-drawer>
  </section>
</template>

<style scoped>
.filter-panel {
  margin-bottom: var(--sp-4);
  padding: 18px 18px 0;
}

.invocation-table {
  overflow: hidden;
}

.mono {
  font-size: 12px;
}

.pager {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 14px 18px;
  border-top: 1px solid var(--border);
}

.decision-message {
  margin-left: 10px;
  color: var(--ink-600);
  font-size: 12px;
}

pre {
  margin: 0;
  font-family: var(--font-mono);
  font-size: 12px;
  white-space: pre-wrap;
  word-break: break-word;
}
</style>
