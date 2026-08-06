import { defineStore } from 'pinia'

export const useAppStore = defineStore('app', {
  state: () => ({
    tenantId: '00000000-0000-0000-0000-000000000001',
    userId: '00000000-0000-0000-0000-000000000001',
    navigationCollapsed: false,
  }),
  actions: {
    toggleNavigation() {
      this.navigationCollapsed = !this.navigationCollapsed
    },
  },
})
