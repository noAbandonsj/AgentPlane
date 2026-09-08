import { expect, test } from '@playwright/test'

test('管理员查看工具契约、启停并筛选调用审计', async ({ page }, testInfo) => {
  const userId = '00000000-0000-0000-0000-000000000001'
  const runId = '10000000-0000-0000-0000-000000000001'
  const user = { id: userId, display_name: '测试管理员', login_name: 'admin', role: 'ADMIN', status: 'ACTIVE' }
  const tool = {
    key: 'calculator.add', name: '加法计算器', version: '1.0.0', description: '安全十进制加法',
    enabled: true, read_only: true, risk_level: 'LOW', requires_approval: false, timeout_seconds: 10,
    input_schema: { type: 'object', properties: { a: { type: 'number' }, b: { type: 'number' } }, additionalProperties: false },
    output_schema: { type: 'string' },
  }
  await page.route('**/api/v1/auth/me', (route) => route.fulfill({ json: user }))
  await page.route('**/api/v1/admin/users', (route) => route.fulfill({ json: [user] }))
  await page.route('**/api/v1/admin/applications', (route) => route.fulfill({ json: [] }))
  await page.route('**/api/v1/admin/tools', (route) => route.fulfill({ json: [tool] }))
  await page.route('**/api/v1/admin/tools/calculator.add/versions', (route) => route.fulfill({ json: [tool] }))
  await page.route('**/api/v1/admin/tools/calculator.add', async (route) => {
    expect(route.request().method()).toBe('PATCH')
    tool.enabled = route.request().postDataJSON().enabled
    await route.fulfill({ json: tool })
  })
  await page.route('**/api/v1/admin/tool-calls?*', (route) => route.fulfill({ json: [{
    id: '20000000-0000-0000-0000-000000000001', run_id: runId, user_id: userId,
    application_id: null, trace_id: '30000000-0000-0000-0000-000000000001',
    tool_key: tool.key, tool_version: tool.version, status: 'SUCCEEDED', error_code: null,
    input_summary: { field_count: 2, values: 'REDACTED' },
    output_summary: { characters: 2, values: 'REDACTED' }, duration_ms: 12,
    created_at: '2026-09-08T01:00:00Z', completed_at: '2026-09-08T01:00:00Z',
  }] }))
  await page.goto('/admin/tools')
  await expect(page.getByRole('heading', { name: '工具管理', level: 2 })).toBeVisible()
  await page.getByRole('button', { name: '契约与版本' }).click()
  await page.getByRole('button', { name: '加法计算器 · 1.0.0' }).click()
  await expect(page.getByText('additionalProperties', { exact: false })).toBeVisible()
  await page.getByRole('button', { name: 'Close this dialog' }).click()
  await page.getByRole('button', { name: '停用', exact: true }).click()
  await expect(page.getByText('已停用', { exact: true })).toBeVisible()
  await page.getByRole('button', { name: '启用', exact: true }).click()
  await expect(page.getByText('已启用', { exact: true })).toBeVisible()
  await expect(page.locator('.el-message')).toHaveCount(0)
  await page.screenshot({ path: testInfo.outputPath('tool-management.png'), fullPage: true, animations: 'disabled' })
  await page.getByRole('tab', { name: '调用审计' }).click()
  await page.getByPlaceholder('完整 Run ID').fill(runId)
  const filtered = page.waitForRequest((request) => request.url().includes(`run_id=${runId}`))
  await page.getByRole('button', { name: '查询', exact: true }).click()
  await filtered
  await expect(page.getByRole('button', { name: '上一页' })).toBeDisabled()
  await page.getByRole('button', { name: '详情', exact: true }).click()
  await expect(page.getByText('工具调用详情', { exact: true })).toBeVisible()
  await expect(page.getByText('"values": "REDACTED"').first()).toBeVisible()
  await page.screenshot({ path: testInfo.outputPath('tool-audit.png'), fullPage: true, animations: 'disabled' })
})
