from pathlib import Path
import sys


marker = Path(sys.argv[1])
if marker.exists():
    print(7)
else:
    marker.write_text("seen\n", encoding="utf-8")
    raise SystemExit(1)
