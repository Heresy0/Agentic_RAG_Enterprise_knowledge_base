"""M2 PostgreSQL acceptance in a disposable, explicitly named local database."""
import json
import sys

from run_manufacturing_m1_tests import main


if __name__ == "__main__":
    try:
        raise SystemExit(main(phase="m2"))
    except Exception as exc:
        print(json.dumps(dict(status="failed", error_type=type(exc).__name__)), file=sys.stderr)
        raise SystemExit(1) from None
