import { test, expect, type Page } from '@playwright/test';

const API_BASE = process.env.E2E_API_URL || 'http://localhost:8000';

// ─── Helpers ───────────────────────────────────────────────────────────

/**
 * Authenticate via the dev-token endpoint (debug mode only), seed
 * localStorage, reload, and confirm we land on the home screen.
 */
async function authenticateViaDevToken(page: Page): Promise<string> {
  const res = await page.request.get(
    `${API_BASE}/api/auth/dev/token?email=smoke-test@example.com`,
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
  const createButton = page.getByText('+ New');
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
 */
async function sendChatMessage(page: Page, text: string): Promise<void> {
  const input = page.getByTestId('chat-input');
  await input.fill(text);
  await page.getByTestId('send-button').click();
  // Wait for the "Thinking..." indicator to appear and disappear.
  await expect(page.getByText('Thinking...')).toBeVisible({ timeout: 5_000 });
  await expect(page.getByText('Thinking...')).not.toBeVisible({ timeout: 30_000 });
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

test.describe('Chat goal creation @smoke', () => {
  test.beforeEach(async ({ page }) => {
    await authenticateViaDevToken(page);
  });

  test('matched path @smoke: youtube_video goal created from chat', async ({ page }) => {
    // ── Navigate from home screen to chat creation ────────────────
    await openChatCreation(page);

    // ── Send the natural-language prompt ──────────────────────────
    await sendChatMessage(
      page,
      'I want to upload a YouTube walkthrough of my project by Friday and pledge $20 to charity',
    );

    // ── Assert the assistant surfaces a match card for youtube_video ──
    const matchCard = page.getByTestId('match-card');
    await expect(matchCard).toBeVisible({ timeout: 15_000 });
    await expect(matchCard.getByText(/YouTube Video/)).toBeVisible();
    await expect(page.getByTestId('use-this-button')).toBeVisible();
    await expect(page.getByTestId('try-another-button')).toBeVisible();

    // ── Click "Use this" to accept the match ──────────────────────
    await page.getByTestId('use-this-button').click();
    await expect(page.getByText('Thinking...')).not.toBeVisible({ timeout: 30_000 });

    // ── Assert assistant asks for deadline ────────────────────────
    const awaitingDeadline = page.getByTestId('awaiting-input-card');
    await expect(awaitingDeadline).toBeVisible({ timeout: 10_000 });
    await expect(awaitingDeadline.getByText("What's your deadline?")).toBeVisible();

    // ── Provide deadline ──────────────────────────────────────────
    await sendChatMessage(page, '2026-05-29T17:00:00Z');

    // ── Assert assistant asks for charity ─────────────────────────
    const awaitingCharity = page.getByTestId('awaiting-input-card');
    await expect(awaitingCharity).toBeVisible({ timeout: 10_000 });
    await expect(
      awaitingCharity.getByText('Which charity should receive the pledge if you miss the goal?'),
    ).toBeVisible();

    // ── Provide charity ───────────────────────────────────────────
    await sendChatMessage(page, 'Doctors Without Borders');

    // ── Assert assistant asks for video description ───────────────
    const awaitingVideo = page.getByTestId('awaiting-input-card');
    await expect(awaitingVideo).toBeVisible({ timeout: 10_000 });
    await expect(awaitingVideo.getByText('What should the video cover?')).toBeVisible();

    // ── Provide video description ─────────────────────────────────
    await sendChatMessage(page, 'A walkthrough of my latest project features');

    // ── Assert ready-to-create card appears ───────────────────────
    const readyCard = page.getByTestId('ready-to-create-card');
    await expect(readyCard).toBeVisible({ timeout: 10_000 });
    await expect(readyCard.getByText('Final review:')).toBeVisible();
    await expect(page.getByTestId('create-goal-button')).toBeVisible();

    // ── Click "Create goal" ───────────────────────────────────────
    await page.getByTestId('create-goal-button').click();
    // After creation, the app navigates to the goal detail screen.
    await expect(page.getByTestId('goal-detail-screen')).toBeVisible({ timeout: 15_000 });

    // ── Verify the exact goal exists via GET /api/goals ───────────
    const goals = await fetchGoals(page);
    const createdGoal = goals.find(
      (g) => g.goal_type === 'youtube_video',
    );
    expect(createdGoal).toBeDefined();
    expect(createdGoal!.goal_type).toBe('youtube_video');
  });

  test('no-match path @smoke: stubbed 501 surfaced in chat without crash', async ({ page }) => {
    // ── Navigate from home screen to chat creation ────────────────
    await openChatCreation(page);

    // ── Send a prompt that won't match any built-in goal type ─────
    await sendChatMessage(page, 'Track that I drank 8 glasses of water today');

    // ── Assert the assistant returns the no-match affordance ──────
    const noMatchCard = page.getByTestId('no-match-card');
    await expect(noMatchCard).toBeVisible({ timeout: 15_000 });
    await expect(
      noMatchCard.getByText("I don't have a built-in way to verify that yet."),
    ).toBeVisible();
    await expect(page.getByTestId('yes-build-it-button')).toBeVisible();
    await expect(page.getByTestId('let-me-rephrase-button')).toBeVisible();

    // ── Tap "Yes, build it" ───────────────────────────────────────
    await page.getByTestId('yes-build-it-button').click();
    await expect(page.getByText('Thinking...')).not.toBeVisible({ timeout: 30_000 });

    // ── Assert the stubbed 501 is surfaced as an honest assistant message ──
    const stubMessage = page.getByText(
      "Goal-type generation isn't enabled yet — coming in D010.",
    );
    await expect(stubMessage).toBeVisible({ timeout: 10_000 });

    // ── Verify the UI is still functional (no crash): input still works ──
    const input = page.getByTestId('chat-input');
    await expect(input).toBeVisible();
    await expect(input).toBeEnabled();
    // Type another message to confirm the app didn't crash.
    await input.fill('Can I try another approach?');
    await expect(page.getByTestId('send-button')).toBeEnabled();
  });
});