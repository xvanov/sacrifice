/**
 * Accessibility audit: goal-type selector on the goal creation screen (D023).
 *
 * Runs axe-core scoped to the match_proposed card (the goal-type selector)
 * and asserts zero label, role, and accessible-name violations.
 *
 * Run:
 *   E2E_BASE_URL=http://localhost:8082 E2E_API_URL=http://localhost:8000 \
 *     npx playwright test e2e/goal-type-selector-a11y.spec.ts
 */
import { test, expect, type Page } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';

const API_BASE = process.env.E2E_API_URL || 'http://localhost:8000';

// ─── Tag scoping: only label, role, accessible-name violations ───────────
//
// wcag412  = WCAG 4.1.2 "Name, Role, Value" — covers accessible-name and
//            role violations (button-name, link-name, aria-input-field-name,
//            aria-roles, aria-required-attr, nested-interactive, etc.).
// cat.forms = form label rules (label, select-name, form-field-multiple-labels).
//
// Together these tags surface selector-affecting label, role, and
// accessible-name violations without broadening into color-contrast,
// landmark-structure, or other unrelated audits.
const SELECTOR_TAGS = ['wcag412', 'cat.forms'];

// ─── Helpers ───────────────────────────────────────────────────────────────

async function authenticateViaDevToken(
  page: Page,
  email = 'a11y-test@example.com',
): Promise<string> {
  const res = await page.request.get(
    `${API_BASE}/api/auth/dev/token?email=${encodeURIComponent(email)}`,
  );
  expect(res.status()).toBe(200);
  const body = await res.json();
  const token: string = body.access_token;

  await page.goto('/');
  await page.evaluate((t) => {
    localStorage.setItem('sacrifice_auth_token', t);
  }, token);

  await page.reload();
  await expect(page.getByText('+ New')).toBeVisible({ timeout: 15_000 });
  return token;
}

async function openChatCreation(page: Page): Promise<void> {
  const createButton = page.getByText('+ New').first();
  await expect(createButton).toBeVisible({ timeout: 15_000 });
  await createButton.click();
  await expect(page.getByTestId('chat-goal-create-screen')).toBeVisible({ timeout: 10_000 });
  await expect(page.getByTestId('chat-input')).toBeVisible();
}

async function sendChatMessage(page: Page, text: string): Promise<void> {
  const input = page.getByTestId('chat-input');
  await input.fill(text);
  await page.getByTestId('send-button').click();
  await expect(page.getByText('Thinking...')).toBeVisible({ timeout: 5_000 });
  await expect(page.getByText('Thinking...')).not.toBeVisible({ timeout: 30_000 });
}

// ─── Tests ─────────────────────────────────────────────────────────────────

test.describe('Goal-type selector accessibility audit (D023)', () => {
  test('match_proposed card has no label, role, or accessible-name violations', async ({ page }) => {
    await authenticateViaDevToken(page);

    // Navigate to the goal creation chat screen.
    await openChatCreation(page);

    // Trigger the match_proposed goal-type selector card.
    await sendChatMessage(
      page,
      'I want to upload a YouTube walkthrough of my project by Friday',
    );

    // Wait for the match_proposed card for youtube_video.
    const matchCard = page.getByTestId('match-proposed-card-youtube_video');
    await expect(matchCard).toBeVisible({ timeout: 15_000 });

    // Run axe-core scoped to the match_proposed card only.
    const results = await new AxeBuilder({ page })
      .include('[data-testid="match-proposed-card-youtube_video"]')
      .withTags(SELECTOR_TAGS)
      .analyze();

    // Surface any violations as a test failure with actionable detail.
    if (results.violations.length > 0) {
      console.log(
        'axe-core violations on goal-type selector:\n' +
          JSON.stringify(results.violations, null, 2),
      );
    }

    expect(results.violations).toEqual([]);
  });

  test('build-new-goal-type card has no label, role, or accessible-name violations', async ({ page }) => {
    await authenticateViaDevToken(page, 'a11y-no-match@example.com');

    // Navigate to the goal creation chat screen.
    await openChatCreation(page);

    // Trigger the no_match path → build-new-goal-type card.
    await sendChatMessage(
      page,
      'wake up at 4am every day, proof is a photo of caffeine gum, sacrifice $10 if I fail',
    );

    // Wait for the no_match card.
    const buildCard = page.getByTestId('build-new-goal-type-card');
    await expect(buildCard).toBeVisible({ timeout: 20_000 });

    // Run axe-core scoped to the build-new-goal-type card only.
    const results = await new AxeBuilder({ page })
      .include('[data-testid="build-new-goal-type-card"]')
      .withTags(SELECTOR_TAGS)
      .analyze();

    if (results.violations.length > 0) {
      console.log(
        'axe-core violations on build-new-goal-type card:\n' +
          JSON.stringify(results.violations, null, 2),
      );
    }

    expect(results.violations).toEqual([]);
  });
});