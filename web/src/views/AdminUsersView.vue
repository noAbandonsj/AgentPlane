<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'

import { api, errorMessage } from '@/api/client'
import type { Agent, ToolMetadata, User, UserStatus } from '@/api/types'

const users = ref<User[]>([])
const agents = ref<Agent[]>([])
const tools = ref<ToolMetadata[]>([])
const selectedUserId = ref('')
const agentIds = ref<string[]>([])
const toolKeys = ref<string[]>([])
const loading = ref(false)
const saving = ref(false)
const selectedUser = computed(() => users.value.find((user) => user.id === selectedUserId.value))

async function load() {
  loading.value = true
  try {
    ;[users.value, agents.value, tools.value] = await Promise.all([
      api.listUsers(),
      api.listAdminAgents(),
      api.listTools(),
    ])
    if (!selectedUserId.value && users.value.length) await selectUser(users.value[0]!.id)
  } catch (error) {
    ElMessage.error(errorMessage(error))
  } finally {
    loading.value = false
  }
}

async function selectUser(userId: string) {
  selectedUserId.value = userId
  try {
    const permissions = await api.getUserPermissions(userId)
    agentIds.value = [...permissions.agent_ids]
    toolKeys.value = [...permissions.tool_keys]
  } catch (error) {
    ElMessage.error(errorMessage(error))
  }
}

async function changeStatus(user: User, status: UserStatus) {
  try {
    const updated = await api.updateUserStatus(user.id, status)
    users.value = users.value.map((item) => (item.id === updated.id ? updated : item))
    ElMessage.success('用户状态已更新')
  } catch (error) {
    ElMessage.error(errorMessage(error))
  }
}

function onStatusChange(value: string | number | boolean | undefined) {
  if (selectedUser.value && typeof value === 'string') {
    void changeStatus(selectedUser.value, value as UserStatus)
  }
}

async function savePermissions() {
  if (!selectedUserId.value) return
  saving.value = true
  try {
    const permissions = await api.replaceUserPermissions(
      selectedUserId.value,
      agentIds.value,
      toolKeys.value,
    )
    agentIds.value = [...permissions.agent_ids]
    toolKeys.value = [...permissions.tool_keys]
    ElMessage.success('用户权限已保存')
  } catch (error) {
    ElMessage.error(errorMessage(error))
  } finally {
    saving.value = false
  }
}

onMounted(load)
</script>

<template>
  <div v-loading="loading" class="admin-users-grid">
    <section class="surface admin-user-list">
      <div class="section-heading"><div><span class="eyebrow">成员</span><h2>用户与状态</h2></div></div>
      <button
        v-for="user in users"
        :key="user.id"
        type="button"
        class="admin-user-row"
        :class="{ active: user.id === selectedUserId }"
        @click="selectUser(user.id)"
      >
        <span><strong>{{ user.display_name }}</strong><small>{{ user.login_name }} · {{ user.role }}</small></span>
        <el-tag :type="user.status === 'ACTIVE' ? 'success' : user.status === 'PENDING' ? 'warning' : 'info'">{{ user.status }}</el-tag>
      </button>
      <el-empty v-if="!users.length" description="暂无用户" />
    </section>

    <section class="surface permission-panel">
      <template v-if="selectedUser">
        <div class="section-heading">
          <div><span class="eyebrow">授权</span><h2>{{ selectedUser.display_name }}</h2></div>
          <el-select :model-value="selectedUser.status" style="width: 140px" @change="onStatusChange">
            <el-option label="待审核" value="PENDING" />
            <el-option label="已启用" value="ACTIVE" />
            <el-option label="已停用" value="DISABLED" />
          </el-select>
        </div>
        <el-alert
          v-if="selectedUser.role === 'ADMIN'"
          title="管理员拥有管理能力，但执行 Agent 时仍使用这里配置的 Agent 与工具授权。"
          type="info"
          :closable="false"
          show-icon
        />
        <div class="permission-group">
          <h3>可用 Agent</h3>
          <p class="muted">用户只能创建获授权 Agent 的会话和 Run。</p>
          <el-checkbox-group v-model="agentIds" class="permission-options">
            <el-checkbox v-for="agent in agents" :key="agent.id" :value="agent.id">
              <strong>{{ agent.name }}</strong><span class="muted"> — {{ agent.description || '无说明' }}</span>
            </el-checkbox>
          </el-checkbox-group>
        </div>
        <div class="permission-group">
          <h3>工具授权</h3>
          <p class="muted">实际工具为用户授权与 AgentVersion 声明的交集。</p>
          <el-checkbox-group v-model="toolKeys" class="permission-options">
            <el-checkbox v-for="tool in tools" :key="tool.key" :value="tool.key">
              <strong>{{ tool.name }}</strong><span class="muted"> — {{ tool.description }}</span>
            </el-checkbox>
          </el-checkbox-group>
        </div>
        <el-button type="primary" :loading="saving" @click="savePermissions">保存权限</el-button>
      </template>
      <el-empty v-else description="请选择用户" />
    </section>
  </div>
</template>
