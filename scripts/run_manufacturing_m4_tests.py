"""M4 checks use their own randomly named database, never the demo database."""
import json
import sys
from run_manufacturing_m1_tests import main

if __name__ == "__main__":
    try: raise SystemExit(main(phase="m4"))
    except Exception as exc:
        print(json.dumps(dict(status="failed", error_type=type(exc).__name__)), file=sys.stderr)
        raise SystemExit(1) from None
