<script setup lang="ts">
import { reactive, ref } from 'vue'
import { useRouter } from 'vue-router'

import { api, errorMessage } from '@/api/client'
import { useAuthStore } from '@/stores/auth'

const router = useRouter()
const auth = useAuthStore()
const loading = ref(false)
const error = ref('')
const form = reactive({ login_name: '', display_name: '', password: '', confirmPassword: '' })

async function submit() {
  error.value = ''
  if (form.password !== form.confirmPassword) {
    error.value = '两次输入的密码不一致'
    return
  }
  loading.value = true
  try {
    const user = await api.bootstrapAdmin({
      login_name: form.login_name,
      display_name: form.display_name,
      password: form.password,
    })
    auth.setUser(user)
    await router.replace('/agents')
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
      <h1>初始化管理员</h1>
      <p class="muted">该入口只在当前租户尚未创建管理员时有效。</p>
      <el-alert v-if="error" :title="error" type="error" :closable="false" show-icon />
      <el-form label-position="top" @submit.prevent="submit">
        <el-form-item label="管理员登录名"><el-input v-model="form.login_name" autocomplete="username" /></el-form-item>
        <el-form-item label="显示名称"><el-input v-model="form.display_name" /></el-form-item>
        <el-form-item label="密码"><el-input v-model="form.password" type="password" autocomplete="new-password" show-password /></el-form-item>
        <el-form-item label="确认密码"><el-input v-model="form.confirmPassword" type="password" autocomplete="new-password" show-password /></el-form-item>
        <el-button type="primary" native-type="submit" :loading="loading" class="auth-submit">创建管理员</el-button>
      </el-form>
      <div class="auth-links"><router-link to="/login">返回登录</router-link></div>
    </section>
  </div>
</template>
