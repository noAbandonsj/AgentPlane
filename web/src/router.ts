import { createRouter, createWebHistory } from 'vue-router'

import AgentsView from './views/AgentsView.vue'
import ChatView from './views/ChatView.vue'
import RunView from './views/RunView.vue'

export default createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', redirect: '/agents' },
    { path: '/agents', component: AgentsView, meta: { title: 'Agent 管理' } },
    { path: '/chat', component: ChatView, meta: { title: '会话运行' } },
    { path: '/runs/:runId', component: RunView, meta: { title: 'Run 详情' } },
  ],
})
