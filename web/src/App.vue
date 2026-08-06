<script setup lang="ts">
import { ChatDotRound, Cpu, Fold, Operation, User } from '@element-plus/icons-vue'
import { computed } from 'vue'
import { useRoute } from 'vue-router'

import { useAppStore } from './stores/app'

const route = useRoute()
const store = useAppStore()
const pageTitle = computed(() => String(route.meta.title ?? 'AgentPlane'))
</script>

<template>
  <el-container class="console-shell">
    <el-aside :width="store.navigationCollapsed ? '72px' : '232px'" class="console-aside">
      <div class="brand" :class="{ compact: store.navigationCollapsed }">
        <span class="brand-mark"><Cpu /></span>
        <span v-if="!store.navigationCollapsed" class="brand-text">AgentPlane</span>
      </div>
      <el-menu router :default-active="route.path" :collapse="store.navigationCollapsed">
        <el-menu-item index="/agents"><el-icon><Operation /></el-icon><span>Agent 管理</span></el-menu-item>
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
        <div class="dev-identity">
          <el-icon><User /></el-icon>
          <div><strong>本地开发用户</strong><span>开发租户 · AUTH_MODE=dev</span></div>
        </div>
      </el-header>
      <el-main class="console-main"><router-view /></el-main>
    </el-container>
  </el-container>
</template>
