<script setup lang="ts">
import { Key, Plus, Setting } from '@element-plus/icons-vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { computed, onMounted, reactive, ref } from 'vue'

import { api, errorMessage } from '@/api/client'
import type {
  Agent,
  ApplicationCredential,
  ApplicationCredentialIssued,
  CallingApplication,
  ExternalUserMapping,
  ToolMetadata,
  User,
} from '@/api/types'
import CopyableId from '@/components/CopyableId.vue'

const loading = ref(false)
const detailLoading = ref(false)
const saving = ref(false)
const applications = ref<CallingApplication[]>([])
const users = ref<User[]>([])
const agents = ref<Agent[]>([])
const tools = ref<ToolMetadata[]>([])
const selectedApplication = ref<CallingApplication | null>(null)
const credentials = ref<ApplicationCredential[]>([])
const mappings = ref<ExternalUserMapping[]>([])
const agentIds = ref<string[]>([])
const toolKeys = ref<string[]>([])
const drawerVisible = ref(false)
const createVisible = ref(false)
const tokenVisible = ref(false)
const issuedCredential = ref<ApplicationCredentialIssued | null>(null)
const issuedTitle = ref('')
const rotateExpiresAt = ref('')
const mappingUserId = ref('')
const mappingExternalUserId = ref('')

const createForm = reactive({
  code: '',
  name: '',
  description: '',
  expiresAt: '',
})
const applicationForm = reactive({
  name: '',
  description: '',
  active: true,
})

const activeUsers = computed(() => users.value.filter((user) => user.status === 'ACTIVE'))

function toOptionalIso(value: string): string | null {
  return value ? new Date(value).toISOString() : null
}

function formatTime(value: string | null | undefined): string {
  if (!value) return '永不过期'
  const date = new Date(value)
  const pad = (n: number) => String(n).padStart(2, '0')
  return `${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`
}

function userLabel(userId: string): string {
  const user = users.value.find((item) => item.id === userId)
  return user ? `${user.display_name}（${user.login_name}）` : userId
}

function credentialStatus(credential: ApplicationCredential): '有效' | '已过期' | '已撤销' {
  if (credential.revoked_at) return '已撤销'
  if (credential.expires_at && new Date(credential.expires_at).getTime() <= Date.now()) {
    return '已过期'
  }
  return '有效'
}

function credentialTagType(credential: ApplicationCredential): 'success' | 'warning' | 'info' {
  const status = credentialStatus(credential)
  if (status === '有效') return 'success'
  return status === '已过期' ? 'warning' : 'info'
}

async function load() {
  loading.value = true
  try {
    ;[applications.value, users.value, agents.value, tools.value] = await Promise.all([
      api.listApplications(),
      api.listUsers(),
      api.listAdminAgents(),
      api.listTools(),
    ])
  } catch (error) {
    ElMessage.error(errorMessage(error))
  } finally {
    loading.value = false
  }
}

function openCreate() {
  Object.assign(createForm, { code: '', name: '', description: '', expiresAt: '' })
  createVisible.value = true
}

function showIssued(title: string, credential: ApplicationCredentialIssued) {
  issuedTitle.value = title
  issuedCredential.value = credential
  tokenVisible.value = true
}

async function createApplication() {
  if (!createForm.code.trim() || !createForm.name.trim()) {
    ElMessage.warning('请填写应用编码和名称')
    return
  }
  saving.value = true
  try {
    const created = await api.createApplication({
      code: createForm.code,
      name: createForm.name,
      description: createForm.description,
      credential_expires_at: toOptionalIso(createForm.expiresAt),
    })
    createVisible.value = false
    showIssued(`${created.application.name} · 初始凭证`, created.credential)
    await load()
  } catch (error) {
    ElMessage.error(errorMessage(error))
  } finally {
    saving.value = false
  }
}

async function loadApplicationDetails(applicationId: string) {
  detailLoading.value = true
  try {
    const [credentialRows, mappingRows, permissions] = await Promise.all([
      api.listApplicationCredentials(applicationId),
      api.listExternalUserMappings(applicationId),
      api.getApplicationPermissions(applicationId),
    ])
    credentials.value = credentialRows
    mappings.value = mappingRows
    agentIds.value = [...permissions.agent_ids]
    toolKeys.value = [...permissions.tool_keys]
  } catch (error) {
    ElMessage.error(errorMessage(error))
  } finally {
    detailLoading.value = false
  }
}

async function openManage(application: CallingApplication) {
  selectedApplication.value = application
  Object.assign(applicationForm, {
    name: application.name,
    description: application.description,
    active: application.active,
  })
  rotateExpiresAt.value = ''
  mappingUserId.value = activeUsers.value[0]?.id ?? ''
  mappingExternalUserId.value = ''
  drawerVisible.value = true
  await loadApplicationDetails(application.id)
}

async function saveApplication() {
  const application = selectedApplication.value
  if (!application || !applicationForm.name.trim()) return
  saving.value = true
  try {
    const updated = await api.patchApplication(application.id, {
      name: applicationForm.name,
      description: applicationForm.description,
      active: applicationForm.active,
    })
    selectedApplication.value = updated
    applications.value = applications.value.map((item) => (item.id === updated.id ? updated : item))
    ElMessage.success('应用信息已保存')
  } catch (error) {
    ElMessage.error(errorMessage(error))
  } finally {
    saving.value = false
  }
}

async function rotateCredential() {
  const application = selectedApplication.value
  if (!application) return
  try {
    await ElMessageBox.confirm(
      '轮换后，该应用现有的所有有效 Token 都会立即失效。请确认调用方可以同步更新。',
      '确认轮换凭证',
      { confirmButtonText: '确认轮换', cancelButtonText: '取消', type: 'warning' },
    )
    const credential = await api.rotateApplicationCredential(application.id, {
      expires_at: toOptionalIso(rotateExpiresAt.value),
    })
    showIssued(`${application.name} · 新凭证`, credential)
    await loadApplicationDetails(application.id)
  } catch (error) {
    if (error !== 'cancel') ElMessage.error(errorMessage(error))
  }
}

async function addMapping() {
  const application = selectedApplication.value
  if (!application || !mappingExternalUserId.value.trim() || !mappingUserId.value) {
    ElMessage.warning('请填写外部用户标识并选择平台用户')
    return
  }
  saving.value = true
  try {
    await api.createExternalUserMapping(application.id, {
      external_user_id: mappingExternalUserId.value,
      user_id: mappingUserId.value,
    })
    mappingExternalUserId.value = ''
    mappings.value = await api.listExternalUserMappings(application.id)
    ElMessage.success('用户映射已添加')
  } catch (error) {
    ElMessage.error(errorMessage(error))
  } finally {
    saving.value = false
  }
}

async function toggleMapping(mapping: ExternalUserMapping, active: boolean) {
  const application = selectedApplication.value
  if (!application) return
  try {
    const updated = await api.patchExternalUserMapping(application.id, mapping.id, { active })
    mappings.value = mappings.value.map((item) => (item.id === updated.id ? updated : item))
    ElMessage.success(active ? '映射已启用' : '映射已停用')
  } catch (error) {
    ElMessage.error(errorMessage(error))
  }
}

async function savePermissions() {
  const application = selectedApplication.value
  if (!application) return
  saving.value = true
  try {
    const permissions = await api.replaceApplicationPermissions(application.id, {
      agent_ids: agentIds.value,
      tool_keys: toolKeys.value,
    })
    agentIds.value = [...permissions.agent_ids]
    toolKeys.value = [...permissions.tool_keys]
    ElMessage.success('应用权限已保存')
  } catch (error) {
    ElMessage.error(errorMessage(error))
  } finally {
    saving.value = false
  }
}

async function copyToken() {
  if (!issuedCredential.value) return
  try {
    await navigator.clipboard.writeText(issuedCredential.value.token)
    ElMessage.success('Token 已复制')
  } catch {
    ElMessage.error('复制失败，请手动选择 Token')
  }
}

onMounted(load)
</script>

<template>
  <section>
    <div class="page-toolbar">
      <div>
        <span class="eyebrow">管理 / 调用应用</span>
        <h2>调用应用</h2>
        <p>管理 CRM、ERP 等调用方身份，以及外部用户映射和能力授权。</p>
      </div>
      <el-button type="primary" :icon="Plus" @click="openCreate">新建调用应用</el-button>
    </div>

    <div class="surface">
      <el-table v-loading="loading" :data="applications" empty-text="还没有调用应用，先创建一个接入方吧">
        <el-table-column label="应用" min-width="230">
          <template #default="{ row }">
            <span class="cell-main">{{ row.name }}</span>
            <span class="cell-sub"><span class="mono">{{ row.code }}</span> · {{ row.description || '暂无说明' }}</span>
          </template>
        </el-table-column>
        <el-table-column label="Application ID" min-width="260">
          <template #default="{ row }"><CopyableId :value="row.id" /></template>
        </el-table-column>
        <el-table-column label="状态" width="110">
          <template #default="{ row }">
            <span class="status-tag" :data-kind="row.active ? 'ok' : 'off'">{{ row.active ? '已启用' : '已停用' }}</span>
          </template>
        </el-table-column>
        <el-table-column label="更新时间" width="140">
          <template #default="{ row }"><span class="mono muted">{{ formatTime(row.updated_at) }}</span></template>
        </el-table-column>
        <el-table-column label="操作" width="120" fixed="right">
          <template #default="{ row }">
            <el-button text type="primary" :icon="Setting" @click="openManage(row)">配置</el-button>
          </template>
        </el-table-column>
      </el-table>
    </div>

    <el-dialog v-model="createVisible" title="新建调用应用" width="600px">
      <el-form label-position="top">
        <el-form-item label="应用编码" required>
          <el-input v-model="createForm.code" placeholder="例如 crm-demand-entry" maxlength="100" />
          <div class="muted">创建后不可修改，只允许字母、数字、点、下划线和连字符。</div>
        </el-form-item>
        <el-form-item label="应用名称" required>
          <el-input v-model="createForm.name" maxlength="200" />
        </el-form-item>
        <el-form-item label="说明">
          <el-input v-model="createForm.description" type="textarea" :rows="3" maxlength="4000" />
        </el-form-item>
        <el-form-item label="初始凭证过期时间">
          <el-date-picker
            v-model="createForm.expiresAt"
            type="datetime"
            value-format="YYYY-MM-DDTHH:mm:ss"
            placeholder="留空表示永不过期"
            style="width: 100%"
          />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="createVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="createApplication">创建并签发凭证</el-button>
      </template>
    </el-dialog>

    <el-drawer
      v-model="drawerVisible"
      :title="selectedApplication ? `${selectedApplication.name} · 接入配置` : '接入配置'"
      size="68%"
    >
      <div v-loading="detailLoading">
        <el-tabs>
          <el-tab-pane label="基本信息">
            <el-form label-position="top" class="detail-form">
              <el-form-item label="Application ID">
                <CopyableId :value="selectedApplication?.id" />
              </el-form-item>
              <el-form-item label="应用编码"><el-input :model-value="selectedApplication?.code" disabled /></el-form-item>
              <el-form-item label="应用名称" required><el-input v-model="applicationForm.name" /></el-form-item>
              <el-form-item label="说明"><el-input v-model="applicationForm.description" type="textarea" :rows="3" /></el-form-item>
              <el-form-item label="启用状态"><el-switch v-model="applicationForm.active" active-text="启用" inactive-text="停用" /></el-form-item>
              <el-button type="primary" :loading="saving" @click="saveApplication">保存基本信息</el-button>
            </el-form>
          </el-tab-pane>

          <el-tab-pane label="外部用户映射">
            <el-alert title="外部系统只能使用这里登记的外部用户标识代表平台用户。" type="info" :closable="false" show-icon />
            <div class="mapping-editor">
              <el-input v-model="mappingExternalUserId" placeholder="外部用户标识，例如 CRM F_UserId" />
              <el-select v-model="mappingUserId" filterable placeholder="选择平台用户">
                <el-option v-for="user in activeUsers" :key="user.id" :label="userLabel(user.id)" :value="user.id" />
              </el-select>
              <el-button type="primary" :loading="saving" @click="addMapping">添加映射</el-button>
            </div>
            <el-table :data="mappings" empty-text="尚未配置用户映射">
              <el-table-column prop="external_user_id" label="外部用户标识" min-width="180" />
              <el-table-column label="平台用户" min-width="220">
                <template #default="{ row }">{{ userLabel(row.user_id) }}</template>
              </el-table-column>
              <el-table-column label="状态" width="120">
                <template #default="{ row }">
                  <el-switch :model-value="row.active" @change="(value: boolean) => toggleMapping(row, value)" />
                </template>
              </el-table-column>
            </el-table>
          </el-tab-pane>

          <el-tab-pane label="应用权限">
            <el-alert
              title="实际权限取应用授权、用户授权和 Agent 版本声明的交集。"
              type="warning"
              :closable="false"
              show-icon
            />
            <div class="permission-group">
              <h3>可调用 Agent</h3>
              <el-checkbox-group v-model="agentIds" class="permission-options">
                <el-checkbox v-for="agent in agents" :key="agent.id" :value="agent.id">
                  <strong>{{ agent.name }}</strong><span class="muted"> — {{ agent.description || '无说明' }}</span>
                </el-checkbox>
              </el-checkbox-group>
            </div>
            <div class="permission-group">
              <h3>可用 Tool</h3>
              <el-checkbox-group v-model="toolKeys" class="permission-options">
                <el-checkbox v-for="tool in tools" :key="tool.key" :value="tool.key">
                  <strong>{{ tool.name }}</strong><span class="muted"> — {{ tool.description }}</span>
                </el-checkbox>
              </el-checkbox-group>
            </div>
            <el-alert
              title="Skill、知识库和其他 Agent Tool 权限将在后续扩展，本阶段不写入占位授权。"
              type="info"
              :closable="false"
            />
            <el-button class="save-permissions" type="primary" :loading="saving" @click="savePermissions">保存应用权限</el-button>
          </el-tab-pane>

          <el-tab-pane label="凭证">
            <el-alert title="完整 Token 不会再次回显。轮换凭证会撤销该应用当前所有有效 Token。" type="warning" :closable="false" show-icon />
            <div class="credential-toolbar">
              <el-date-picker
                v-model="rotateExpiresAt"
                type="datetime"
                value-format="YYYY-MM-DDTHH:mm:ss"
                placeholder="新凭证过期时间（可留空）"
              />
              <el-button type="danger" plain :icon="Key" @click="rotateCredential">轮换凭证</el-button>
            </div>
            <el-table :data="credentials" empty-text="没有凭证记录">
              <el-table-column label="Token 前缀" min-width="170">
                <template #default="{ row }"><span class="mono">{{ row.token_prefix }}</span></template>
              </el-table-column>
              <el-table-column label="状态" width="100">
                <template #default="{ row }"><el-tag :type="credentialTagType(row)">{{ credentialStatus(row) }}</el-tag></template>
              </el-table-column>
              <el-table-column label="创建时间" width="180"><template #default="{ row }">{{ formatTime(row.created_at) }}</template></el-table-column>
              <el-table-column label="过期时间" width="180"><template #default="{ row }">{{ formatTime(row.expires_at) }}</template></el-table-column>
              <el-table-column label="撤销时间" width="180"><template #default="{ row }">{{ row.revoked_at ? formatTime(row.revoked_at) : '—' }}</template></el-table-column>
            </el-table>
          </el-tab-pane>
        </el-tabs>
      </div>
    </el-drawer>

    <el-dialog v-model="tokenVisible" :title="issuedTitle" width="680px" :close-on-click-modal="false">
      <el-alert title="这是完整 Token 唯一一次展示，请立即复制并保存到调用方安全配置中。" type="warning" :closable="false" show-icon />
      <el-input v-if="issuedCredential" :model-value="issuedCredential.token" readonly class="token-input">
        <template #append><el-button @click="copyToken">复制 Token</el-button></template>
      </el-input>
      <template #footer><el-button type="primary" @click="tokenVisible = false">我已妥善保存</el-button></template>
    </el-dialog>
  </section>
</template>

<style scoped>
.detail-form {
  max-width: 720px;
}

.mapping-editor {
  display: grid;
  grid-template-columns: minmax(220px, 1fr) minmax(240px, 1fr) auto;
  gap: 12px;
  margin: 18px 0;
}

.status-tag {
  display: inline-flex;
  align-items: center;
  height: 24px;
  padding: 0 10px;
  border-radius: var(--radius-pill);
  font-size: 12px;
  font-weight: 500;
}
.status-tag[data-kind='ok'] { color: var(--st-success); background: var(--st-success-bg); }
.status-tag[data-kind='off'] { color: var(--st-neutral); background: var(--st-neutral-bg); }

.mono {
  font-size: 12px;
}

.permission-group {
  margin: 22px 0;
}

.permission-options {
  display: grid;
  gap: 10px;
}

.save-permissions {
  margin-top: 18px;
}

.credential-toolbar {
  display: flex;
  justify-content: flex-end;
  gap: 12px;
  margin: 18px 0;
}

.token-input {
  margin-top: 18px;
}

@media (max-width: 900px) {
  .mapping-editor {
    grid-template-columns: 1fr;
  }
}
</style>
