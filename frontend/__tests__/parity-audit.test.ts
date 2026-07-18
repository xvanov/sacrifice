import { execSync } from 'child_process';
import path from 'path';

/**
 * Parity audit test (AC1.1, AC1.3).
 *
 * Runs the parity-audit.sh script against the frontend source tree.
 * The audit inventories web-only API usage in shared code paths and
 * fails on unguarded usage.
 */
describe('Parity audit', () => {
  const auditScript = path.resolve(__dirname, '../../scripts/parity-audit.sh');

  it('passes — no unguarded web-only API in shared code paths', () => {
    let result: { stdout: string; stderr: string; status: number };
    try {
      const stdout = execSync(`bash "${auditScript}"`, {
        cwd: path.resolve(__dirname, '../..'),
        encoding: 'utf-8',
        timeout: 30_000,
      });
      result = { stdout, stderr: '', status: 0 };
    } catch (e: any) {
      result = {
        stdout: e.stdout?.toString() || '',
        stderr: e.stderr?.toString() || '',
        status: e.status ?? 1,
      };
    }

    expect(result.status).toBe(0);
    expect(result.stdout).toContain('PASS');
  });
});