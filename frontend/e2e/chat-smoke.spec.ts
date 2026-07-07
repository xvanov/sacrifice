import { test, expect, type Page } from '@playwright/test';

const API_BASE = process.env.E2E_API_URL || 'http://localhost:8000';

// ─── Helpers ───────────────────────────────────────────────────────────

/**
 * Authenticate via the dev-token endpoint (debug mode only), seed
 * localStorage, reload, and confirm we land on the home screen.
 */
async function authenticateViaDevToken(
  page: Page,
  email = 'smoke-test@example.com',
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
  // Wait for the home screen to render.
  await expect(page.getByText('+ New')).toBeVisible({ timeout: 15_000 });
  return token;
}

/**
 * Navigate from the home screen to the chat goal creation surface and
 * assert the chat greeting is visible.
 */
async function openChatCreation(page: Page): Promise<void> {
  const createButton = page.getByText('+ New').first();
  await expect(createButton).toBeVisible({ timeout: 15_000 });
  await createButton.click();

  // Assert the chat screen loaded: greeting message and input bar.
  await expect(page.getByTestId('chat-goal-create-screen')).toBeVisible({ timeout: 10_000 });
  await expect(page.getByText("Tell me what you want to do, and I'll figure out how to track it."))
    .toBeVisible({ timeout: 10_000 });
  await expect(page.getByTestId('chat-input')).toBeVisible();
}

/**
 * Send a message through the chat UI: type in the input and tap Send.
 * Waits for the "Thinking..." indicator to appear and disappear.
 */
async function sendChatMessage(page: Page, text: string): Promise<void> {
  const input = page.getByTestId('chat-input');
  await input.fill(text);
  await page.getByTestId('send-button').click();
  await expect(page.getByText('Thinking...')).toBeVisible({ timeout: 5_000 });
  await expect(page.getByText('Thinking...')).not.toBeVisible({ timeout: 30_000 });
}

/**
 * Reply to an awaiting_input prompt. Slot-filling replies are handled by a
 * fast server path (no LLM call), so the "Thinking..." indicator may flash
 * too briefly to assert on — the caller instead awaits the next card, which
 * provides the synchronization point.
 */
async function answerPrompt(page: Page, text: string): Promise<void> {
  const input = page.getByTestId('chat-input');
  await input.fill(text);
  await page.getByTestId('send-button').click();
}

/**
 * Fetch goals via the API for final verification.
 */
async function fetchGoals(page: Page): Promise<Array<{ id: string; goal_type: string }>> {
  const token = await page.evaluate(() =>
    localStorage.getItem('sacrifice_auth_token'),
  );
  const res = await page.request.get(`${API_BASE}/api/goals`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  expect(res.status()).toBe(200);
  return res.json();
}

// ─── Tests ─────────────────────────────────────────────────────────────
//
// NOTE: these selectors and assistant prompts are aligned to the ACTUAL
// ChatGoalCreateScreen component and chat backend state machine (verified
// against the live stack), not a speculative contract. The matched flow is:
//   match_proposed → "Use this" → awaiting_input(pledge_amount) →
//   awaiting_input(charity_id) → awaiting_input(min_duration_seconds) →
//   ready_to_create → "Create goal" → success message (stays on chat).
// The deadline criterion is auto-extracted from "by Friday", so it is not
// prompted for.

test.describe('Chat goal creation @smoke', () => {
  test.beforeEach(async ({ page }) => {
    await authenticateViaDevToken(page);
  });

  test('matched path @smoke: youtube_video goal created from chat', async ({ page }) => {
    await openChatCreation(page);

    // ── Send the natural-language prompt ──────────────────────────
    await sendChatMessage(
      page,
      'I want to upload a YouTube walkthrough of my project by Friday',
    );

    // ── Assert the assistant surfaces a match card for youtube_video ──
    const matchCard = page.getByTestId('match-proposed-card-youtube_video');
    await expect(matchCard).toBeVisible({ timeout: 15_000 });
    await expect(matchCard.getByText(/Matched type: youtube_video/)).toBeVisible();
    await expect(page.getByTestId('use-this-goal-type')).toBeVisible();

    // ── Click "Use this" to accept the match ──────────────────────
    await page.getByTestId('use-this-goal-type').click();
    await expect(page.getByText('Thinking...')).not.toBeVisible({ timeout: 30_000 });

    // ── Slot-filling: pledge_amount ───────────────────────────────
    await expect(page.getByTestId('awaiting-input-pledge_amount')).toBeVisible({ timeout: 10_000 });
    await expect(
      page.getByTestId('awaiting-input-pledge_amount').getByText('How much do you want to pledge?'),
    ).toBeVisible();
    await answerPrompt(page, '20');

    // ── Slot-filling: charity_id ──────────────────────────────────
    await expect(page.getByTestId('awaiting-input-charity_id')).toBeVisible({ timeout: 10_000 });
    await expect(
      page
        .getByTestId('awaiting-input-charity_id')
        .getByText('Which charity should receive the pledge if you miss it?'),
    ).toBeVisible();
    await answerPrompt(page, 'Doctors Without Borders');

    // ── Slot-filling: min_duration_seconds ────────────────────────
    await expect(page.getByTestId('awaiting-input-min_duration_seconds')).toBeVisible({ timeout: 10_000 });
    await expect(
      page
        .getByTestId('awaiting-input-min_duration_seconds')
        .getByText('How long should the video be at minimum?'),
    ).toBeVisible();
    await answerPrompt(page, '60');

    // ── Ready-to-create review card ───────────────────────────────
    const readyCard = page.getByTestId('ready-to-create-card');
    await expect(readyCard).toBeVisible({ timeout: 10_000 });
    await expect(readyCard.getByText('Ready to create')).toBeVisible();
    await expect(page.getByTestId('create-goal-confirm')).toBeVisible();

    // ── Click "Create goal" ───────────────────────────────────────
    await page.getByTestId('create-goal-confirm').click();

    // After creation the chat shows a success message (it does not
    // navigate away — the user returns Home to see the goal).
    await expect(
      page.getByText('Your goal is created and active. You can track it from the home screen.'),
    ).toBeVisible({ timeout: 15_000 });

    // ── Verify the goal exists via GET /api/goals ─────────────────
    const goals = await fetchGoals(page);
    const createdGoal = goals.find((g) => g.goal_type === 'youtube_video');
    expect(createdGoal).toBeDefined();
    expect(createdGoal!.goal_type).toBe('youtube_video');
  });

  test('no-match path @smoke: build a new goal type is accepted', async ({ page }) => {
    // Use a unique user so the cross-session "already building" (409) guard
    // and the per-user daily spend cap never make this flaky across re-runs.
    const email = `build-${Date.now()}@example.com`;
    await authenticateViaDevToken(page, email);
    await openChatCreation(page);

    // ── Send a prompt that won't match any built-in goal type ─────
    await sendChatMessage(
      page,
      'wake up at 4am every day, proof is a photo of caffeine gum, sacrifice $10 if I fail',
    );

    // ── Assert the assistant returns the build-new-goal-type card ──
    const noMatchCard = page.getByTestId('build-new-goal-type-card');
    await expect(noMatchCard).toBeVisible({ timeout: 20_000 });
    await expect(
      noMatchCard.getByText("I don't have a built-in way to verify that yet."),
    ).toBeVisible();
    await expect(page.getByTestId('yes-build-it')).toBeVisible();
    await expect(noMatchCard.getByText('Let me rephrase')).toBeVisible();

    // ── Tap "Yes, build it" → the request must be ACCEPTED (no 422) ──
    // This drives a real synthesis LLM call + direction write, so allow time.
    await page.getByTestId('yes-build-it').click();
    await expect(
      page.getByText("On it — I'm building a new goal type for this", { exact: false }),
    ).toBeVisible({ timeout: 60_000 });
    // A persistent "building…" status card is shown (the visible indication
    // that generation is in progress, and what resumes on navigate-away/back).
    await expect(page.getByTestId('generation-status-card')).toBeVisible({ timeout: 10_000 });
    // Regression guard: the old bug surfaced "Failed to request new goal type: HTTP 422".
    await expect(page.getByText(/Failed to request new goal type/)).toHaveCount(0);

    // ── UI stays interactive (no crash) ───────────────────────────
    const input = page.getByTestId('chat-input');
    await expect(input).toBeVisible();
    await expect(input).toBeEnabled();
  });
});
