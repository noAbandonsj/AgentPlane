<script setup lang="ts">
import {
  ArrowDown,
  ChatDotRound,
  Connection,
  Cpu,
  Document,
  Fold,
  Operation,
  SwitchButton,
  UserFilled,
} from '@element-plus/icons-vue'
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { useAppStore } from './stores/app'
import { useAuthStore } from './stores/auth'

const route = useRoute()
const router = useRouter()
const store = useAppStore()
const auth = useAuthStore()
const pageTitle = computed(() => String(route.meta.title ?? 'AgentPlane'))
const isPublicPage = computed(() => Boolean(route.meta.public))
const avatarInitial = computed(() => (auth.user?.display_name ?? '?').slice(0, 1))

async function logout() {
  await auth.logout()
  await router.replace('/login')
}

function onUserCommand(command: string) {
  if (command === 'logout') void logout()
}
</script>

<template>
  <router-view v-if="isPublicPage" />
  <el-container v-else class="console-shell">
    <el-aside :width="store.navigationCollapsed ? '72px' : '232px'" class="console-aside">
      <div class="brand" :class="{ compact: store.navigationCollapsed }">
        <span class="brand-mark"><Cpu /></span>
        <span v-if="!store.navigationCollapsed" class="brand-text">AgentPlane</span>
      </div>
      <el-menu router :default-active="route.path" :collapse="store.navigationCollapsed">
        <template v-if="auth.isAdmin">
          <div class="nav-group" :class="{ compact: store.navigationCollapsed }">
            {{ store.navigationCollapsed ? '—' : '管理' }}
          </div>
          <el-menu-item index="/agents"><el-icon><Operation /></el-icon><span>Agent 管理</span></el-menu-item>
          <el-menu-item index="/admin/applications"><el-icon><Connection /></el-icon><span>调用应用</span></el-menu-item>
          <el-menu-item index="/admin/invocations"><el-icon><Document /></el-icon><span>调用记录</span></el-menu-item>
          <el-menu-item index="/admin/users"><el-icon><UserFilled /></el-icon><span>用户与权限</span></el-menu-item>
        </template>
        <div class="nav-group" :class="{ compact: store.navigationCollapsed }">
          {{ store.navigationCollapsed ? '—' : '运行' }}
        </div>
        <el-menu-item index="/chat"><el-icon><ChatDotRound /></el-icon><span>会话运行</span></el-menu-item>
      </el-menu>
      <button class="collapse-button" type="button" @click="store.toggleNavigation">
        <el-icon><Fold /></el-icon>
        <span v-if="!store.navigationCollapsed">收起导航</span>
      </button>
    </el-aside>
    <el-container>
      <el-header class="console-header">
        <div>
          <div class="eyebrow">企业智能体控制与运行平台</div>
          <h1>{{ pageTitle }}</h1>
        </div>
        <el-dropdown v-if="auth.user" trigger="click" @command="onUserCommand">
          <div class="dev-identity" style="cursor: pointer">
            <span class="avatar">{{ avatarInitial }}</span>
            <div><strong>{{ auth.user.display_name }}</strong><span>{{ auth.user.login_name }} · {{ auth.user.role }}</span></div>
            <el-icon class="muted"><ArrowDown /></el-icon>
          </div>
          <template #dropdown>
            <el-dropdown-menu>
              <el-dropdown-item command="logout" :icon="SwitchButton">退出登录</el-dropdown-item>
            </el-dropdown-menu>
          </template>
        </el-dropdown>
      </el-header>
      <el-main class="console-main">
        <router-view v-slot="{ Component }">
          <transition name="page-fade" mode="out-in">
            <component :is="Component" />
          </transition>
        </router-view>
      </el-main>
    </el-container>
  </el-container>
</template>
