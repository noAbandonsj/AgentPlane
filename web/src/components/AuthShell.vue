<script setup lang="ts">
import { Cpu } from '@element-plus/icons-vue'

defineProps<{ title: string; subtitle: string }>()

const highlights = [
  { title: '版本化发布', desc: 'Agent 草稿与发布版本分离，运行可复现' },
  { title: '权限交集', desc: '应用、用户、Agent 三方授权取交集，最小可用' },
  { title: '全链路审计', desc: '每一次调用、每一个 Run 事件都可回放' },
]
</script>

<template>
  <div class="auth-split">
    <aside class="auth-hero">
      <div class="auth-hero-brand"><span class="hero-mark"><Cpu /></span>AgentPlane</div>
      <h2>让企业智能体<br />安全地跑起来</h2>
      <p class="hero-sub">智能体的定义、发布、授权与运行审计，一个控制台全部完成。</p>
      <ul>
        <li v-for="item in highlights" :key="item.title">
          <strong>{{ item.title }}</strong>
          <span>{{ item.desc }}</span>
        </li>
      </ul>
      <footer>企业智能体控制与运行平台</footer>
    </aside>
    <main class="auth-panel">
      <section class="auth-form">
        <h1>{{ title }}</h1>
        <p class="muted">{{ subtitle }}</p>
        <slot />
      </section>
    </main>
  </div>
</template>

<style scoped>
.auth-split {
  min-height: 100vh;
  display: grid;
  grid-template-columns: minmax(440px, 5fr) 7fr;
}

.auth-hero {
  position: relative;
  display: flex;
  flex-direction: column;
  padding: 56px 52px 40px;
  color: #eaf1ff;
  background:
    radial-gradient(600px 420px at 15% 0%, rgb(34 211 238 / 28%), transparent 60%),
    radial-gradient(520px 520px at 90% 100%, rgb(79 107 255 / 35%), transparent 65%),
    #0b1526;
  overflow: hidden;
}
.auth-hero::after {
  content: "";
  position: absolute;
  inset: 0;
  background-image: radial-gradient(rgb(255 255 255 / 7%) 1px, transparent 1px);
  background-size: 26px 26px;
  pointer-events: none;
}

.auth-hero-brand {
  display: flex;
  align-items: center;
  gap: 12px;
  font-family: var(--font-display);
  font-size: 18px;
  font-weight: 700;
  letter-spacing: 0.3px;
}
.hero-mark {
  width: 38px;
  height: 38px;
  border-radius: 11px;
  display: grid;
  place-items: center;
  color: #fff;
  background: var(--grad-brand);
  box-shadow: 0 6px 18px rgb(63 110 253 / 50%);
}
.hero-mark svg { width: 22px; }

.auth-hero h2 {
  margin: auto 0 18px;
  font-family: var(--font-display);
  font-size: 38px;
  font-weight: 700;
  line-height: 1.25;
  letter-spacing: 0.5px;
}
.hero-sub {
  margin: 0 0 34px;
  color: #9fb2d8;
  font-size: 14px;
  line-height: 1.8;
}

.auth-hero ul {
  margin: 0 0 auto;
  padding: 0;
  list-style: none;
  display: grid;
  gap: 18px;
}
.auth-hero li {
  position: relative;
  padding-left: 18px;
  display: grid;
  gap: 4px;
}
.auth-hero li::before {
  content: "";
  position: absolute;
  left: 0;
  top: 7px;
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: var(--grad-brand);
}
.auth-hero li strong { font-size: 14px; }
.auth-hero li span { color: #9fb2d8; font-size: 12px; line-height: 1.7; }

.auth-hero footer {
  margin-top: 48px;
  color: #5f7093;
  font-size: 11px;
  letter-spacing: 1.5px;
}

.auth-panel {
  display: grid;
  place-items: center;
  padding: 40px;
  background: var(--bg);
}

.auth-form { width: 400px; }
.auth-form h1 {
  margin: 0 0 8px;
  font-family: var(--font-display);
  font-size: 26px;
  font-weight: 700;
}
.auth-form > .muted { display: block; margin: 0 0 28px; }
.auth-form :deep(.el-alert) { margin-bottom: 18px; }
</style>
