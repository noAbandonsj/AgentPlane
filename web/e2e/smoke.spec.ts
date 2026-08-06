import { expect, test } from '@playwright/test'

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

test('管理页展示已发布 Agent 并可查看版本', async ({ page }) => {
  await page.route('**/api/v1/agents', (route) => route.fulfill({ json: [agent] }))
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
