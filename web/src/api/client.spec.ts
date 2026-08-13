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

  it('serializes invocation audit filters with admin paging', async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify([]), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }),
    )
    vi.stubGlobal('fetch', fetchMock)

    await api.listAdminInvocations({
      applicationId: 'application-1',
      externalUserId: 'crm-user-1',
      decision: 'DENIED',
      status: 'REJECTED',
      limit: 50,
      offset: 100,
    })

    expect(fetchMock).toHaveBeenCalledOnce()
    expect(fetchMock.mock.calls[0]?.[0]).toBe(
      '/api/v1/admin/invocations?application_id=application-1&external_user_id=crm-user-1&decision=DENIED&status=REJECTED&limit=50&offset=100',
    )
  })
})
