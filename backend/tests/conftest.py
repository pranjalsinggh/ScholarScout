"""Keep the test run's files and caches out of the live application data."""
import os
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
os.environ["DATABASE_URL"] = f"sqlite:///{(BACKEND / 'data' / 'test_scholarscout.db').as_posix()}"
os.environ["SCHOLARSCOUT_DATA_DIR"] = str(BACKEND / "data" / "test-runtime")
os.environ["REMOTE_EMBEDDINGS"] = "0"
