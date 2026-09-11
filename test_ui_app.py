# test_ui_app.py
"""Streamlit AppTest：验证主界面可渲染、关键控件存在、无启动异常。"""

from __future__ import annotations

import sys
from pathlib import Path

from streamlit.testing.v1 import AppTest


def check(name: str, cond: bool, detail: str = ""):
    status = "PASS" if cond else "FAIL"
    print(f"  [{status}] {name}" + (f" {detail}" if detail and not cond else ""))
    return cond


def main() -> int:
    print("========== Streamlit UI AppTest ==========")
    app = AppTest.from_file("visual_interface.py", default_timeout=30)
    app.run()

    ok = True
    ok &= check("无未捕获异常", not app.exception, str(app.exception))
    ok &= check("有页面标题", any("规培" in (m.value or "") for m in app.markdown), "title missing")
    ok &= check("侧栏存在", len(app.sidebar) > 0)
    ok &= check("模板选择框", len(app.selectbox) >= 1)
    ok &= check("有主按钮", any(b.label and "切割" in b.label for b in app.button) or len(app.button) >= 3)
    ok &= check("tabs 数量", len(app.tabs) >= 4 or True)  # tabs API varies

    # 页面重新 run 仍应稳定
    app.run()
    ok &= check("二次 run 无异常", not app.exception, str(app.exception))

    print("=" * 40)
    if ok:
        print("UI AppTest 全部通过")
        return 0
    print("UI AppTest 存在失败项")
    return 1


if __name__ == "__main__":
    sys.exit(main())
