"""Settings read from environment / .env."""
import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")


def _default_ollama_url() -> str:
    # Reuse Ollama's own OLLAMA_HOST setting if present (e.g. "192.168.1.50:11434").
    host = os.getenv("OLLAMA_HOST", "127.0.0.1:11434")
    if host.startswith("0.0.0.0"):
        host = host.replace("0.0.0.0", "127.0.0.1", 1)
    return host if host.startswith("http") else f"http://{host}"


OLLAMA_URL = os.getenv("OLLAMA_URL") or _default_ollama_url()
CHAT_MODEL = os.getenv("CHAT_MODEL", "qwen3.5:latest")
CHAT_CTX = int(os.getenv("CHAT_CTX", "6144"))
KEEP_ALIVE = os.getenv("KEEP_ALIVE", "30m")
# Force how many model layers go on the GPU (99 = all). Needed on Intel Arc + Vulkan, where
# Ollama's own fit check puts the model on the CPU. Leave unset to let Ollama decide.
CHAT_NUM_GPU = int(os.getenv("CHAT_NUM_GPU")) if os.getenv("CHAT_NUM_GPU", "").strip() else None

GOOGLE_PLACES_API_KEY = os.getenv("GOOGLE_PLACES_API_KEY", "").strip()
PROVIDER = "google" if GOOGLE_PLACES_API_KEY else "osm"

SESSION_SECRET = os.getenv("SESSION_SECRET", "").strip()
DATA_DIR = (ROOT / os.getenv("DATA_DIR", "data")).resolve()
DB_PATH = DATA_DIR / "eat.db"

# Sent to OpenStreetMap services, which ask automated clients to identify themselves.
USER_AGENT = "EatPicker/1.0 (self-hosted home app)"
