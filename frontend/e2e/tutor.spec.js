// E2E «теория → практика» на моках API (без бэкенда).
import { test, expect } from '@playwright/test'

const THEORY = { v: 1, type: 'theory', text: 'Квадратное уравнение $$x^2=4$$', payload: { topic: 'ур' } }
const ADAPTIVE = { student_id: 'stu_e2e', current_knowledge_level: 0.5, topic: 'ур', topic_level: 0.5, attempts: 0, correct: 0, difficulty: 'medium', recommended_next: null }

async function mockApi(page) {
  await page.route('**/chat/stream', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'text/event-stream',
      body: [
        'event: message',
        `data: ${JSON.stringify({ content: 'Текст', envelope: THEORY, adaptive: ADAPTIVE, error: null, session_id: 'ses_e2e' })}`,
        '',
        '',
        'event: done',
        'data: {"session_id":"ses_e2e","steps":1}',
        '',
        '',
      ].join('\n'),
    })
  })
  await page.route('**/student/**', (route) => route.fulfill({ status: 404, contentType: 'application/json', body: '{}' }))
  // Profile-мок регистрируем ПОСЛЕ catch-all: в Playwright побеждает последний зарегистрированный route.
  await page.route('**/student/*/profile', (route) =>
    route.fulfill({ status: 200, contentType: 'application/json', body: '{}' })
  )
  await page.route('**/chat/history/**', (route) => route.fulfill({ status: 404, contentType: 'application/json', body: '{}' }))
}

test('theory is rendered with latex and next lesson flows', async ({ page }) => {
  await mockApi(page)
  await page.addInitScript(() => {
    localStorage.setItem('edututor_student', JSON.stringify({
      student_id: 'stu_e2e',
      student_name: 'Иван Иванов',
      learner_type: 'schoolchild',
      grade: '8 класс',
      identity: 'иван иванов|schoolchild|8 класс',
      legacy: false,
    }))
  })
  await page.goto('/')
  await page.fill('input[placeholder="квадратные уравнения"]', 'ур')
  await page.click('button[type="submit"]')
  await expect(page.getByText('Адаптивность')).toBeVisible()
  await expect(page.locator('.block.theory')).toBeVisible()
})
