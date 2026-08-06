<script setup lang="ts">
import { ChatDotRound, Cpu, Fold, Operation, SwitchButton, User, UserFilled } from '@element-plus/icons-vue'
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

async function logout() {
  await auth.logout()
  await router.replace('/login')
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
        <el-menu-item v-if="auth.isAdmin" index="/agents"><el-icon><Operation /></el-icon><span>Agent 管理</span></el-menu-item>
        <el-menu-item v-if="auth.isAdmin" index="/admin/users"><el-icon><UserFilled /></el-icon><span>用户与权限</span></el-menu-item>
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
        <div class="dev-identity" v-if="auth.user">
          <el-icon><User /></el-icon>
          <div><strong>{{ auth.user.display_name }}</strong><span>{{ auth.user.login_name }} · {{ auth.user.role }}</span></div>
          <el-button text :icon="SwitchButton" @click="logout">退出</el-button>
        </div>
      </el-header>
      <el-main class="console-main"><router-view /></el-main>
    </el-container>
  </el-container>
</template>
