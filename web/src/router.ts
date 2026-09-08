import { createRouter, createWebHistory } from 'vue-router'

import { pinia } from './pinia'
import { useAuthStore } from './stores/auth'
import AdminUsersView from './views/AdminUsersView.vue'
import AdminToolsView from './views/AdminToolsView.vue'
import AdminApplicationsView from './views/AdminApplicationsView.vue'
import AdminInvocationsView from './views/AdminInvocationsView.vue'
import AgentsView from './views/AgentsView.vue'
import BootstrapAdminView from './views/BootstrapAdminView.vue'
import ChatView from './views/ChatView.vue'
import LoginView from './views/LoginView.vue'
import RegisterView from './views/RegisterView.vue'
import RunView from './views/RunView.vue'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', redirect: () => useAuthStore(pinia).homePath },
    { path: '/login', component: LoginView, meta: { title: '登录', public: true } },
    { path: '/register', component: RegisterView, meta: { title: '注册', public: true } },
    {
      path: '/bootstrap-admin',
      component: BootstrapAdminView,
      meta: { title: '初始化管理员', public: true },
    },
    { path: '/agents', component: AgentsView, meta: { title: 'Agent 管理', admin: true } },
    { path: '/admin/tools', component: AdminToolsView, meta: { title: '工具管理', admin: true } },
    {
      path: '/admin/applications',
      component: AdminApplicationsView,
      meta: { title: '调用应用', admin: true },
    },
    {
      path: '/admin/invocations',
      component: AdminInvocationsView,
      meta: { title: '调用记录', admin: true },
    },
    {
      path: '/admin/users',
      component: AdminUsersView,
      meta: { title: '用户与权限', admin: true },
    },
    { path: '/chat', component: ChatView, meta: { title: '会话运行' } },
    { path: '/runs/:runId', component: RunView, meta: { title: 'Run 详情' } },
  ],
})

router.beforeEach(async (to) => {
  const auth = useAuthStore(pinia)
  if (!auth.ready) await auth.loadMe()
  if (to.meta.public) {
    if (auth.user) return auth.homePath
    return true
  }
  if (!auth.user) return { path: '/login', query: { redirect: to.fullPath } }
  if (to.meta.admin && !auth.isAdmin) return '/chat'
  return true
})

export default router
