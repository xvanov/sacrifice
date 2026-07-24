import { CAMERA_PERMISSION_DENIED_AUDIT_SCENARIO, getApiBaseUrl } from '../config';

/**
 * Config module tests (AC2.1, AC2.2, AC2.3).
 *
 * Validates that the app resolves its backend base URL from
 * EXPO_PUBLIC_API_URL configuration with a sane localhost default.
 */
describe('getApiBaseUrl', () => {
  const originalEnv = process.env;

  beforeEach(() => {
    jest.resetModules();
    process.env = { ...originalEnv };
    delete process.env.EXPO_PUBLIC_API_URL;
  });

  afterAll(() => {
    process.env = originalEnv;
  });

  it('returns localhost default when EXPO_PUBLIC_API_URL is not set (AC2.2)', () => {
    delete process.env.EXPO_PUBLIC_API_URL;

    // Re-import to pick up the fresh env
    const { getApiBaseUrl: fresh } = require('../config');
    const url = fresh();

    expect(url).toMatch(/^http:\/\/localhost:/);
    expect(url).toContain('8000');
  });

  it('returns EXPO_PUBLIC_API_URL when it is set (AC2.1)', () => {
    process.env.EXPO_PUBLIC_API_URL = 'https://api.example.com';

    const { getApiBaseUrl: fresh } = require('../config');
    const url = fresh();

    expect(url).toBe('https://api.example.com');
  });

  it('strips trailing slash from configured URL', () => {
    process.env.EXPO_PUBLIC_API_URL = 'https://api.example.com/';

    const { getApiBaseUrl: fresh } = require('../config');
    const url = fresh();

    expect(url).toBe('https://api.example.com');
  });

  it('returns a string suitable for URL construction', () => {
    const url = getApiBaseUrl();

    // Should not have a trailing slash for clean URL joining
    expect(url).not.toMatch(/\/$/);
    // Should be a valid URL prefix
    expect(url).toMatch(/^https?:\/\/.+/);
  });
});

describe('getUxAuditScenario', () => {
  const originalEnv = process.env;
  const originalWindow = (globalThis as { window?: { location?: { search?: string } } }).window;

  beforeEach(() => {
    jest.resetModules();
    process.env = { ...originalEnv };
    delete process.env.EXPO_PUBLIC_UX_AUDIT_TARGET;
    (globalThis as { window?: { location?: { search?: string } } }).window = {
      location: { search: '' },
    };
  });

  afterAll(() => {
    process.env = originalEnv;
    if (typeof originalWindow === 'undefined') {
      delete (globalThis as { window?: { location?: { search?: string } } }).window;
      return;
    }

    (globalThis as { window?: { location?: { search?: string } } }).window = originalWindow;
  });

  it('returns null when audit-target runtime flag is disabled', () => {
    (globalThis as { window?: { location?: { search?: string } } }).window = {
      location: { search: '?uxAuditScenario=camera-permission-denied' },
    };

    const { getUxAuditScenario } = require('../config');
    expect(getUxAuditScenario()).toBeNull();
  });

  it('returns null when runtime flag is enabled but scenario is missing', () => {
    process.env.EXPO_PUBLIC_UX_AUDIT_TARGET = '1';

    const { getUxAuditScenario } = require('../config');
    expect(getUxAuditScenario()).toBeNull();
  });

  it('returns the camera denied scenario when query param is provided and runtime flag is enabled', () => {
    process.env.EXPO_PUBLIC_UX_AUDIT_TARGET = '1';
    (globalThis as { window?: { location?: { search?: string } } }).window = {
      location: { search: `?uxAuditScenario=${CAMERA_PERMISSION_DENIED_AUDIT_SCENARIO}` },
    };

    const { getUxAuditScenario } = require('../config');
    expect(getUxAuditScenario()).toBe(CAMERA_PERMISSION_DENIED_AUDIT_SCENARIO);
  });
});
