"""Configuration is loaded at application startup, never at module import."""
import os
import hashlib
import re
from functools import lru_cache
from dataclasses import dataclass
from pathlib import Path
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
MESH_BASE_URL = "https://api.meshapi.ai/v1"
POLICY_VERSION = "2"
LOCAL_HOSTS = ("127.0.0.1", "localhost", "[::1]", "testserver", "api")


@lru_cache(maxsize=1)
def pipeline_fingerprint():
    return hashlib.sha256(b"".join(p.read_bytes() for p in sorted((ROOT / "deepdive").glob("*.py")))).hexdigest()


@dataclass
class Settings:
    data: Path
    mesh_key: str = ""
    model: str = "openai/gpt-4o"
    api_token: str = ""
    input_tokens: int = 24000
    embedding_model: str = "openai/text-embedding-3-small"
    embedding_revision: str = "1"
    allowed_hosts: tuple[str, ...] = LOCAL_HOSTS
    output_tokens: int = 5000

    @classmethod
    def load(cls):
        load_dotenv(ROOT / ".env")
        path = Path(os.getenv("DEEPDIVE_DATA_DIR", "data"))
        token_file = os.getenv("DEEPDIVE_API_TOKEN_FILE")
        token = Path(token_file).read_text(encoding="utf-8").strip() if token_file else os.getenv("DEEPDIVE_API_TOKEN", "")
        hosts = LOCAL_HOSTS
        private_domain = os.getenv("RAILWAY_PRIVATE_DOMAIN", "")
        if private_domain:
            if not re.fullmatch(r"[a-z0-9-]+\.railway\.internal", private_domain):
                raise ValueError("Invalid RAILWAY_PRIVATE_DOMAIN")
            hosts += (private_domain, "healthcheck.railway.app")
        return cls(path if path.is_absolute() else ROOT / path,
                   os.getenv("MESH_API_KEY", ""), os.getenv("MESH_MODEL", "openai/gpt-4o"),
                   token, embedding_model=os.getenv("MESH_EMBEDDING_MODEL", "openai/text-embedding-3-small"),
                   embedding_revision=os.getenv("MESH_EMBEDDING_REVISION", "1"), allowed_hosts=hosts)
