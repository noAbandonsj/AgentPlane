import { afterEach, describe, expect, it, vi } from 'vitest'

import { api } from './client'

describe('API error handling', () => {
  afterEach(() => vi.unstubAllGlobals())

  it('preserves a stable backend error code', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        new Response(
          JSON.stringify({
            error: {
              code: 'MODEL_NOT_CONFIGURED',
              message: '模型接口尚未配置',
              details: {},
            },
          }),
          { status: 503, headers: { 'Content-Type': 'application/json' } },
        ),
      ),
    )

    await expect(api.createRun('session-1', '你好')).rejects.toMatchObject({
      status: 503,
      code: 'MODEL_NOT_CONFIGURED',
      message: '模型接口尚未配置',
    })
  })
})
