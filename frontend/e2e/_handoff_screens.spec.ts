/**
 * Handoff helper (not a smoke assertion): captures screenshots of the key
 * screens against the live stack so the human reviewer can eyeball them.
 * Output: frontend/handoff-screenshots/*.png
 *
 *   E2E_BASE_URL=http://localhost:8090 E2E_API_URL=http://localhost:8000 \
 *     npx playwright test e2e/_handoff_screens.spec.ts
 */
import { test, expect, type Page } from '@playwright/test';

const API_BASE = process.env.E2E_API_URL || 'http://localhost:8000';
const DIR = 'handoff-screenshots';

async function auth(page: Page): Promise<void> {
  const res = await page.request.get(`${API_BASE}/api/auth/dev/token?email=handoff@example.com`);
  const { access_token } = await res.json();
  await page.goto('/');
  await page.evaluate((t) => localStorage.setItem('sacrifice_auth_token', t), access_token);
  await page.reload();
  await expect(page.getByText('+ New')).toBeVisible({ timeout: 15_000 });
}

test('capture handoff screenshots', async ({ page }) => {
  await auth(page);
  await page.screenshot({ path: `${DIR}/01-home.png`, fullPage: true });

  await page.getByText('+ New').first().click();
  await expect(page.getByTestId('chat-goal-create-screen')).toBeVisible({ timeout: 10_000 });
  await page.screenshot({ path: `${DIR}/02-chat-greeting.png`, fullPage: true });

  const input = page.getByTestId('chat-input');
  await input.fill('I want to upload a YouTube walkthrough of my project by Friday');
  await page.getByTestId('send-button').click();
  await expect(page.getByTestId('match-proposed-card-youtube_video')).toBeVisible({ timeout: 45_000 });
  await page.screenshot({ path: `${DIR}/03-match-card.png`, fullPage: true });

  await page.getByTestId('use-this-goal-type').click();
  await expect(page.getByTestId('awaiting-input-pledge_amount')).toBeVisible({ timeout: 15_000 });
  await input.fill('20');
  await page.getByTestId('send-button').click();
  await expect(page.getByTestId('awaiting-input-charity_id')).toBeVisible({ timeout: 15_000 });
  await input.fill('Doctors Without Borders');
  await page.getByTestId('send-button').click();
  await expect(page.getByTestId('awaiting-input-min_duration_seconds')).toBeVisible({ timeout: 15_000 });
  await input.fill('60');
  await page.getByTestId('send-button').click();
  await expect(page.getByTestId('ready-to-create-card')).toBeVisible({ timeout: 15_000 });
  await page.screenshot({ path: `${DIR}/04-ready-to-create.png`, fullPage: true });

  await page.getByTestId('create-goal-confirm').click();
  await expect(
    page.getByText('Your goal is created and active. You can track it from the home screen.'),
  ).toBeVisible({ timeout: 15_000 });
  await page.screenshot({ path: `${DIR}/05-goal-created.png`, fullPage: true });
});
