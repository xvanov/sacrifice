import json
import os
from pathlib import Path

import httpx

CONFIG_DIR = Path.home() / ".config" / "sacrifice"
CONFIG_FILE = CONFIG_DIR / "config.json"

DEFAULT_BASE_URL = "http://localhost:8000"


def _load_config() -> dict:
    if CONFIG_FILE.exists():
        try:
            return json.loads(CONFIG_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def _save_config(config: dict):
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(config, indent=2))


def get_token() -> str | None:
    return _load_config().get("access_token")


def get_refresh_token() -> str | None:
    return _load_config().get("refresh_token")


def get_base_url() -> str:
    return os.environ.get("SACRIFICE_API_URL", _load_config().get("base_url", DEFAULT_BASE_URL))


def save_tokens(access_token: str, refresh_token: str | None = None):
    config = _load_config()
    config["access_token"] = access_token
    if refresh_token:
        config["refresh_token"] = refresh_token
    else:
        config.pop("refresh_token", None)
    _save_config(config)


def save_token(token: str):
    save_tokens(token, get_refresh_token())


def save_user_info(user: dict):
    config = _load_config()
    config["user"] = user
    _save_config(config)


def get_user_info() -> dict | None:
    return _load_config().get("user")


def clear_token():
    config = _load_config()
    config.pop("access_token", None)
    config.pop("refresh_token", None)
    config.pop("user", None)
    _save_config(config)


class APIClient:
    def __init__(self, base_url: str | None = None):
        self.base_url = (base_url or get_base_url()).rstrip("/")
        self.token = get_token()
        self.refresh_token = get_refresh_token()

    def _headers(self, auth_token: str | None = None, extra_headers: dict | None = None) -> dict:
        headers = {"Content-Type": "application/json"}
        if auth_token:
            headers["Authorization"] = f"Bearer {auth_token}"
        if extra_headers:
            headers.update(extra_headers)
        return headers

    def _perform_request(
        self,
        method: str,
        path: str,
        *,
        auth_token: str | None,
        **kwargs,
    ) -> httpx.Response:
        url = f"{self.base_url}{path}"
        headers = self._headers(auth_token=auth_token, extra_headers=kwargs.pop("headers", None))
        with httpx.Client(timeout=30.0) as client:
            return client.request(method, url, headers=headers, **kwargs)

    def _refresh_session(self) -> bool:
        if not self.refresh_token:
            return False
        resp = self._perform_request(
            "POST",
            "/api/auth/refresh",
            auth_token=None,
            json={"refresh_token": self.refresh_token},
        )
        if resp.status_code != 200:
            clear_token()
            self.token = None
            self.refresh_token = None
            return False
        data = resp.json()
        self.token = data["access_token"]
        self.refresh_token = data.get("refresh_token")
        save_tokens(self.token, self.refresh_token)
        return True

    def _request(self, method: str, path: str, retry_on_unauthorized: bool = True, **kwargs) -> httpx.Response:
        resp = self._perform_request(method, path, auth_token=self.token, **kwargs)
        if resp.status_code == 401 and retry_on_unauthorized and path != "/api/auth/refresh":
            if self._refresh_session():
                return self._perform_request(method, path, auth_token=self.token, **kwargs)
        return resp

    def login(self, provider: str, code_or_token: str) -> dict:
        if provider == "google":
            resp = self._request("POST", "/api/auth/google", json={"token": code_or_token}, retry_on_unauthorized=False)
        elif provider == "github":
            resp = self._request("POST", "/api/auth/github", json={"code": code_or_token}, retry_on_unauthorized=False)
        else:
            raise ValueError(f"Unknown provider: {provider}")
        if resp.status_code != 200:
            raise ValueError(f"Login failed: {resp.text}")
        data = resp.json()
        save_tokens(data["access_token"], data.get("refresh_token"))
        save_user_info(data["user"])
        self.token = data["access_token"]
        self.refresh_token = data.get("refresh_token")
        return data

    def whoami(self) -> dict:
        resp = self._request("GET", "/api/auth/me")
        if resp.status_code == 401:
            raise PermissionError("Not authenticated. Run 'sacrifice login' first.")
        resp.raise_for_status()
        return resp.json()

    def list_goals(self, status: str | None = None) -> list:
        params = {}
        if status:
            params["status"] = status
        resp = self._request("GET", "/api/goals", params=params)
        if resp.status_code == 401:
            raise PermissionError("Not authenticated. Run 'sacrifice login' first.")
        resp.raise_for_status()
        return resp.json()

    def get_goal(self, goal_id: str) -> dict:
        resp = self._request("GET", f"/api/goals/{goal_id}")
        if resp.status_code == 401:
            raise PermissionError("Not authenticated. Run 'sacrifice login' first.")
        resp.raise_for_status()
        return resp.json()

    def create_goal(self, data: dict) -> dict:
        resp = self._request("POST", "/api/goals", json=data)
        if resp.status_code == 401:
            raise PermissionError("Not authenticated. Run 'sacrifice login' first.")
        resp.raise_for_status()
        return resp.json()

    def update_goal(self, goal_id: str, data: dict) -> dict:
        resp = self._request("PUT", f"/api/goals/{goal_id}", json=data)
        if resp.status_code == 401:
            raise PermissionError("Not authenticated. Run 'sacrifice login' first.")
        resp.raise_for_status()
        return resp.json()

    def delete_goal(self, goal_id: str):
        resp = self._request("DELETE", f"/api/goals/{goal_id}")
        if resp.status_code == 401:
            raise PermissionError("Not authenticated. Run 'sacrifice login' first.")
        resp.raise_for_status()

    def submit_proof(self, goal_id: str, proof_data: dict) -> dict:
        resp = self._request("POST", f"/api/goals/{goal_id}/submit-proof", json=proof_data)
        if resp.status_code == 401:
            raise PermissionError("Not authenticated. Run 'sacrifice login' first.")
        resp.raise_for_status()
        return resp.json()

    def verification_status(self, goal_id: str) -> dict:
        resp = self._request("GET", f"/api/goals/{goal_id}/verification-status")
        if resp.status_code == 401:
            raise PermissionError("Not authenticated. Run 'sacrifice login' first.")
        resp.raise_for_status()
        return resp.json()

    def dashboard_stats(self) -> dict:
        resp = self._request("GET", "/api/dashboard/stats")
        if resp.status_code == 401:
            raise PermissionError("Not authenticated. Run 'sacrifice login' first.")
        resp.raise_for_status()
        return resp.json()

    def dashboard_history(self) -> list:
        resp = self._request("GET", "/api/dashboard/history")
        if resp.status_code == 401:
            raise PermissionError("Not authenticated. Run 'sacrifice login' first.")
        resp.raise_for_status()
        return resp.json()

    def list_notifications(self, limit: int = 20, offset: int = 0) -> list:
        resp = self._request("GET", "/api/notifications", params={"limit": limit, "offset": offset})
        if resp.status_code == 401:
            raise PermissionError("Not authenticated. Run 'sacrifice login' first.")
        resp.raise_for_status()
        return resp.json()

    def unread_count(self) -> int:
        resp = self._request("GET", "/api/notifications/unread-count")
        if resp.status_code == 401:
            raise PermissionError("Not authenticated. Run 'sacrifice login' first.")
        resp.raise_for_status()
        return resp.json()["unread_count"]

    def mark_read(self, notification_id: str):
        resp = self._request("PUT", f"/api/notifications/{notification_id}/read")
        if resp.status_code == 401:
            raise PermissionError("Not authenticated. Run 'sacrifice login' first.")
        resp.raise_for_status()

    def mark_all_read(self):
        resp = self._request("PUT", "/api/notifications/read-all")
        if resp.status_code == 401:
            raise PermissionError("Not authenticated. Run 'sacrifice login' first.")
        resp.raise_for_status()
