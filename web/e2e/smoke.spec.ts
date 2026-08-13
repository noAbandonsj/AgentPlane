import { expect, test } from '@playwright/test'

const adminUser = {
  id: '00000000-0000-0000-0000-000000000001',
  login_name: 'admin',
  display_name: '本地管理员',
  role: 'ADMIN',
  status: 'ACTIVE',
  created_at: '2026-08-05T08:00:00Z',
  updated_at: '2026-08-05T08:00:00Z',
}

const agent = {
  id: '10000000-0000-0000-0000-000000000001',
  name: '企业演示助手',
  description: '用于首版浏览器闭环',
  instructions: '准确回答问题',
  model_alias: 'default',
  tool_keys: ['calculator.add'],
  lifecycle: 'ACTIVE',
  latest_published_version_id: '20000000-0000-0000-0000-000000000001',
  created_at: '2026-08-05T08:00:00Z',
  updated_at: '2026-08-05T08:00:00Z',
}

const normalUser = {
  id: '00000000-0000-0000-0000-000000000002',
  login_name: 'normal-user',
  display_name: '普通用户',
  role: 'USER',
  status: 'ACTIVE',
  created_at: '2026-08-05T08:00:00Z',
  updated_at: '2026-08-05T08:00:00Z',
}

const callingApplication = {
  id: '80000000-0000-0000-0000-000000000001',
  code: 'crm-demand-entry',
  name: 'CRM 需求录入',
  description: 'CRM 调用端',
  active: true,
  created_at: '2026-08-13T08:00:00Z',
  updated_at: '2026-08-13T08:00:00Z',
}

const invocation = {
  id: '90000000-0000-0000-0000-000000000001',
  application_id: callingApplication.id,
  credential_id: '81000000-0000-0000-0000-000000000001',
  external_request_id: 'crm-demand-001',
  external_user_id: 'crm-user-001',
  user_id: normalUser.id,
  conversation_key: 'demand-create',
  agent_id: agent.id,
  agent_version_id: agent.latest_published_version_id,
  decision: 'ALLOWED',
  decision_code: 'ALLOWED',
  decision_message: '授权通过',
  effective_tool_keys: [],
  session_id: '91000000-0000-0000-0000-000000000001',
  run_id: '92000000-0000-0000-0000-000000000001',
  status: 'SUCCEEDED',
  output: '{"product":"演示产品"}',
  error_code: null,
  error_message: null,
  created_at: '2026-08-13T08:00:00Z',
}

test('普通用户注册后进入待审核提示', async ({ page }) => {
  await page.route('**/api/v1/auth/me', (route) =>
    route.fulfill({
      status: 401,
      json: {
        error: { code: 'AUTHENTICATION_REQUIRED', message: '请先登录', details: {} },
      },
    }),
  )
  await page.route('**/api/v1/auth/bootstrap-status', (route) =>
    route.fulfill({ json: { required: false } }),
  )
  await page.route('**/api/v1/auth/register', async (route) => {
    expect(route.request().postDataJSON()).toEqual({
      login_name: 'normal-user',
      display_name: '普通用户',
      password: 'secret1',
    })
    await route.fulfill({ status: 201, json: { ...normalUser, status: 'PENDING' } })
  })

  await page.goto('/register')
  await page.getByLabel('登录名').fill('normal-user')
  await page.getByLabel('显示名称').fill('普通用户')
  await page.getByLabel('密码', { exact: true }).fill('secret1')
  await page.getByLabel('确认密码').fill('secret1')
  await page.getByRole('button', { name: '提交注册' }).click()
  await expect(page.getByText('注册成功，请等待管理员审核后登录')).toBeVisible()
})

test('管理员可配置用户的 Agent 与工具权限', async ({ page }) => {
  let savedPermissions: unknown
  await page.route('**/api/v1/auth/me', (route) => route.fulfill({ json: adminUser }))
  await page.route('**/api/v1/admin/users', (route) =>
    route.fulfill({ json: [normalUser] }),
  )
  await page.route('**/api/v1/admin/agents', (route) => route.fulfill({ json: [agent] }))
  await page.route('**/api/v1/tools', (route) =>
    route.fulfill({
      json: [
        {
          key: 'calculator.add',
          name: '加法计算器',
          description: '对两个整数执行加法',
          risk_level: 'LOW',
          requires_approval: false,
        },
      ],
    }),
  )
  await page.route(`**/api/v1/admin/users/${normalUser.id}/permissions`, async (route) => {
    if (route.request().method() === 'PUT') {
      savedPermissions = route.request().postDataJSON()
      await route.fulfill({
        json: {
          user_id: normalUser.id,
          ...(savedPermissions as { agent_ids: string[]; tool_keys: string[] }),
        },
      })
      return
    }
    await route.fulfill({
      json: { user_id: normalUser.id, agent_ids: [], tool_keys: [] },
    })
  })

  await page.goto('/admin/users')
  await expect(page.getByRole('heading', { name: '普通用户' })).toBeVisible()
  await page.locator('.el-checkbox').filter({ hasText: '企业演示助手' }).click()
  await page.locator('.el-checkbox').filter({ hasText: '加法计算器' }).click()
  await page.getByRole('button', { name: '保存权限' }).click()
  await expect.poll(() => savedPermissions).toEqual({
    agent_ids: [agent.id],
    tool_keys: ['calculator.add'],
  })
  await expect(page.getByText('用户权限已保存')).toBeVisible()
})

test('管理页展示已发布 Agent 并可查看版本', async ({ page }) => {
  await page.route('**/api/v1/auth/me', (route) => route.fulfill({ json: adminUser }))
  await page.route('**/api/v1/admin/agents', (route) => route.fulfill({ json: [agent] }))
  await page.route('**/api/v1/tools', (route) =>
    route.fulfill({
      json: [
        {
          key: 'calculator.add',
          name: '加法计算器',
          description: '对两个整数执行加法',
          risk_level: 'LOW',
          requires_approval: false,
        },
      ],
    }),
  )
  await page.route(`**/api/v1/agents/${agent.id}/versions`, (route) =>
    route.fulfill({
      json: [
        {
          id: agent.latest_published_version_id,
          agent_definition_id: agent.id,
          version_number: 1,
          name: agent.name,
          description: agent.description,
          instructions: agent.instructions,
          model_alias: agent.model_alias,
          tool_keys: agent.tool_keys,
          published_at: '2026-08-05T08:00:00Z',
          published_by: '00000000-0000-0000-0000-000000000001',
        },
      ],
    }),
  )
  await page.route(`**/api/v1/agents/${agent.id}/publish`, (route) =>
    route.fulfill({
      status: 201,
      json: {
        id: agent.latest_published_version_id,
        agent_definition_id: agent.id,
        version_number: 1,
        name: agent.name,
        description: agent.description,
        instructions: agent.instructions,
        model_alias: agent.model_alias,
        tool_keys: agent.tool_keys,
        published_at: '2026-08-05T08:00:00Z',
        published_by: '00000000-0000-0000-0000-000000000001',
      },
    }),
  )

  await page.goto('/agents')
  await expect(page.getByText('企业演示助手')).toBeVisible()
  await expect(page.getByText('已有发布版本')).toBeVisible()
  await page.getByRole('button', { name: '发布', exact: true }).click()
  await page.locator('.el-message-box').getByRole('button', { name: '发布' }).click()
  await expect(page.getByText('已发布 v1')).toBeVisible()
  await page.getByRole('button', { name: '版本' }).click()
  await page.locator('.el-collapse-item__header').filter({ hasText: 'v1' }).click()
  await expect(page.getByText('准确回答问题')).toBeVisible()
})

test('管理员可在调用应用页创建应用并取得一次性凭证', async ({ page }) => {
  let applicationCreated = false
  await page.route('**/api/v1/auth/me', (route) => route.fulfill({ json: adminUser }))
  await page.route('**/api/v1/admin/applications', async (route) => {
    if (route.request().method() === 'POST') {
      applicationCreated = true
      expect(route.request().postDataJSON()).toMatchObject({
        code: 'crm-demand-entry',
        name: 'CRM 需求录入',
      })
      await route.fulfill({
        status: 201,
        json: {
          application: callingApplication,
          credential: {
            id: '81000000-0000-0000-0000-000000000001',
            token: 'ap_once_only_secret',
            token_prefix: 'ap_once_only',
            expires_at: null,
            created_at: '2026-08-13T08:00:00Z',
          },
        },
      })
      return
    }
    await route.fulfill({ json: applicationCreated ? [callingApplication] : [] })
  })
  await page.route('**/api/v1/admin/users', (route) => route.fulfill({ json: [normalUser] }))
  await page.route('**/api/v1/admin/agents', (route) => route.fulfill({ json: [agent] }))
  await page.route('**/api/v1/tools', (route) => route.fulfill({ json: [] }))

  await page.goto('/admin/applications')
  await page.getByRole('button', { name: '新建调用应用' }).click()
  await page.getByLabel('应用编码').fill('crm-demand-entry')
  await page.getByLabel('应用名称').fill('CRM 需求录入')
  await page.getByRole('button', { name: '创建并签发凭证' }).click()
  await expect(page.getByText('这是完整 Token 唯一一次展示')).toBeVisible()
  await expect(page.locator('.token-input input')).toHaveValue('ap_once_only_secret')
})

test('管理员可配置外部用户映射、应用权限并查看凭证状态', async ({ page }) => {
  let mappingPayload: unknown
  let permissionPayload: unknown
  const tool = {
    key: 'calculator.add',
    name: '加法计算器',
    description: '对两个整数执行加法',
    risk_level: 'LOW',
    requires_approval: false,
  }
  await page.route('**/api/v1/auth/me', (route) => route.fulfill({ json: adminUser }))
  await page.route('**/api/v1/admin/applications', (route) =>
    route.fulfill({ json: [callingApplication] }),
  )
  await page.route('**/api/v1/admin/users', (route) => route.fulfill({ json: [normalUser] }))
  await page.route('**/api/v1/admin/agents', (route) => route.fulfill({ json: [agent] }))
  await page.route('**/api/v1/tools', (route) => route.fulfill({ json: [tool] }))
  await page.route(
    `**/api/v1/admin/applications/${callingApplication.id}/credentials`,
    (route) =>
      route.fulfill({
        json: [
          {
            id: invocation.credential_id,
            token_prefix: 'ap_current_token',
            expires_at: null,
            revoked_at: null,
            created_at: '2026-08-13T08:00:00Z',
          },
        ],
      }),
  )
  await page.route(
    `**/api/v1/admin/applications/${callingApplication.id}/user-mappings`,
    async (route) => {
      if (route.request().method() === 'POST') {
        mappingPayload = route.request().postDataJSON()
        await route.fulfill({
          status: 201,
          json: {
            id: '83000000-0000-0000-0000-000000000001',
            application_id: callingApplication.id,
            external_user_id: 'crm-user-001',
            user_id: normalUser.id,
            active: true,
            created_at: '2026-08-13T08:00:00Z',
            updated_at: '2026-08-13T08:00:00Z',
          },
        })
        return
      }
      await route.fulfill({ json: [] })
    },
  )
  await page.route(
    `**/api/v1/admin/applications/${callingApplication.id}/permissions`,
    async (route) => {
      if (route.request().method() === 'PUT') {
        permissionPayload = route.request().postDataJSON()
        await route.fulfill({
          json: { application_id: callingApplication.id, ...permissionPayload as object },
        })
        return
      }
      await route.fulfill({
        json: { application_id: callingApplication.id, agent_ids: [], tool_keys: [] },
      })
    },
  )

  await page.goto('/admin/applications')
  await page.getByRole('button', { name: '配置' }).click()
  await page.getByRole('tab', { name: '外部用户映射' }).click()
  await page.getByPlaceholder('外部用户标识，例如 CRM F_UserId').fill('crm-user-001')
  await page.getByRole('button', { name: '添加映射' }).click()
  await expect.poll(() => mappingPayload).toEqual({
    external_user_id: 'crm-user-001',
    user_id: normalUser.id,
  })

  await page.getByRole('tab', { name: '应用权限' }).click()
  await page.locator('.el-checkbox').filter({ hasText: '企业演示助手' }).click()
  await page.locator('.el-checkbox').filter({ hasText: '加法计算器' }).click()
  await page.getByRole('button', { name: '保存应用权限' }).click()
  await expect.poll(() => permissionPayload).toEqual({
    agent_ids: [agent.id],
    tool_keys: [tool.key],
  })

  await page.getByRole('tab', { name: '凭证' }).click()
  await expect(page.getByText('ap_current_token')).toBeVisible()
  await expect(page.getByText('有效', { exact: true })).toBeVisible()
})

test('管理员可查询调用审计并查看运行结果', async ({ page }) => {
  await page.route('**/api/v1/auth/me', (route) => route.fulfill({ json: adminUser }))
  await page.route('**/api/v1/admin/applications', (route) =>
    route.fulfill({ json: [callingApplication] }),
  )
  await page.route('**/api/v1/admin/agents', (route) => route.fulfill({ json: [agent] }))
  await page.route('**/api/v1/admin/users', (route) => route.fulfill({ json: [normalUser] }))
  await page.route('**/api/v1/admin/invocations**', (route) => {
    const isDetail = route.request().url().includes(invocation.id)
    return route.fulfill({ json: isDetail ? invocation : [invocation] })
  })

  await page.goto('/admin/invocations')
  await expect(page.getByText('crm-demand-001')).toBeVisible()
  await expect(page.getByText('SUCCEEDED')).toBeVisible()
  await page.getByRole('button', { name: '详情' }).click()
  await expect(page.getByText('授权通过')).toBeVisible()
  await expect(page.getByText('{"product":"演示产品"}')).toBeVisible()
  await expect(page.getByRole('button', { name: '打开 Run 详情' })).toBeVisible()
})

test('会话页消费 SSE 增量并展示终态', async ({ page }) => {
  const sessionId = '30000000-0000-0000-0000-000000000001'
  const runId = '40000000-0000-0000-0000-000000000001'
  const traceId = '50000000-0000-0000-0000-000000000001'
  let messageReads = 0
  const session = {
    id: sessionId,
    user_id: '00000000-0000-0000-0000-000000000001',
    agent_definition_id: agent.id,
    agent_version_id: agent.latest_published_version_id,
    title: '计算演示会话',
    status: 'ACTIVE',
    created_at: '2026-08-05T08:00:00Z',
    updated_at: '2026-08-05T08:00:00Z',
  }
  const runningRun = {
    id: runId,
    user_id: session.user_id,
    session_id: sessionId,
    agent_definition_id: agent.id,
    agent_version_id: agent.latest_published_version_id,
    trace_id: traceId,
    status: 'RUNNING',
    input_text: '20 + 22 等于多少？',
    effective_tool_keys: ['calculator.add'],
    output_text: null,
    error_code: null,
    error_message: null,
    attempt_count: 1,
    model_name: 'fake-model',
    input_tokens: null,
    output_tokens: null,
    cancel_requested_at: null,
    created_at: '2026-08-05T08:00:00Z',
    started_at: '2026-08-05T08:00:01Z',
    completed_at: null,
    updated_at: '2026-08-05T08:00:01Z',
  }

  await page.route('**/api/v1/auth/me', (route) => route.fulfill({ json: adminUser }))
  await page.route('**/api/v1/agents', (route) => route.fulfill({ json: [agent] }))
  await page.route('**/api/v1/sessions', (route) => route.fulfill({ json: [session] }))
  await page.route('**/api/v1/capabilities', (route) =>
    route.fulfill({
      json: {
        runtime: 'langgraph',
        model_configured: true,
        model_aliases: ['default'],
        tools: [],
        approval_resume_supported: false,
      },
    }),
  )
  await page.route(`**/api/v1/sessions/${sessionId}/messages`, (route) => {
    messageReads += 1
    const messages =
      messageReads === 1
        ? []
        : [
            {
              id: '70000000-0000-0000-0000-000000000001',
              session_id: sessionId,
              run_id: runId,
              sequence: 1,
              role: 'USER',
              content: runningRun.input_text,
              message_metadata: {},
              created_at: '2026-08-05T08:00:00Z',
            },
            ...(messageReads >= 3
              ? [
                  {
                    id: '70000000-0000-0000-0000-000000000002',
                    session_id: sessionId,
                    run_id: runId,
                    sequence: 2,
                    role: 'ASSISTANT',
                    content: '计算结果是 42',
                    message_metadata: {},
                    created_at: '2026-08-05T08:00:02Z',
                  },
                ]
              : []),
          ]
    return route.fulfill({ json: messages })
  })
  await page.route(`**/api/v1/sessions/${sessionId}/runs`, (route) =>
    route.fulfill({ status: 202, json: runningRun }),
  )
  await page.route(`**/api/v1/runs/${runId}/events`, (route) =>
    route.fulfill({
      contentType: 'text/event-stream',
      body: [
        `id: 1\nevent: run.started\ndata: ${JSON.stringify({ id: 'event-1', run_id: runId, sequence: 1, event_type: 'run.started', payload: { attempt: 1 }, trace_id: traceId, created_at: '2026-08-05T08:00:01Z' })}\n\n`,
        `id: 2\nevent: model.delta\ndata: ${JSON.stringify({ id: 'event-2', run_id: runId, sequence: 2, event_type: 'model.delta', payload: { delta: '计算结果是 42' }, trace_id: traceId, created_at: '2026-08-05T08:00:02Z' })}\n\n`,
        `id: 3\nevent: run.completed\ndata: ${JSON.stringify({ id: 'event-3', run_id: runId, sequence: 3, event_type: 'run.completed', payload: { output: '计算结果是 42' }, trace_id: traceId, created_at: '2026-08-05T08:00:02Z' })}\n\n`,
      ].join(''),
    }),
  )
  await page.route(`**/api/v1/runs/${runId}`, (route) =>
    route.fulfill({
      json: {
        ...runningRun,
        status: 'SUCCEEDED',
        output_text: '计算结果是 42',
        input_tokens: 5,
        output_tokens: 5,
        completed_at: '2026-08-05T08:00:02Z',
      },
    }),
  )

  await page.goto('/chat')
  await page.getByPlaceholder('输入任务；测试工具可尝试让 Agent 计算 20 + 22').fill(runningRun.input_text)
  await page.getByRole('button', { name: '发送' }).click()
  await expect(page.getByText('计算结果是 42')).toBeVisible()
  await expect(page.getByText('已完成')).toBeVisible()
})
