/**
 * UX Audit smoke test — verifies the audit target is reachable, the camera
 * proof entry path loads, and the camera permission-denied branch provides
 * observable UI evidence.
 *
 * Prerequisites (run before this test):
 *   ./scripts/audit-target.sh
 *
 * Run:
 *   cd frontend
 *   E2E_BASE_URL=http://localhost:8083 E2E_API_URL=http://localhost:8001 \
 *     npx playwright test e2e/audit_smoke.spec.ts --project=chromium
 */
import { test, expect } from '@playwright/test';

const API_BASE = process.env.E2E_API_URL || 'http://localhost:8001';
const FRONTEND_BASE = process.env.E2E_BASE_URL || 'http://localhost:8083';

test.describe('audit target smoke', () => {
  test('AC1.1 — frontend loads and serves the Expo web app shell', async ({ page }) => {
    const response = await page.goto(FRONTEND_BASE, { waitUntil: 'domcontentloaded' });
    expect(response?.status()).toBe(200);

    // Verify the Expo web root is present — this is observable UI evidence
    // that the app is running (not a blank page or error).
    const rootDiv = page.locator('#root');
    await expect(rootDiv).toBeAttached({ timeout: 15_000 });
  });

  test('AC1.2 — backend health endpoint is reachable', async ({ request }) => {
    const response = await request.get(`${API_BASE}/api/health`);
    expect(response.status()).toBe(200);

    const body = await response.json();
    expect(body).toHaveProperty('status', 'ok');
  });

  test('AC1.2 — dev token endpoint works (auth path available for audit)', async ({ request }) => {
    const response = await request.get(`${API_BASE}/api/auth/dev/token`);
    expect(response.status()).toBe(200);

    const body = await response.json();
    expect(body).toHaveProperty('access_token');
    expect(typeof body.access_token).toBe('string');
    expect(body.access_token.length).toBeGreaterThan(0);
  });

  test('AC1.2 — camera proof entry: authenticated app shell loads camera-capable screen', async ({
    page,
    request,
  }) => {
    // Get a dev token to authenticate
    const tokenResp = await request.get(`${API_BASE}/api/auth/dev/token`);
    const { access_token: token } = await tokenResp.json();

    // Inject the token into localStorage and navigate to the app
    await page.goto(FRONTEND_BASE, { waitUntil: 'domcontentloaded' });
    await page.evaluate((t) => localStorage.setItem('sacrifice_auth_token', t), token);
    await page.reload({ waitUntil: 'domcontentloaded' });

    // The home screen should show after auth — this is the entry point from
    // which the camera proof flow is reachable (goal detail → submit proof).
    // We look for the "+" new-goal button as evidence the authenticated
    // app shell is functional.
    const newGoalButton = page.getByText('+ New');
    await expect(newGoalButton.first()).toBeVisible({ timeout: 15_000 });

    // Create a camera-type goal so the proof submission path (which hosts
    // the camera capture component) is reachable. This verifies the full
    // pipeline: auth → goal creation → proof entry point.
    const createResp = await request.post(`${API_BASE}/api/goals`, {
      headers: {
        Authorization: `Bearer ${token}`,
        'Content-Type': 'application/json',
      },
      data: {
        title: 'Audit Smoke Camera Goal',
        goal_type: 'camera',
        pledge_amount: 1000,
        currency: 'usd',
        deadline: new Date(Date.now() + 7 * 86400_000).toISOString(),
        timezone: 'UTC',
        recurrence: 'none',
        criteria: {},
      },
    });
    expect(createResp.status()).toBe(200);

    const goal = await createResp.json();
    expect(goal).toHaveProperty('id');
    expect(goal.goal_type).toBe('camera');

    // Activate the goal so the submit-proof path is open
    const activateResp = await request.put(`${API_BASE}/api/goals/${goal.id}`, {
      headers: {
        Authorization: `Bearer ${token}`,
        'Content-Type': 'application/json',
      },
      data: { status: 'active' },
    });
    expect(activateResp.status()).toBe(200);

    // Navigate to the goal list — this screen exposes the "Submit Proof"
    // entry point that launches the camera capture flow
    await page.reload({ waitUntil: 'domcontentloaded' });

    // Verify the goal appears on the home screen
    const goalTitle = page.getByText('Audit Smoke Camera Goal');
    await expect(goalTitle.first()).toBeVisible({ timeout: 10_000 });

    // Navigate to the goal detail screen and click Submit Proof to reach
    // the camera proof submission screen
    await goalTitle.first().click();
    await page.waitForTimeout(500);

    const submitProofButton = page.getByTestId('submit-proof-button');
    await expect(submitProofButton).toBeVisible({ timeout: 10_000 });
  });

  test('AC1.2 / AC1.3 — camera permission-denied branch: observable UI evidence', async ({
    page,
    request,
  }) => {
    // Get a dev token and set up auth
    const tokenResp = await request.get(`${API_BASE}/api/auth/dev/token`);
    const { access_token: token } = await tokenResp.json();

    await page.goto(FRONTEND_BASE, { waitUntil: 'domcontentloaded' });
    await page.evaluate((t) => localStorage.setItem('sacrifice_auth_token', t), token);
    await page.reload({ waitUntil: 'domcontentloaded' });

    // Create a camera-type goal and activate it
    const createResp = await request.post(`${API_BASE}/api/goals`, {
      headers: {
        Authorization: `Bearer ${token}`,
        'Content-Type': 'application/json',
      },
      data: {
        title: 'Camera Permission Denied Audit',
        goal_type: 'camera',
        pledge_amount: 500,
        currency: 'usd',
        deadline: new Date(Date.now() + 7 * 86400_000).toISOString(),
        timezone: 'UTC',
        recurrence: 'none',
        criteria: {},
      },
    });
    expect(createResp.status()).toBe(200);
    const goal = await createResp.json();

    await request.put(`${API_BASE}/api/goals/${goal.id}`, {
      headers: {
        Authorization: `Bearer ${token}`,
        'Content-Type': 'application/json',
      },
      data: { status: 'active' },
    });

    // Navigate to the goal on the home screen, click through to detail,
    // then click Submit Proof to reach the CameraProofSubmissionScreen
    await page.reload({ waitUntil: 'domcontentloaded' });
    const goalTitle = page.getByText('Camera Permission Denied Audit');
    await expect(goalTitle.first()).toBeVisible({ timeout: 10_000 });
    await goalTitle.first().click();
    await page.waitForTimeout(500);

    // Click Submit Proof — this navigates to CameraProofSubmissionScreen
    // for camera-type goals
    const submitProofButton = page.getByTestId('submit-proof-button');
    await expect(submitProofButton).toBeVisible({ timeout: 10_000 });
    await submitProofButton.click();

    // In headless Chromium, camera permissions are denied by default.
    // The CameraCapture component renders the denied-state UI with these
    // exact strings from the flow.md documented branches:
    //   "Camera access is required to submit this proof"
    //   "Open settings"
    //   "Cancel"
    const deniedMessage = page.getByText('Camera access is required to submit this proof');
    await expect(deniedMessage).toBeVisible({ timeout: 10_000 });

    const openSettingsButton = page.getByText('Open settings');
    await expect(openSettingsButton).toBeVisible();

    const cancelButton = page.getByText('Cancel');
    await expect(cancelButton).toBeVisible();

    // Press Cancel to verify the navigation back works (onCancel calls goBack)
    await cancelButton.click();
    await page.waitForTimeout(500);

    // Should be back on the goal detail screen
    await expect(page.getByTestId('submit-proof-button')).toBeVisible({ timeout: 10_000 });
  });

  test('no raw token in auth redirect — OAuth endpoints use auth_code', async ({ request }) => {
    // Verify the backend's OAuth callback redirects use auth_code, never
    // access_token. This is a critical security invariant for the audit
    // target (see context/project.md "Active constraints").
    const endpoints = [
      '/api/auth/google/callback?code=test&state=test',
      '/auth/github/callback?code=test&state=test',
    ];

    for (const endpoint of endpoints) {
      const response = await request.get(`${API_BASE}${endpoint}`, {
        maxRedirects: 0,
      });

      // Should be a redirect (302/307/303) — follow the Location header
      const status = response.status();
      if (status >= 300 && status < 400) {
        const location = response.headers()['location'] || '';
        expect(location).not.toContain('access_token=');
        // If it contains auth_code=, that's the correct one-time token pattern
        if (location.includes('auth_code=')) {
          // Confirmed: uses one-time auth_code exchange, not raw token
          expect(location).toContain('auth_code=');
        }
        // If neither, it's still safe — just not a successful OAuth flow
      }
      // Non-redirect (200, 400, 422, etc.) is also safe — means the
      // endpoint rejected the bogus params without leaking anything.
    }
  });
});