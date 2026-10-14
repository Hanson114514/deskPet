"""开发入口：把 ``src/`` 加进搜索路径，然后调用 ``deskpet.app.run()``。

与 ``main.py`` **不能合并**（agent.md §1）：
本文件是运行时改搜索路径的开发脚本；``main.py`` 的 import 写在顶层，
静态分析（打包）才收得全模块。两者指向**同一个** ``run()``。
"""

import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from deskpet.app import run  # noqa: E402  （必须在改完 sys.path 之后）

if __name__ == "__main__":
    sys.exit(run())
