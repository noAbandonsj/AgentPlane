import { defineStore } from 'pinia'

import { api, ApiClientError } from '@/api/client'
import type { User } from '@/api/types'

export const useAuthStore = defineStore('auth', {
  state: () => ({
    user: null as User | null,
    ready: false,
  }),
  getters: {
    isAdmin: (state) => state.user?.role === 'ADMIN',
    homePath: (state) => (state.user?.role === 'ADMIN' ? '/agents' : '/chat'),
  },
  actions: {
    async loadMe() {
      try {
        this.user = await api.me()
      } catch (error) {
        if (!(error instanceof ApiClientError) || error.status !== 401) throw error
        this.user = null
      } finally {
        this.ready = true
      }
    },
    setUser(user: User) {
      this.user = user
      this.ready = true
    },
    async logout() {
      await api.logout()
      this.user = null
      this.ready = true
    },
  },
})
