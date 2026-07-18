import { execSync } from 'child_process';
import fs from 'fs';
import os from 'os';
import path from 'path';

/**
 * Parity audit test (AC1.1, AC1.3).
 *
 * Runs the parity-audit.sh script against the frontend source tree.
 * The audit inventories web-only API usage in shared code paths and
 * fails on unguarded usage.
 *
 * In addition to the real-scan smoke test, fixture-based tests inject
 * known violations and guarded cases into a temp tree to verify the
 * audit reports exact file:line/type entries and does not flag guarded
 * constructs.
 */

const auditScript = path.resolve(__dirname, '../../scripts/parity-audit.sh');

function runAudit(targetDir: string): { stdout: string; stderr: string; status: number } {
  try {
    const stdout = execSync(`bash "${auditScript}"`, {
      cwd: targetDir,
      encoding: 'utf-8',
      timeout: 30_000,
      env: { ...process.env, PARITY_AUDIT_DIR: targetDir },
    });
    return { stdout, stderr: '', status: 0 };
  } catch (e: any) {
    return {
      stdout: e.stdout?.toString() || '',
      stderr: e.stderr?.toString() || '',
      status: e.status ?? 1,
    };
  }
}

describe('Parity audit', () => {
  // ------------------------------------------------------------------
  // Smoke test: real tree
  // ------------------------------------------------------------------
  it('passes — no unguarded web-only API in shared code paths', () => {
    const result = runAudit(path.resolve(__dirname, '../..'));
    expect(result.status).toBe(0);
    expect(result.stdout).toContain('PASS');
  });

  // ------------------------------------------------------------------
  // Fixture tests: known violations and guarded cases
  // ------------------------------------------------------------------
  let tmpDir: string;

  beforeEach(() => {
    tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), 'parity-audit-test-'));
    // Mimic the expected directory layout so the audit scanner finds files.
    const compDir = path.join(tmpDir, 'components');
    const srvDir = path.join(tmpDir, 'services');
    fs.mkdirSync(compDir, { recursive: true });
    fs.mkdirSync(srvDir, { recursive: true });
  });

  afterEach(() => {
    fs.rmSync(tmpDir, { recursive: true, force: true });
  });

  function writeFile(relPath: string, content: string) {
    const full = path.join(tmpDir, relPath);
    fs.mkdirSync(path.dirname(full), { recursive: true });
    fs.writeFileSync(full, content, 'utf-8');
  }

  // -- helpers to assert violation lines ---------------------------------
  function parseViolations(stdout: string): Array<{ type: string; filepath: string; lineno: number }> {
    const lines = stdout.split('\n').filter((l) => l.match(/^\s+(document\.|window\.|localStorage)/));
    return lines.map((l) => {
      const m = l.match(/^\s+(document\.|window\.\s*\(unguarded\)|localStorage):\s+\.\/(.+):(\d+)/);
      if (!m) throw new Error(`Cannot parse violation line: ${l}`);
      return { type: m[1], filepath: m[2], lineno: parseInt(m[3], 10) };
    });
  }

  it('reports unguarded document. usage with file:line and type', () => {
    writeFile('components/UnguardedDoc.tsx', `
export function Bad() {
  const el = document.createElement('div');
  return null;
}
`);
    const result = runAudit(tmpDir);
    expect(result.status).toBe(1);
    expect(result.stdout).toContain('FAIL');
    const violations = parseViolations(result.stdout);
    expect(violations).toHaveLength(1);
    expect(violations[0]).toMatchObject({
      type: 'document.',
      filepath: 'components/UnguardedDoc.tsx',
      lineno: expect.any(Number),
    });
    expect(violations[0].lineno).toBeGreaterThan(0);
  });

  it('reports unguarded window. usage with file:line and type', () => {
    writeFile('services/WindowStuff.ts', `
export function doWindow() {
  window.location.href = '/foo';
}
`);
    const result = runAudit(tmpDir);
    expect(result.status).toBe(1);
    const violations = parseViolations(result.stdout);
    expect(violations).toHaveLength(1);
    expect(violations[0]).toMatchObject({
      type: 'window. (unguarded)',
      filepath: 'services/WindowStuff.ts',
    });
  });

  it('reports unguarded localStorage usage with file:line and type', () => {
    writeFile('services/Local.ts', `
export function save() {
  localStorage.setItem('k', 'v');
}
`);
    const result = runAudit(tmpDir);
    expect(result.status).toBe(1);
    const violations = parseViolations(result.stdout);
    expect(violations).toHaveLength(1);
    expect(violations[0]).toMatchObject({
      type: 'localStorage',
      filepath: 'services/Local.ts',
    });
  });

  it('reports multiple violations across files', () => {
    writeFile('components/A.tsx', `
export function A() { document.title = 'x'; }
`);
    writeFile('services/B.ts', `
export function B() { localStorage.setItem('k', 'v'); window.open('/'); }
`);
    const result = runAudit(tmpDir);
    expect(result.status).toBe(1);
    const violations = parseViolations(result.stdout);
    // document.title, localStorage.setItem, window.open = 3 violations
    expect(violations.length).toBeGreaterThanOrEqual(3);
    const types = violations.map((v) => v.type);
    expect(types).toContain('document.');
    expect(types).toContain('localStorage');
    expect(types).toContain('window. (unguarded)');
  });

  // -- guarded cases: must pass ------------------------------------------

  it('accepts same-line Platform.OS guard for document usage', () => {
    writeFile('components/SameLineGuard.tsx', `
import { Platform } from 'react-native';
export function Ok() {
  if (Platform.OS === 'web') { document.title = 'ok'; }
}
`);
    const result = runAudit(tmpDir);
    expect(result.status).toBe(0);
    expect(result.stdout).toContain('PASS');
  });

  it('accepts block-level Platform.OS guard within preceding 25 lines', () => {
    const lines: string[] = [
      "import { Platform } from 'react-native';",
      'export function Ok() {',
      "  if (Platform.OS === 'web') {",
    ];
    // Pad with empty lines then add violation
    for (let i = 0; i < 20; i++) lines.push('');
    lines.push("    localStorage.setItem('k', 'v');");
    lines.push('  }');
    lines.push('}');
    writeFile('services/BlockGuard.ts', lines.join('\n'));
    const result = runAudit(tmpDir);
    expect(result.status).toBe(0);
  });

  it('accepts early-return guard within preceding 40 lines', () => {
    const lines: string[] = [
      "import { Platform } from 'react-native';",
      'export function Ok() {',
      "  if (Platform.OS !== 'web') return;",
    ];
    // Pad then add window usage
    for (let i = 0; i < 30; i++) lines.push('');
    lines.push("  window.location.href = '/foo';");
    lines.push('}');
    writeFile('services/EarlyReturnGuard.ts', lines.join('\n'));
    const result = runAudit(tmpDir);
    expect(result.status).toBe(0);
  });

  it('accepts typeof window guard for window usage', () => {
    writeFile('services/TypeofGuard.ts', `
export function Ok() {
  if (typeof window !== 'undefined') { window.history.pushState({}, ''); }
}
`);
    const result = runAudit(tmpDir);
    expect(result.status).toBe(0);
  });
});