"""deskPet —— 桌面宠物（PySide6 重写版）。

包名全小写 ``deskpet``；仓库 / 项目名是 ``deskPet``（两者不要混用）。

本模块**只放常量**，不要在这里做任何装配工作：
窗口、配置、日志分别属于 ``pet_window`` / ``config`` / ``logger``。
"""

#: 程序名。窗口标题、后续日志与自启项名都以它为准（agent.md §0 的项目命名）。
PROGRAM_NAME = "deskPet"

#: 版本号。打包与 ``--version`` 用。
__version__ = "0.2.0"

__all__ = ["PROGRAM_NAME", "__version__"]

__author__ = "Hanson"
