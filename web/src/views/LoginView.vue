<script setup lang="ts">
import { reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'

import { api, errorMessage } from '@/api/client'
import { useAuthStore } from '@/stores/auth'

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()
const loading = ref(false)
const error = ref('')
const bootstrapRequired = ref(false)
const form = reactive({ login_name: '', password: '' })

api.bootstrapStatus().then((result) => (bootstrapRequired.value = result.required)).catch(() => {})

async function submit() {
  loading.value = true
  error.value = ''
  try {
    const user = await api.login(form)
    auth.setUser(user)
    const redirect = typeof route.query.redirect === 'string' ? route.query.redirect : auth.homePath
    await router.replace(redirect)
  } catch (caught) {
    error.value = errorMessage(caught)
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <div class="auth-page">
    <section class="auth-card surface">
      <div class="auth-brand">AgentPlane</div>
      <h1>登录</h1>
      <p class="muted">登录后进入智能体管理或会话运行控制台。</p>
      <el-alert v-if="route.query.registered" title="注册成功，请等待管理员审核后登录" type="success" :closable="false" show-icon />
      <el-alert v-if="error" :title="error" type="error" :closable="false" show-icon />
      <el-form label-position="top" @submit.prevent="submit">
        <el-form-item label="登录名"><el-input v-model="form.login_name" autocomplete="username" /></el-form-item>
        <el-form-item label="密码"><el-input v-model="form.password" type="password" autocomplete="current-password" show-password /></el-form-item>
        <el-button type="primary" native-type="submit" :loading="loading" class="auth-submit">登录</el-button>
      </el-form>
      <div class="auth-links">
        <router-link to="/register">注册普通用户</router-link>
        <router-link v-if="bootstrapRequired" to="/bootstrap-admin">初始化管理员</router-link>
      </div>
    </section>
  </div>
</template>
