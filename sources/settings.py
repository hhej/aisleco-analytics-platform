"""Settings for the source systems, read from environment variables (dev defaults)."""
import os
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
LAKE_DIR = Path(os.environ.get("LAKE_DIR", REPO / "lake"))
LINE_CHANNEL_SECRET = os.environ.get("LINE_CHANNEL_SECRET", "dev-line-secret")
META_APP_SECRET = os.environ.get("META_APP_SECRET", "dev-meta-secret")
META_VERIFY_TOKEN = os.environ.get("META_VERIFY_TOKEN", "dev-verify-token")
INBOX_API_KEY = os.environ.get("INBOX_API_KEY", "dev-inbox-key")
GATEWAY_MODE = os.environ.get("GATEWAY_MODE", "production")  # "sim" honours X-Sim-Clock
