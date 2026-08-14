<script setup lang="ts">
import { reactive, ref } from 'vue'
import { useRouter } from 'vue-router'

import { api, errorMessage } from '@/api/client'
import AuthShell from '@/components/AuthShell.vue'

const router = useRouter()
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
    await api.register({
      login_name: form.login_name,
      display_name: form.display_name,
      password: form.password,
    })
    await router.replace({ path: '/login', query: { registered: '1' } })
  } catch (caught) {
    error.value = errorMessage(caught)
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <AuthShell title="注册普通用户" subtitle="注册后需要管理员审核并配置 Agent 与工具权限。">
    <el-alert v-if="error" :title="error" type="error" :closable="false" show-icon />
    <el-form label-position="top" @submit.prevent="submit">
      <el-form-item label="登录名"><el-input v-model="form.login_name" autocomplete="username" /></el-form-item>
      <el-form-item label="显示名称"><el-input v-model="form.display_name" /></el-form-item>
      <el-form-item label="密码"><el-input v-model="form.password" type="password" autocomplete="new-password" show-password /></el-form-item>
      <el-form-item label="确认密码"><el-input v-model="form.confirmPassword" type="password" autocomplete="new-password" show-password /></el-form-item>
      <el-button type="primary" native-type="submit" :loading="loading" class="auth-submit" size="large">提交注册</el-button>
    </el-form>
    <div class="auth-links"><router-link to="/login">返回登录</router-link></div>
  </AuthShell>
</template>
