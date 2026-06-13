/**
 * Smoke test for the video upload API.
 *
 * Posts a small fixture MP4 to POST /api/uploads/video and asserts the
 * response shape.  This is a pure HTTP test — it does NOT exercise the
 * Expo camera-capture component.
 *
 * Requires the backend to be running at SACRIFICE_API_URL (default
 * http://localhost:8000) with a live database.  Uses the dev token
 * endpoint to avoid requiring a real Google/GitHub OAuth handshake.
 *
 * Run:
 *   cd frontend
 *   npx playwright test e2e/video_upload.spec.ts --project=chromium
 */
import { test, expect } from '@playwright/test';
import { createHash } from 'crypto';

test('video upload API smoke — accepts fixture video and returns 201', async ({ request }) => {
  const apiUrl = process.env['SACRIFICE_API_URL'] ?? 'http://localhost:8000';

  // Get a dev token
  const tokenResp = await request.get(`${apiUrl}/api/auth/dev/token`);
  if (tokenResp.status() !== 200) {
    test.skip(true, 'Dev token endpoint not available in this environment');
    return;
  }
  const { access_token: token } = await tokenResp.json();

  // Build a minimal valid MP4-like payload
  const videoBytes = Buffer.from(
    '\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42' + '\x00'.repeat(1024),
    'binary',
  );

  const formData = new FormData();
  formData.append(
    'file',
    new Blob([videoBytes], { type: 'video/mp4' }),
    'smoke-test.mp4',
  );
  formData.append('duration_seconds', '3.0');

  const response = await request.post(`${apiUrl}/api/uploads/video`, {
    headers: { Authorization: `Bearer ${token}` },
    multipart: formData,
  });

  expect(response.status()).toBe(201);

  const body = await response.json();
  expect(body).toHaveProperty('upload_id');
  expect(body).toHaveProperty('sha256');
  expect(typeof body.sha256).toBe('string');
  expect(body.sha256).toHaveLength(64);
  expect(body.size_bytes).toBeGreaterThan(0);
  expect(body.duration_seconds).toBe(3.0);
  expect(body.mime_type).toBe('video/mp4');

  // Verify the SHA-256 matches what we uploaded
  const expectedSha256 = createHash('sha256').update(videoBytes).digest('hex');
  expect(body.sha256).toBe(expectedSha256);
});