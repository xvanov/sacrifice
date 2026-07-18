import json
import os
import re
import socket
import sys
import threading
import webbrowser
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer

import click

from cli.client import (
    APIClient,
    clear_token,
    get_base_url,
    get_token,
    get_user_info,
    save_token,
    save_user_info,
)


def _format_goal(g: dict) -> str:
    lines = [
        f"ID:        {g['id']}",
        f"Title:     {g['title']}",
        f"Type:      {g['goal_type']}",
        f"Status:    {g['status']}",
        f"Pledge:    ${g['pledge_amount'] / 100:.2f} {g['currency'].upper()}",
        f"Deadline:  {g['deadline']}",
        f"Created:   {g['created_at']}",
    ]
    if g.get("description"):
        lines.insert(2, f"Desc:      {g['description']}")
    if g.get("criteria"):
        lines.append(f"Criteria:  {json.dumps(g['criteria'], indent=2)}")
    return "\n".join(lines)


def _format_proof_status(r: dict) -> str:
    details = r.get("verification_details") or {}
    lines = [
        f"Submission ID:  {r['submission_id']}",
        f"Status:         {r['verification_status']}",
    ]
    if details:
        lines.append(f"Details:        {json.dumps(details, indent=2)}")
    return "\n".join(lines)


def _require_auth():
    if not get_token():
        click.echo("Not authenticated. Run 'sacrifice login' first.")
        sys.exit(1)


def _emit_json(data: dict | list, json_flag: bool):
    if json_flag:
        click.echo(json.dumps(data, indent=2, default=str))
        return True
    return False


@click.group()
@click.option("--api-url", envvar="SACRIFICE_API_URL", help="Backend API URL")
@click.pass_context
def cli(ctx, api_url):
    ctx.ensure_object(dict)
    ctx.obj["api_url"] = api_url


# ---------------------------------------------------------------------------
# Login
# ---------------------------------------------------------------------------

def _find_free_port(start: int = 9876) -> int:
    for port in range(start, start + 100):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            try:
                s.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    raise RuntimeError("Could not find a free port")


@cli.command()
@click.option("--provider", default="github", type=click.Choice(["google", "github"]),
              help="OAuth provider")
@click.option("--code", help="OAuth code (if already obtained)")
@click.option("--token", help="Google ID token (only for google provider)")
@click.pass_context
def login(ctx, provider, code, token):
    """Authenticate with the backend via OAuth."""
    if code or token:
        _login_with_code(ctx, provider, code or token)
        return

    base_url = ctx.obj.get("api_url") or get_base_url()
    port = _find_free_port()
    login_url = f"{base_url}/api/auth/cli/login/{provider}?port={port}"

    result = {"auth_code": None, "access_token": None}
    event = threading.Event()

    class CallbackHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path.startswith("/callback"):
                query = self.path.split("?", 1)[1] if "?" in self.path else ""
                params = dict(re.findall(r"([^&=]+)=([^&]*)", query))
                auth_code = params.get("auth_code", "")
                token_val = params.get("access_token", "")
                if auth_code or token_val:
                    result["auth_code"] = auth_code or None
                    result["access_token"] = token_val or None
                    event.set()
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html")
                    self.end_headers()
                    self.wfile.write(b"<html><body><h1>Authenticated!</h1><p>You can close this tab.</p></body></html>")
                else:
                    self.send_response(400)
                    self.end_headers()
                    self.wfile.write(b"Missing auth_code")
            else:
                self.send_response(404)
                self.end_headers()

        def log_message(self, fmt, *args):
            pass

    server = HTTPServer(("127.0.0.1", port), CallbackHandler)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()

    click.echo("=" * 60)
    click.echo("  SACRIFICE CLI LOGIN")
    click.echo("=" * 60)
    click.echo()
    click.echo(f"Opening browser to:\n  {login_url}")
    click.echo()
    click.echo("If the browser doesn't open, copy and paste the URL above.")
    click.echo()

    webbrowser.open(login_url)

    click.echo("Waiting for authentication...")
    event.wait(timeout=300)

    server.shutdown()

    auth_code = result["auth_code"]
    access_token = result["access_token"]
    if not auth_code and not access_token:
        click.echo("Authentication failed or timed out.", err=True)
        click.echo("Alternatively, you can pass --code or --token to this command.", err=True)
        sys.exit(1)

    client = APIClient(ctx.obj.get("api_url"))
    try:
        if auth_code:
            data = client.exchange_auth_code(auth_code)
            user_info = data["user"]
        else:
            save_token(access_token)
            client.token = access_token
            user_info = client.whoami()
            save_user_info(user_info)
        click.echo(f"\nLogged in as: {user_info['display_name']} ({user_info['email']})")
    except Exception:
        if access_token:
            click.echo("\nToken saved. Run 'sacrifice whoami' to verify.")
        else:
            click.echo("\nAuthentication succeeded but code exchange failed.", err=True)
            sys.exit(1)
    click.echo("Goals and data created via CLI will appear in your web account.")


def _login_with_code(ctx, provider, code_or_token):
    api_url = ctx.obj.get("api_url")
    client = APIClient(api_url)
    try:
        data = client.login(provider, code_or_token)
        click.echo(f"\nLogged in as: {data['user']['display_name']} ({data['user']['email']})")
    except ValueError as e:
        click.echo(f"Login failed: {e}", err=True)
        sys.exit(1)


# ---------------------------------------------------------------------------
# Dev token
# ---------------------------------------------------------------------------
@cli.command("dev-token")
@click.option("--email", default="dev@example.com", help="Dev user email")
@click.pass_context
def dev_token_cmd(ctx, email):
    """Get a dev JWT token (backend must be in debug mode)."""
    base_url = ctx.obj.get("api_url") or get_base_url()
    import httpx
    with httpx.Client(base_url=base_url) as client:
        resp = client.get(f"/api/auth/dev/token", params={"email": email})
        if resp.status_code != 200:
            click.echo(f"Failed to get dev token: {resp.text}", err=True)
            click.echo("Make sure the backend is running and settings.debug = True.", err=True)
            sys.exit(1)
        data = resp.json()
    save_token(data["access_token"])
    save_user_info(data["user"])
    click.echo(f"Dev token saved. Logged in as: {data['user']['display_name']} ({data['user']['email']})")


# ---------------------------------------------------------------------------
# Whoami / Logout
# ---------------------------------------------------------------------------
@cli.command()
def whoami():
    """Show current user info."""
    _require_auth()
    user = get_user_info()
    if user:
        click.echo(f"User:  {user.get('display_name', '?')}")
        click.echo(f"Email: {user.get('email', '?')}")
        click.echo(f"ID:    {user.get('id', '?')}")
    else:
        try:
            user = APIClient().whoami()
            click.echo(f"User:  {user.get('display_name', '?')}")
            click.echo(f"Email: {user.get('email', '?')}")
            click.echo(f"ID:    {user.get('id', '?')}")
        except Exception as e:
            click.echo(f"Error: {e}", err=True)
            sys.exit(1)


@cli.command()
@click.pass_context
def logout(ctx):
    """Clear stored credentials."""
    token = get_token()
    if token:
        try:
            APIClient(ctx.obj.get("api_url")).logout()
        except Exception as e:
            click.echo(f"Backend logout failed: {e}", err=True)
    clear_token()
    click.echo("Logged out.")


# ---------------------------------------------------------------------------
# Goals
# ---------------------------------------------------------------------------
@cli.group()
def goals():
    """Manage goals."""


@goals.command("list")
@click.option("--status", help="Filter by status (draft, active, pending_review, verified, failed, cancelled)")
@click.option("--json", "json_flag", is_flag=True, help="Output as JSON")
@click.pass_context
def list_goals(ctx, status, json_flag):
    """List all goals."""
    _require_auth()
    api_url = ctx.obj.get("api_url")
    client = APIClient(api_url)
    try:
        goals_list = client.list_goals(status)
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)

    if _emit_json(goals_list, json_flag):
        return

    if not goals_list:
        click.echo("No goals found.")
        return

    for g in goals_list:
        click.echo(_format_goal(g))
        click.echo("---")


@goals.command("show")
@click.argument("goal_id")
@click.option("--json", "json_flag", is_flag=True, help="Output as JSON")
@click.pass_context
def show_goal(ctx, goal_id, json_flag):
    """Show goal details."""
    _require_auth()
    api_url = ctx.obj.get("api_url")
    client = APIClient(api_url)
    try:
        g = client.get_goal(goal_id)
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)

    if _emit_json(g, json_flag):
        return
    click.echo(_format_goal(g))


@goals.command("activate")
@click.argument("goal_id")
@click.option("--json", "json_flag", is_flag=True, help="Output as JSON")
@click.pass_context
def activate_goal(ctx, goal_id, json_flag):
    """Activate a draft goal."""
    _require_auth()
    api_url = ctx.obj.get("api_url")
    client = APIClient(api_url)
    try:
        g = client.update_goal(goal_id, {"status": "active"})
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)

    if _emit_json(g, json_flag):
        return
    click.echo(f"Goal '{g['title']}' is now ACTIVE.")
    click.echo(f"Deadline: {g['deadline']}")


@goals.command("delete")
@click.argument("goal_id")
@click.pass_context
def delete_goal_cmd(ctx, goal_id):
    """Delete a draft goal."""
    _require_auth()
    api_url = ctx.obj.get("api_url")
    client = APIClient(api_url)
    try:
        client.delete_goal(goal_id)
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)

    click.echo("Goal deleted.")


@goals.command("submit-proof")
@click.argument("goal_id")
@click.option("--youtube-url", help="YouTube video URL (for youtube_video goals)")
@click.option("--url", help="API endpoint URL (for api_endpoint goals) or repo URL (for dev_sandbox/github_repo)")
@click.option("--method", default="GET", help="HTTP method (for api_endpoint)")
@click.option("--branch", default="main", help="Git branch (for dev_sandbox/github_repo)")
@click.option("--test-command", help="Test command (for dev_sandbox)")
@click.option("--language", help="Programming language (for dev_sandbox)")
@click.option("--github-token", help="GitHub personal access token (for github_repo)")
@click.option("--json", "json_flag", is_flag=True, help="Output as JSON")
@click.pass_context
def submit_proof(ctx, goal_id, youtube_url, url, method, branch, test_command, language, github_token, json_flag):
    """Submit proof for a goal."""
    _require_auth()
    api_url = ctx.obj.get("api_url")
    client = APIClient(api_url)

    proof_data = {}
    if youtube_url:
        proof_data["youtube_url"] = youtube_url
    if url:
        proof_data["url"] = url
        proof_data["repo_url"] = url
    if method:
        proof_data["method"] = method
    if branch:
        proof_data["branch"] = branch
    if test_command:
        proof_data["test_command"] = test_command
    if language:
        proof_data["language"] = language
    if github_token:
        proof_data["github_token"] = github_token

    try:
        result = client.submit_proof(goal_id, proof_data)
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)

    if _emit_json(result, json_flag):
        return
    click.echo("Proof submitted!")
    click.echo(f"Submission ID: {result['submission_id']}")
    click.echo(f"Status:        {result['verification_status']}")


@goals.command("verification-status")
@click.argument("goal_id")
@click.option("--json", "json_flag", is_flag=True, help="Output as JSON")
@click.pass_context
def verification_status(ctx, goal_id, json_flag):
    """Check verification status of the latest proof submission."""
    _require_auth()
    api_url = ctx.obj.get("api_url")
    client = APIClient(api_url)
    try:
        result = client.verification_status(goal_id)
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)

    if _emit_json(result, json_flag):
        return
    click.echo(_format_proof_status(result))


# ---------------------------------------------------------------------------
# Goals Create (subcommands per type)
# ---------------------------------------------------------------------------
@goals.group("create")
def create_goal():
    """Create a new goal."""


@create_goal.command("youtube")
@click.argument("title")
@click.option("--description", help="Goal description")
@click.option("--deadline", required=True, help="Deadline (ISO format, e.g. 2026-06-01T12:00:00Z)")
@click.option("--pledge-amount", required=True, type=int, help="Pledge amount in cents (e.g. 500 = $5)")
@click.option("--min-duration", required=True, type=int, help="Minimum video duration in seconds")
@click.option("--video-description", required=True, help="Description of what the video should cover")
@click.option("--charity-id", help="Stripe Connect charity ID")
@click.option("--timezone", default="UTC", help="IANA timezone")
@click.option("--recurrence", default="none", type=click.Choice(["none", "daily", "weekly", "monthly"]))
@click.option("--json", "json_flag", is_flag=True, help="Output as JSON")
@click.pass_context
def create_youtube(ctx, title, description, deadline, pledge_amount, min_duration,
                   video_description, charity_id, timezone, recurrence, json_flag):
    """Create a YouTube video goal."""
    _require_auth()
    api_url = ctx.obj.get("api_url")
    client = APIClient(api_url)

    data = {
        "title": title,
        "description": description,
        "deadline": deadline,
        "pledge_amount": pledge_amount,
        "goal_type": "youtube_video",
        "criteria": {
            "min_duration_seconds": min_duration,
            "video_description": video_description,
        },
        "charity_id": charity_id,
        "timezone": timezone,
        "recurrence": recurrence,
        "currency": "usd",
    }
    try:
        g = client.create_goal(data)
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)

    if _emit_json(g, json_flag):
        return
    click.echo("YouTube video goal created!")
    click.echo(_format_goal(g))
    click.echo(f"\nActivate it with: sacrifice goals activate {g['id']}")


@create_goal.command("api")
@click.argument("title")
@click.option("--description", help="Goal description")
@click.option("--deadline", required=True, help="Deadline (ISO format)")
@click.option("--pledge-amount", required=True, type=int, help="Pledge amount in cents")
@click.option("--url", required=True, help="API endpoint URL to check")
@click.option("--method", default="GET", help="HTTP method")
@click.option("--expected-status", type=int, default=200, help="Expected HTTP status code")
@click.option("--expected-body-schema", help="Expected JSON body schema (JSON string)")
@click.option("--charity-id", help="Stripe Connect charity ID")
@click.option("--timezone", default="UTC")
@click.option("--recurrence", default="none", type=click.Choice(["none", "daily", "weekly", "monthly"]))
@click.option("--json", "json_flag", is_flag=True, help="Output as JSON")
@click.pass_context
def create_api(ctx, title, description, deadline, pledge_amount, url, method,
               expected_status, expected_body_schema, charity_id, timezone, recurrence, json_flag):
    """Create an API endpoint goal."""
    _require_auth()
    api_url = ctx.obj.get("api_url")
    client = APIClient(api_url)

    criteria = {
        "url": url,
        "method": method,
        "expected_status": expected_status,
    }
    if expected_body_schema:
        try:
            criteria["expected_body_schema"] = json.loads(expected_body_schema)
        except json.JSONDecodeError as e:
            click.echo(f"Invalid JSON for expected-body-schema: {e}", err=True)
            sys.exit(1)

    data = {
        "title": title,
        "description": description,
        "deadline": deadline,
        "pledge_amount": pledge_amount,
        "goal_type": "api_endpoint",
        "criteria": criteria,
        "charity_id": charity_id,
        "timezone": timezone,
        "recurrence": recurrence,
        "currency": "usd",
    }
    try:
        g = client.create_goal(data)
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)

    if _emit_json(g, json_flag):
        return
    click.echo("API endpoint goal created!")
    click.echo(_format_goal(g))
    click.echo(f"\nActivate it with: sacrifice goals activate {g['id']}")


@create_goal.command("sandbox")
@click.argument("title")
@click.option("--description", help="Goal description")
@click.option("--deadline", required=True, help="Deadline (ISO format)")
@click.option("--pledge-amount", required=True, type=int, help="Pledge amount in cents")
@click.option("--repo-url", required=True, help="Git repository URL")
@click.option("--branch", default="main", help="Git branch")
@click.option("--test-command", default="python -m pytest -v", help="Test command")
@click.option("--language", default="python", help="Programming language")
@click.option("--goal-description", help="Description of the expected implementation")
@click.option("--charity-id", help="Stripe Connect charity ID")
@click.option("--timezone", default="UTC")
@click.option("--recurrence", default="none", type=click.Choice(["none", "daily", "weekly", "monthly"]))
@click.option("--json", "json_flag", is_flag=True, help="Output as JSON")
@click.pass_context
def create_sandbox(ctx, title, description, deadline, pledge_amount, repo_url, branch,
                   test_command, language, goal_description, charity_id, timezone, recurrence, json_flag):
    """Create a dev sandbox (code test) goal."""
    _require_auth()
    api_url = ctx.obj.get("api_url")
    client = APIClient(api_url)

    criteria = {
        "repo_url": repo_url,
        "branch": branch,
        "test_command": test_command,
        "language": language,
        "goal_description": goal_description or description or title,
    }
    data = {
        "title": title,
        "description": description,
        "deadline": deadline,
        "pledge_amount": pledge_amount,
        "goal_type": "dev_sandbox",
        "criteria": criteria,
        "charity_id": charity_id,
        "timezone": timezone,
        "recurrence": recurrence,
        "currency": "usd",
    }
    try:
        g = client.create_goal(data)
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)

    if _emit_json(g, json_flag):
        return
    click.echo("Dev sandbox goal created!")
    click.echo(_format_goal(g))
    click.echo(f"\nActivate it with: sacrifice goals activate {g['id']}")


@create_goal.command("github")
@click.argument("title")
@click.option("--description", help="Goal description")
@click.option("--deadline", required=True, help="Deadline (ISO format)")
@click.option("--pledge-amount", required=True, type=int, help="Pledge amount in cents")
@click.option("--repo-url", required=True, help="GitHub repository URL (e.g. https://github.com/owner/repo)")
@click.option("--branch", default="main", help="Git branch to check")
@click.option("--condition", multiple=True,
              help=("Conditions in JSON format. "
                    'Examples:\n'
                    '  --condition \'{"type":"commits","min_count":10,"since_date":"2026-05-01T00:00:00Z"}\'\n'
                    '  --condition \'{"type":"lines_changed","min_count":500,"since_date":"2026-05-01T00:00:00Z"}\'\n'
                    '  --condition \'{"type":"tickets_closed","tickets":["https://github.com/owner/repo/issues/1"]}\''))
@click.option("--github-token", help="GitHub personal access token (for private repos or higher rate limits)")
@click.option("--charity-id", help="Stripe Connect charity ID")
@click.option("--timezone", default="UTC")
@click.option("--recurrence", default="none", type=click.Choice(["none", "daily", "weekly", "monthly"]))
@click.option("--json", "json_flag", is_flag=True, help="Output as JSON")
@click.pass_context
def create_github(ctx, title, description, deadline, pledge_amount, repo_url, branch,
                  condition, github_token, charity_id, timezone, recurrence, json_flag):
    """Create a GitHub repo verification goal with auto-checkable conditions."""
    _require_auth()
    api_url = ctx.obj.get("api_url")
    client = APIClient(api_url)

    conditions = []
    for c in condition:
        try:
            parsed = json.loads(c)
            conditions.append(parsed)
        except json.JSONDecodeError as e:
            click.echo(f"Invalid condition JSON: {e}", err=True)
            sys.exit(1)

    if not conditions:
        click.echo("At least one --condition is required.", err=True)
        sys.exit(1)

    criteria = {
        "repo_url": repo_url,
        "branch": branch,
        "conditions": conditions,
    }
    if github_token:
        criteria["github_token"] = github_token

    data = {
        "title": title,
        "description": description,
        "deadline": deadline,
        "pledge_amount": pledge_amount,
        "goal_type": "github_repo",
        "criteria": criteria,
        "charity_id": charity_id,
        "timezone": timezone,
        "recurrence": recurrence,
        "currency": "usd",
    }
    try:
        g = client.create_goal(data)
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)

    if _emit_json(g, json_flag):
        return
    click.echo("GitHub repo goal created!")
    click.echo(_format_goal(g))
    click.echo(f"\nActivate it with: sacrifice goals activate {g['id']}")


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------
@cli.group()
def dashboard():
    """Dashboard stats and history."""


@dashboard.command("stats")
@click.option("--json", "json_flag", is_flag=True, help="Output as JSON")
@click.pass_context
def dashboard_stats(ctx, json_flag):
    """Show dashboard statistics."""
    _require_auth()
    api_url = ctx.obj.get("api_url")
    client = APIClient(api_url)
    try:
        stats = client.dashboard_stats()
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)

    if _emit_json(stats, json_flag):
        return
    click.echo("Dashboard Stats")
    click.echo("=" * 40)
    click.echo(f"Total goals:    {stats['total_goals']}")
    click.echo(f"Completed:      {stats['completed_count']}")
    click.echo(f"Failed:         {stats['failed_count']}")
    click.echo(f"Success rate:   {stats['success_rate']}%")
    click.echo(f"Total pledged:  ${stats['total_pledged'] / 100:.2f}")
    click.echo(f"Total donated:  ${stats['total_donated'] / 100:.2f}")
    click.echo(f"Total saved:    ${stats['total_saved'] / 100:.2f}")


@dashboard.command("history")
@click.option("--json", "json_flag", is_flag=True, help="Output as JSON")
@click.pass_context
def dashboard_history(ctx, json_flag):
    """Show goal history."""
    _require_auth()
    api_url = ctx.obj.get("api_url")
    client = APIClient(api_url)
    try:
        history = client.dashboard_history()
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)

    if _emit_json(history, json_flag):
        return
    if not history:
        click.echo("No history found.")
        return

    for item in history:
        click.echo(
            f"{item['created_at'][:10]}  "
            f"{item['status']:15s}  "
            f"${item['pledge_amount'] / 100:>6.2f}  "
            f"{item['goal_type']:15s}  "
            f"{item['title']}"
        )


# ---------------------------------------------------------------------------
# Notifications
# ---------------------------------------------------------------------------
@cli.group()
def notifications():
    """Manage notifications."""


@notifications.command("list")
@click.option("--limit", default=20, type=int, help="Max notifications")
@click.option("--offset", default=0, type=int, help="Offset")
@click.option("--json", "json_flag", is_flag=True, help="Output as JSON")
@click.pass_context
def list_notifications(ctx, limit, offset, json_flag):
    """List notifications."""
    _require_auth()
    api_url = ctx.obj.get("api_url")
    client = APIClient(api_url)
    try:
        notifs = client.list_notifications(limit, offset)
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)

    if _emit_json(notifs, json_flag):
        return
    if not notifs:
        click.echo("No notifications.")
        return

    for n in notifs:
        status = "READ" if n["read"] else "NEW "
        click.echo(f"[{status}] {n['created_at'][:19]} {n['title']}")
        click.echo(f"       ID: {n['id']}")
        if n["body"]:
            click.echo(f"       {n['body']}")
        click.echo()


@notifications.command("unread")
@click.option("--json", "json_flag", is_flag=True, help="Output as JSON")
@click.pass_context
def unread_count(ctx, json_flag):
    """Show unread notification count."""
    _require_auth()
    api_url = ctx.obj.get("api_url")
    client = APIClient(api_url)
    try:
        count = client.unread_count()
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)

    if json_flag:
        click.echo(json.dumps({"unread_count": count}))
        return
    click.echo(f"Unread notifications: {count}")


@notifications.command("read")
@click.argument("notification_id")
@click.pass_context
def mark_read(ctx, notification_id):
    """Mark a notification as read."""
    _require_auth()
    api_url = ctx.obj.get("api_url")
    client = APIClient(api_url)
    try:
        client.mark_read(notification_id)
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)

    click.echo("Marked as read.")


@notifications.command("read-all")
@click.pass_context
def mark_all_read(ctx):
    """Mark all notifications as read."""
    _require_auth()
    api_url = ctx.obj.get("api_url")
    client = APIClient(api_url)
    try:
        client.mark_all_read()
    except Exception as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)

    click.echo("All notifications marked as read.")


if __name__ == "__main__":
    cli()
