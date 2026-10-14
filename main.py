"""打包入口：**顶层静态 import**，调用与 ``run.py`` 相同的 ``run()``。

import 必须写在顶层：打包工具的静态分析才收得全模块
（``run.py`` 那种"运行时改路径再 import"的开发写法收不全）。
"""


import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from deskpet import PROGRAM_NAME, __version__  # noqa: E402,F401
from deskpet.app import run  # noqa: E402
from deskpet.pet_window import PetWindow  # noqa: E402,F401

if __name__ == "__main__":
    sys.exit(run())
