"""Container liveness check. Does not assert feed entitlement or trading readiness."""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path


def healthy(path, now=None):
    try:
        data = json.loads(Path(path).read_text())
        at = datetime.fromisoformat(data['heartbeat'])
        now = now or datetime.now(timezone.utc)
        return at.tzinfo is not None and 0 <= (now-at).total_seconds() <= 10
    except (OSError, ValueError, KeyError, TypeError):
        return False


if __name__ == '__main__':
    sys.exit(0 if healthy(sys.argv[1]) else 1)
