import { expect, test } from '@playwright/test';

const FRONTEND_BASE = process.env.E2E_BASE_URL || 'http://localhost:8083/?uxAuditScenario=camera-permission-denied';

test.describe('scheduled audit camera permission denied target', () => {
  test.beforeEach(async ({ context }) => {
    await context.clearPermissions();

    await context.addInitScript(() => {
      const deniedError = new Error('Camera permission denied');
      deniedError.name = 'NotAllowedError';

      const mediaDevices = navigator.mediaDevices || {};

      Object.defineProperty(mediaDevices, 'getUserMedia', {
        configurable: true,
        value: async () => {
          throw deniedError;
        },
      });

      Object.defineProperty(navigator, 'mediaDevices', {
        configurable: true,
        value: mediaDevices,
      });
    });
  });

  test('AC1.1-AC1.6: can open app, trigger Record proof, deny camera, and verify denied branch copy/actions', async ({
    page,
  }) => {
    const response = await page.goto(FRONTEND_BASE, { waitUntil: 'domcontentloaded' });
    expect(response?.status()).toBe(200);

    const recordProofButton = page.getByRole('button', { name: 'Record proof' });
    await expect(recordProofButton).toBeVisible({ timeout: 15_000 });

    await recordProofButton.click();

    await expect(page.getByText('Camera access is required to submit this proof')).toBeVisible({
      timeout: 15_000,
    });
    await expect(page.getByText('Open settings')).toBeVisible();
    await expect(page.getByText('Cancel')).toBeVisible();
  });
});
