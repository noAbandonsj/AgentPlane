import 'element-plus/dist/index.css'
import './styles.css'

import ElementPlus from 'element-plus'
import { createApp } from 'vue'

import App from './App.vue'
import { pinia } from './pinia'
import router from './router'

createApp(App).use(pinia).use(router).use(ElementPlus).mount('#app')
