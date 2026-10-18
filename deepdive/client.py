"""Small HTTP client used by both local interfaces."""
import os
import httpx
from .config import Settings


class Client:
    def __init__(self, url=None, entry_point="cli"):
        settings = Settings.load()
        self.url = (url or os.getenv("DEEPDIVE_API_URL", "http://127.0.0.1:8000")).rstrip("/")
        self.headers = {"Authorization": "Bearer " + settings.api_token} if settings.api_token else {}
        self.headers["X-DeepDive-Client"] = entry_point

    def request(self, method, path, **kwargs):
        with httpx.Client(base_url=self.url, headers=self.headers, timeout=20, trust_env=False) as client:
            response = client.request(method, path, **kwargs)
            if response.is_error:
                try:
                    detail = response.json().get("detail", response.text)
                except ValueError:
                    detail = response.text
                raise RuntimeError(f"API {response.status_code}: {detail}")
            return response.json()

    def download(self, run_id, artifact_id):
        with httpx.Client(base_url=self.url, headers=self.headers, timeout=30, trust_env=False) as client:
            response = client.get(f"/runs/{run_id}/artifacts/{artifact_id}")
            response.raise_for_status()
            return response.content

    def upload(self, filename, content, **scope):
        return self.request("POST", "/documents", params={"filename": filename, **scope}, content=content,
                            headers={"Content-Type": "application/octet-stream"}, timeout=60)
