import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import RunStatusTag from './RunStatusTag.vue'

describe('RunStatusTag', () => {
  it('renders a Chinese terminal state', () => {
    const wrapper = mount(RunStatusTag, {
      props: { status: 'SUCCEEDED' },
      global: { stubs: { 'el-tag': { template: '<span><slot /></span>' } } },
    })

    expect(wrapper.text()).toContain('已完成')
  })
})
