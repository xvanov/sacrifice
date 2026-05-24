#!/usr/bin/env python3
"""
Sacrifice CLI E2E Test

Tests the full CLI workflow end-to-end using the sacrifice CLI tool.

Prerequisites:
  1. Backend running at SACRIFICE_API_URL (default http://localhost:8000)
  2. Redis, PostgreSQL, Celery worker running
  3. A valid JWT token (set SACRIFICE_TOKEN or pass --token)

Usage:
  # Set an existing token (from a previous login)
  export SACRIFICE_TOKEN="eyJ..."
  python e2e_test.py

  # Or with a token argument
  python e2e_test.py --token "eyJ..."

  # Custom API URL
  python e2e_test.py --api-url http://localhost:8000
"""

import json
import os
import subprocess
import sys
import time
import argparse

SACRIFICE_CMD = os.environ.get("SACRIFICE_CMD", "sacrifice")
POLL_INTERVAL = 3  # seconds between verification status polls
MAX_POLLS = 30     # max polling iterations (90 seconds)


def run(args, check=True, timeout=30):
    cmd = [SACRIFICE_CMD] + args
    env = os.environ.copy()
    if "SACRIFICE_API_URL" in env:
        pass
    if "SACRIFICE_TOKEN" in env:
        env["SACRIFICE_TOKEN"] = env["SACRIFICE_TOKEN"]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=env)
    if check and result.returncode != 0:
        print(f"  FAILED: {' '.join(cmd)}")
        print(f"  stderr: {result.stderr.strip()}")
        print(f"  stdout: {result.stdout.strip()}")
        sys.exit(1)
    return result


def run_json(args, check=True, timeout=30):
    """Run a CLI command with --json and parse the output."""
    result = run(args + ["--json"], check=check, timeout=timeout)
    if result.returncode != 0:
        return None
    try:
        return json.loads(result.stdout.strip())
    except json.JSONDecodeError as e:
        print(f"  JSON parse error: {e}")
        print(f"  stdout: {result.stdout.strip()}")
        if check:
            sys.exit(1)
        return None


def poll_verification(goal_id):
    """Poll verification-status until verified or failed."""
    for i in range(MAX_POLLS):
        status_data = run_json(["goals", "verification-status", goal_id])
        if status_data is None:
            print(f"  Could not get verification status (attempt {i+1})")
            time.sleep(POLL_INTERVAL)
            continue
        vs = status_data.get("verification_status", "unknown")
        print(f"  Poll {i+1}: verification_status={vs}")
        if vs in ("verified", "failed"):
            return status_data
        time.sleep(POLL_INTERVAL)
    print("  Timed out waiting for verification.")
    return None


def section(title):
    print()
    print("=" * 70)
    print(f"  {title}")
    print("=" * 70)


def test_1_whoami():
    section("1. Authentication - whoami")
    result = run(["whoami"])
    print(f"  {result.stdout.strip()}")
    return True


def test_2_create_passing_api_goal():
    section("2. Create a PASSING API endpoint goal")
    g = run_json([
        "goals", "create", "api",
        "E2E Test - Passing API Goal",
        "--deadline", "2026-12-31T23:59:00Z",
        "--pledge-amount", "100",
        "--url", "https://api.github.com",
        "--method", "GET",
        "--expected-status", "200",
    ])
    if g:
        print(f"  Goal created: {g['id']}")
        print(f"  Title: {g['title']}")
        print(f"  Status: {g['status']}")
    return g["id"] if g else None


def test_3_activate_goal(goal_id):
    section(f"3. Activate goal {goal_id}")
    g = run_json(["goals", "activate", goal_id])
    if g:
        print(f"  Status: {g['status']} (expected: active)")
        assert g["status"] == "active", f"Expected active, got {g['status']}"
        print("  PASS: Goal is active")
        return True
    return False


def test_4_submit_proof(goal_id, proof_url="https://api.github.com"):
    section(f"4. Submit proof for goal {goal_id}")
    result = run_json([
        "goals", "submit-proof", goal_id,
        "--url", proof_url,
        "--method", "GET",
    ])
    if result:
        print(f"  Submission ID: {result.get('submission_id')}")
        print(f"  Status: {result.get('verification_status')} (expected: pending)")
        assert result["verification_status"] == "pending"
        print(f"  PASS: Proof submitted (URL: {proof_url}), status is pending")
        return True
    return False


def test_5_poll_verification(goal_id, expected="verified"):
    section(f"5. Poll verification for goal {goal_id} (expected: {expected})")
    status_data = poll_verification(goal_id)
    if status_data:
        vs = status_data.get("verification_status")
        print(f"  Final status: {vs}")
        details = status_data.get("verification_details", {})
        if details:
            print(f"  Details: {json.dumps(details, indent=4)}")
        if vs == expected:
            print(f"  PASS: Goal is {expected} as expected")
            return True
        else:
            print(f"  FAIL: Expected {expected}, got {vs}")
            return False
    return False


def test_6_create_failing_api_goal():
    section("6. Create a FAILING API endpoint goal (bad URL)")
    g = run_json([
        "goals", "create", "api",
        "E2E Test - Failing API Goal",
        "--deadline", "2026-12-31T23:59:00Z",
        "--pledge-amount", "200",
        "--url", "https://api.github.com/nonexistent-endpoint-12345",
        "--method", "GET",
        "--expected-status", "200",
    ])
    if g:
        print(f"  Goal created: {g['id']}")
        print(f"  Title: {g['title']}")
        print(f"  Status: {g['status']}")
    return g["id"] if g else None


def test_7_dashboard_stats():
    section("7. Dashboard stats")
    stats = run_json(["dashboard", "stats"])
    if stats:
        print(f"  Total goals:  {stats['total_goals']}")
        print(f"  Completed:    {stats['completed_count']}")
        print(f"  Failed:       {stats['failed_count']}")
        print(f"  Success rate: {stats['success_rate']}%")
        assert stats["total_goals"] >= 2, "Should have at least 2 goals"
        print("  PASS: Dashboard stats OK")
        return True
    return False


def test_8_dashboard_history():
    section("8. Dashboard history")
    history = run_json(["dashboard", "history"])
    if history is not None:
        print(f"  Found {len(history)} goals in history")
        for item in history:
            print(f"    [{item['status']:15s}] {item['title']} (${item['pledge_amount']/100:.2f})")
        return True
    return False


def test_9_notifications():
    section("9. Notifications")
    notifs = run_json(["notifications", "list"])
    if notifs is not None:
        print(f"  Found {len(notifs)} notifications")
        unread_data = run_json(["notifications", "unread"])
        unread_count = unread_data.get("unread_count", 0) if unread_data else 0
        print(f"  Unread count: {unread_count}")
        # Mark all as read
        run(["notifications", "read-all"])
        unread2_data = run_json(["notifications", "unread"])
        unread2 = unread2_data.get("unread_count", -1) if unread2_data else -1
        print(f"  Unread after read-all: {unread2}")
        assert unread2 == 0, f"Expected 0 unread after read-all, got {unread2}"
        print("  PASS: Notifications OK")
        return True
    return False


def test_10_show_goals():
    section("10. List all goals")
    goals = run_json(["goals", "list"])
    if goals is not None:
        print(f"  Found {len(goals)} goals:")
        for g in goals:
            print(f"    [{g['status']:15s}] {g['title']} ({g['goal_type']})")
        return True
    return False


def main():
    parser = argparse.ArgumentParser(description="Sacrifice CLI E2E Test")
    parser.add_argument("--api-url", default=os.environ.get("SACRIFICE_API_URL", "http://localhost:8000"),
                        help="Backend API URL")
    parser.add_argument("--token", default=os.environ.get("SACRIFICE_TOKEN", ""),
                        help="JWT access token (if not set, uses stored token from `sacrifice login`)")
    parser.add_argument("--venv", default=os.environ.get("SACRIFICE_VENV", ""),
                        help="Path to backend virtualenv (auto-finds sacrifice binary)")
    args = parser.parse_args()

    if args.venv:
        bin_dir = os.path.join(args.venv, "bin")
        sacrifice_cmd = os.path.join(bin_dir, "sacrifice")
        if os.path.exists(sacrifice_cmd):
            os.environ.setdefault("SACRIFICE_CMD", sacrifice_cmd)
            os.environ["PATH"] = f"{bin_dir}:{os.environ['PATH']}"
        else:
            print(f"Warning: {sacrifice_cmd} not found, falling back to PATH")

    os.environ["SACRIFICE_API_URL"] = args.api_url
    if args.token:
        # Write token to config file so the CLI can use it
        from cli.client import save_token
        save_token(args.token)
        print(f"Token saved from --token argument.")

    print(f"Sacrifice API URL: {args.api_url}")
    print()

    # Verify CLI is available
    result = subprocess.run([SACRIFICE_CMD, "--help"], capture_output=True, text=True)
    if result.returncode != 0:
        print(f"Error: '{SACRIFICE_CMD}' not found. Install the CLI package first.")
        print("  cd backend && pip install -e .")
        sys.exit(1)
    print(f"CLI found: {SACRIFICE_CMD}")

    passing_goal_id = None
    failing_goal_id = None

    try:
        # 1. Whoami (verify auth)
        if not test_1_whoami():
            print("FATAL: Not authenticated. Run `sacrifice login` or pass --token.")
            sys.exit(1)

        # 2. Create passing goal
        passing_goal_id = test_2_create_passing_api_goal()
        if not passing_goal_id:
            print("FATAL: Failed to create passing goal.")
            sys.exit(1)

        # 3. Activate passing goal
        assert test_3_activate_goal(passing_goal_id)

        # 4. Submit proof for passing goal
        assert test_4_submit_proof(passing_goal_id)

        # 5. Poll for verification (should pass)
        if test_5_poll_verification(passing_goal_id, expected="verified"):
            print("\n  >>> PASSING GOAL: VERIFIED <<<")
        else:
            print("\n  >>> PASSING GOAL: UNEXPECTED RESULT <<<")

        # 6. Create failing goal
        failing_goal_id = test_6_create_failing_api_goal()
        if not failing_goal_id:
            print("FATAL: Failed to create failing goal.")
            sys.exit(1)

        # 3b. Activate failing goal
        assert test_3_activate_goal(failing_goal_id)

        # 4b. Submit proof for failing goal (with the nonexistent URL)
        assert test_4_submit_proof(failing_goal_id, proof_url="https://api.github.com/nonexistent-endpoint-12345")

        # 5b. Poll for verification (should fail)
        if test_5_poll_verification(failing_goal_id, expected="failed"):
            print("\n  >>> FAILING GOAL: CORRECTLY FAILED <<<")
        else:
            print("\n  >>> FAILING GOAL: UNEXPECTED RESULT <<<")

        # 7. Dashboard stats
        test_7_dashboard_stats()

        # 8. Dashboard history
        test_8_dashboard_history()

        # 9. Notifications
        test_9_notifications()

        # 10. List all goals
        test_10_show_goals()

    finally:
        # Cleanup: delete draft goals if any remain
        pass

    print()
    print("=" * 70)
    print("  E2E TEST RESULTS")
    print("=" * 70)

    goal_data_1 = run_json(["goals", "show", passing_goal_id]) if passing_goal_id else None
    goal_data_2 = run_json(["goals", "show", failing_goal_id]) if failing_goal_id else None

    passing_status = goal_data_1.get("status") if goal_data_1 else "unknown"
    failing_status = goal_data_2.get("status") if goal_data_2 else "unknown"

    print(f"  Passing goal ({passing_goal_id}): {passing_status}")
    print(f"  Failing goal ({failing_goal_id}): {failing_status}")
    print()

    all_pass = True

    if passing_status == "verified":
        print("  [PASS] Passing goal was verified successfully")
    else:
        print(f"  [FAIL] Passing goal status is '{passing_status}', expected 'verified'")
        all_pass = False

    if failing_status == "failed":
        print("  [PASS] Failing goal correctly failed verification")
    else:
        print(f"  [FAIL] Failing goal status is '{failing_status}', expected 'failed'")
        all_pass = False

    print()
    if all_pass:
        print("  ALL TESTS PASSED")
    else:
        print("  SOME TESTS FAILED")
        sys.exit(1)


if __name__ == "__main__":
    main()
