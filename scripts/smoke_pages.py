"""临时冒烟脚本：AppTest 逐页加载，报告异常"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from streamlit.testing.v1 import AppTest

pages = sorted((ROOT / "frontend" / "pages").glob("*.py"))
failed = 0
for p in pages:
    try:
        at = AppTest.from_file(str(p), default_timeout=45)
        at.run()
        n_err = len(at.exception)
        status = "OK " if n_err == 0 else "ERR"
        if n_err:
            failed += 1
        print(f"[{status}] {p.name}  exceptions={n_err}")
        for e in at.exception[:2]:
            print("   ", str(e.value)[:300].replace("\n", " | "))
    except Exception as e:
        failed += 1
        print(f"[FAIL] {p.name}: {str(e)[:300]}")

print(f"\n{len(pages)} pages, {failed} with exceptions")
sys.exit(1 if failed else 0)
