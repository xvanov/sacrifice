/**
 * Sign-in e2e spec for deployed frontend origins.
 *
 * Provider execution is mocked by simulating post-provider redirects with
 * `?auth_code=...` and stubbing auth + goals API responses. This keeps the
 * test reproducible across environments while still exercising Google/GitHub
 * callback branches and asserting authenticated end state.
 *
 * Run against any deployed base URL:
 *   cd frontend
 *   E2E_BASE_URL=https://your-frontend-origin npm run test:e2e:signin
 */
import { test, expect, type Page } from '@playwright/test';

const FRONTEND_BASE = process.env.E2E_BASE_URL || 'http://localhost:8082';

type Provider = 'google' | 'github';

interface MockUser {
  id: string;
  email: string;
  display_name: string;
  auth_provider: Provider;
}

interface MockSession {
  accessToken: string;
  user: MockUser;
}

async function installAuthMocks(page: Page, provider: Provider): Promise<MockSession> {
  const accessToken = `e2e-mock-jwt-${provider}-${Date.now()}`;
  const user: MockUser = {
    id: `e2e-${provider}-user`,
    email: `e2e-${provider}@example.com`,
    display_name: `E2E ${provider === 'google' ? 'Google' : 'GitHub'} User`,
    auth_provider: provider,
  };

  await page.route('**/api/auth/exchange', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ access_token: accessToken, user }),
    });
  });

  await page.route('**/api/auth/me', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(user),
    });
  });

  await page.route('**/api/goals**', async (route) => {
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify([]),
    });
  });

  return { accessToken, user };
}

async function runMockedProviderCallback(page: Page, provider: Provider, expectedToken: string): Promise<void> {
  await page.goto(`${FRONTEND_BASE}?auth_code=e2e-mock-auth-code-${provider}`, {
    waitUntil: 'domcontentloaded',
  });

  await expect(
    page.getByText('+ New goal'),
    `${provider} auth_code flow should reach authenticated shell`,
  ).toBeVisible({ timeout: 20_000 });

  const storedToken = await page.evaluate(() => localStorage.getItem('sacrifice_auth_token'));
  expect(storedToken, 'mock exchange token must persist in localStorage').toBe(expectedToken);
}

test.describe('Sign-in e2e', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto(`${FRONTEND_BASE}?__e2e_clear=1`, { waitUntil: 'domcontentloaded' });
    await page.evaluate(() => {
      localStorage.removeItem('sacrifice_auth_token');
      localStorage.removeItem('sacrifice_chat_goal_create_session');
    });
  });

  test('AC1.1 + AC1.3 — Google sign-in reaches authenticated end state', async ({ page }) => {
    const { accessToken } = await installAuthMocks(page, 'google');
    await runMockedProviderCallback(page, 'google', accessToken);
  });

  test('AC1.2 + AC1.3 — GitHub sign-in reaches authenticated end state', async ({ page }) => {
    const { accessToken } = await installAuthMocks(page, 'github');
    await runMockedProviderCallback(page, 'github', accessToken);
  });

  test('AC1.4 — provider execution uses mock callback path (no external OAuth host calls)', async ({
    page,
  }) => {
    const providerHosts = ['accounts.google.com', 'github.com/login/oauth'];
    const externalProviderRequests: string[] = [];

    page.on('request', (req) => {
      const url = req.url();
      if (providerHosts.some((host) => url.includes(host))) {
        externalProviderRequests.push(url);
      }
    });

    const { accessToken } = await installAuthMocks(page, 'google');
    await runMockedProviderCallback(page, 'google', accessToken);

    expect(
      externalProviderRequests,
      'mock callback path must avoid real provider URLs',
    ).toHaveLength(0);
  });
});
