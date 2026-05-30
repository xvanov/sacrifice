import { test, expect } from '@playwright/test';
import { createHash } from 'node:crypto';
import * as fs from 'node:fs';
import * as path from 'node:path';

const BASE_URL = 'http://localhost:8000';
const FIXTURE_PATH = path.resolve(__dirname, 'fixtures', 'minimal.mp4');
const FIXTURE_BYTES = fs.readFileSync(FIXTURE_PATH);
const FIXTURE_SHA256 = createHash('sha256').update(FIXTURE_BYTES).digest('hex');
const FIXTURE_SIZE = FIXTURE_BYTES.length;

test.describe('POST /api/uploads/video', { tag: ['@smoke'] }, () => {
  test('returns 201 with expected response shape', async ({ request }) => {
    // Authenticate via dev token endpoint
    const tokenResp = await request.get(
      `${BASE_URL}/api/auth/dev/token?email=smoke-test@example.com`
    );
    expect(tokenResp.status()).toBe(200);
    const { access_token } = await tokenResp.json();

    // Upload fixture video via multipart/form-data
    const resp = await request.post(`${BASE_URL}/api/uploads/video`, {
      headers: { Authorization: `Bearer ${access_token}` },
      multipart: {
        file: {
          name: 'fixture.mp4',
          mimeType: 'video/mp4',
          buffer: FIXTURE_BYTES,
        },
        duration_seconds: '12.5',
      },
    });

    expect(resp.status()).toBe(201);

    const body = await resp.json();

    // Exact key set per api_spec.md
    expect(Object.keys(body).sort()).toEqual([
      'duration_seconds',
      'mime_type',
      'sha256',
      'size_bytes',
      'upload_id',
    ]);

    // upload_id must be a valid UUID4
    expect(typeof body.upload_id).toBe('string');
    expect(body.upload_id).toMatch(
      /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/
    );

    expect(body.sha256).toBe(FIXTURE_SHA256);
    expect(body.size_bytes).toBe(FIXTURE_SIZE);
    expect(body.duration_seconds).toBe(12.5);
    expect(body.mime_type).toBe('video/mp4');
  });
});