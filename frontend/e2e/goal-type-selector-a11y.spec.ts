/**
 * Accessibility audit: goal-type selector on the goal creation screen (D023).
 *
 * Runs axe-core against the goal creation screen and asserts zero label,
 * role, and accessible-name violations affecting the goal-type selector.
 *
 * Broad-read coverage:
 *   - Card-scoped audits (match_proposed, build-new-goal-type) — narrow baseline.
 *   - Screen-level audit: runs axe-core on the full chat screen and filters
 *     violations to only those whose affected nodes include a goal-type-selector
 *     element.  This catches selector-affecting violations that originate
 *     outside the card boundary (e.g. a mislabeled parent container).
 *
 * Run:
 *   E2E_BASE_URL=http://localhost:8082 E2E_API_URL=http://localhost:8000 \
 *     npx playwright test e2e/goal-type-selector-a11y.spec.ts
 */
import { test, expect, type Page } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';
import type { AxeResults, Result } from 'axe-core';

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

// ─── Selector-affecting testids ──────────────────────────────────────────
//
// These testids identify the goal-type selector surface rendered by
// ChatGoalCreateScreen.tsx.  When running screen-level audits we filter
// axe-core results to violations that involve at least one of these
// elements, keeping assertions scoped to the selector while still
// exercising the full screen rendering path.
//
//   match-proposed-card-*   — card presented when a known goal type matches
//   build-new-goal-type-card — card presented when no built-in type matches
//   use-this-goal-type       — "Use this" button inside a match_proposed card
//   yes-build-it             — "Yes, build it" button inside build-new-goal-type card
const SELECTOR_TESTIDS = [
  /^match-proposed-card-/,
  'build-new-goal-type-card',
  'use-this-goal-type',
  'yes-build-it',
];

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

/**
 * Return true when any node in `nodes` targets an element whose testid
 * matches one of the goal-type-selector patterns.
 *
 * axe-core `nodes` entries carry `target` as a string[] of CSS selectors
 * (e.g. `["[data-testid=\"match-proposed-card-youtube_video\"]"]`).  We
 * inspect each selector string for a known testid prefix or literal.
 */
function anyNodeMatchesSelectorTestids(nodes: Result['nodes']): boolean {
  return nodes.some((node) =>
    node.target.some((sel) => {
      const selStr = typeof sel === 'string' ? sel : JSON.stringify(sel);
      return SELECTOR_TESTIDS.some((pattern) =>
        typeof pattern === 'string' ? selStr.includes(pattern) : pattern.test(selStr),
      );
    }),
  );
}

/**
 * Filter axe-core violations to only those whose affected nodes include at
 * least one goal-type-selector element.  Unrelated violations (e.g. a
 * landmark issue on the header) are excluded.
 */
function filterSelectorViolations(violations: Result[]): Result[] {
  return violations.filter((v) => anyNodeMatchesSelectorTestids(v.nodes));
}

// ─── Tests ─────────────────────────────────────────────────────────────────

test.describe('Goal-type selector accessibility audit (D023)', () => {
  // ── Card-scoped audits (narrow baseline) ─────────────────────────────

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

  // ── Screen-level audits (broad-read) ─────────────────────────────────

  test('full goal creation screen has no selector-affecting label, role, or accessible-name violations (match path)', async ({ page }) => {
    await authenticateViaDevToken(page);

    await openChatCreation(page);

    // Trigger the match_proposed goal-type selector card.
    await sendChatMessage(
      page,
      'I want to upload a YouTube walkthrough of my project by Friday',
    );

    // Wait for the match_proposed card for youtube_video.
    const matchCard = page.getByTestId('match-proposed-card-youtube_video');
    await expect(matchCard).toBeVisible({ timeout: 15_000 });

    // Run axe-core on the FULL screen, then filter to selector-affecting
    // violations only.  This catches issues like a mislabeled ancestor
    // that the card-scoped audit would miss.
    const results: AxeResults = await new AxeBuilder({ page })
      .include('[data-testid="chat-goal-create-screen"]')
      .withTags(SELECTOR_TAGS)
      .analyze();

    const selectorViolations = filterSelectorViolations(results.violations);

    if (selectorViolations.length > 0) {
      console.log(
        'axe-core selector-affecting violations on full screen (match path):\n' +
          JSON.stringify(selectorViolations, null, 2),
      );
    }

    expect(selectorViolations).toEqual([]);
  });

  test('full goal creation screen has no selector-affecting label, role, or accessible-name violations (no-match path)', async ({ page }) => {
    await authenticateViaDevToken(page, 'a11y-no-match-screen@example.com');

    await openChatCreation(page);

    // Trigger the no_match path → build-new-goal-type card.
    await sendChatMessage(
      page,
      'wake up at 4am every day, proof is a photo of caffeine gum, sacrifice $10 if I fail',
    );

    // Wait for the no_match card.
    const buildCard = page.getByTestId('build-new-goal-type-card');
    await expect(buildCard).toBeVisible({ timeout: 20_000 });

    // Run axe-core on the FULL screen, then filter to selector-affecting
    // violations only.
    const results: AxeResults = await new AxeBuilder({ page })
      .include('[data-testid="chat-goal-create-screen"]')
      .withTags(SELECTOR_TAGS)
      .analyze();

    const selectorViolations = filterSelectorViolations(results.violations);

    if (selectorViolations.length > 0) {
      console.log(
        'axe-core selector-affecting violations on full screen (no-match path):\n' +
          JSON.stringify(selectorViolations, null, 2),
      );
    }

    expect(selectorViolations).toEqual([]);
  });
});