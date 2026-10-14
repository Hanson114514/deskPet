"""重启自己：**拉起新实例 → 成功才放锁 → 关闭自己**。

换形象走"重启"而不是热替换（task13 §4）：`cell` 可能不同，热替换会留下
"状态半新半旧"的中间态。这个模块只负责"怎么活着回来"。

## 顺序是硬约定（task13 §4）

```
build_args()  →  startDetached()  →  真判返回值  →  成功才 handle.release()  →  调用方关自己
```

**先拉起新实例、再关自己**，反过来的话进程正在消失，子进程可能被一起带走。

## 两边一起兜住"锁的交接"

新进程 `--restart-delay`（**默认 1.2 秒**）先等再抢锁，旧进程在这 1.2 秒里
**还活着**。所以旧进程必须**主动放锁**——只靠"进程退出时系统回收"是不够的
（那段窗口期里新实例会把自己判成"已有实例"然后退出，用户看到的是
"点了重启，桌宠没了"，task13 §6 明写不许出现这一幕）。

`handle.release()` 的实际语义见 `single_instance`：**释放所有权 + 关闭句柄**，
两句缺一不可（只释放所有权的话互斥体对象还在，新实例照样进不来）。

## 子进程必须**完全脱离**

| 要求 | 怎么做 |
| --- | --- |
| 不继承标准流 | `QProcess.startDetached()` 本身不连管道；⚠ **不要**用 `QProcess.start()`（父进程退出会带走子进程，agent.md §4.9） |
| 独立进程组 / 会话 | `startDetached()` 在 Windows 上走 `CreateProcess` 且不带 `CREATE_NEW_PROCESS_GROUP` 之外的继承关系；真正的"脱离"靠**父进程不再持有子进程句柄**，所以**必须真判返回值**——返回 `False` 时子进程可能压根没起来 |
| 工作目录固定为**程序目录** | `startDetached(program, args, working_dir)` 的第三个参数（task13 §6 有一条验收看日志里的 `Path.cwd()`） |

## 失败**绝不退出**

`restart_self()` 返回 `False` 时调用方要弹窗
`重启失败，请手动关掉再启动一次`，**当前实例继续运行**
（task13 §2 第 7 条 / §6）。这是"用户明确要求的写操作失败"那一类
（agent.md §2.10 第二类）：必须报错、必须把系统原因原样带出来。
"""

import logging
import os
import sys
from pathlib import Path
from typing import Final

from PySide6.QtCore import QProcess

from . import paths
from .single_instance import SingleInstanceHandle

logger = logging.getLogger(__name__)

#: `--restart-delay` 的默认秒数（task13 §4 的**硬约定：1.2 秒**）。
#: ⚠ **不许为了"更快重启"改小或去掉**——它是锁交接的关键：
#: 新进程要先等旧进程把锁放掉，再去抢。改小了就是"新实例抢锁时旧实例还在跑"。
RESTART_DELAY_DEFAULT: Final[float] = 1.2

#: 命令行参数名（真值**只在这一处**：`app.py` 的解析器与本模块的拼装都引用它）。
RESTART_DELAY_ARG: Final[str] = "--restart-delay"

#: 重启失败时给用户看的那句话（task13 §4 逐字要求）。
RESTART_FAILED_MESSAGE: Final[str] = "重启失败，请手动关掉再启动一次"


def build_args(delay: float = RESTART_DELAY_DEFAULT) -> list[str]:
    """拼出新进程的参数（**必须含 `--restart-delay <秒>`**）。

    ## 为什么"新进程该用什么命令"要从**当前进程**反推

    task13 §5 明写"**不写死路径**"：打包后要指向 exe，源码运行要指向
    `pythonw.exe` + 启动脚本。三条分支：

    | 运行方式 | `program` | `args` |
    | --- | --- | --- |
    | 打包（`paths.is_frozen()`） | exe 自己 | `["--restart-delay", "1.2"]` |
    | 源码（`python run.py`） | **`pythonw.exe`**（与当前解释器同目录） | `[run.py 的绝对路径, "--restart-delay", "1.2"]` |

    ## ⚠ 源码运行为什么优先 `pythonw.exe` 而不是 `sys.executable`

    `sys.executable` 是**当前那个解释器**：用户敲的是 `python run.py`
    （控制台程序），拿它重启会让新实例**弹出一个控制台黑框**，
    而桌宠是个无控制台的应用。同目录的 `pythonw.exe` 正是"不带控制台"的那个。
    它不存在时（精简安装 / 非标准发行版）**退回 `sys.executable`**——
    有个带控制台的桌宠也好过重启失败。

    ## ⚠ 启动脚本的路径从哪来

    用 `paths.program_dir() / "run.py"`（**不是** `sys.argv[0]`）：
    `program_dir()` 是全项目"程序目录"的唯一真值（§4.13），
    而 `sys.argv[0]` 在 `python -m` 下是 `__main__.py`、在自检里是别的脚本，
    两者对不上。源码运行时程序目录就是仓库根，`run.py` 正在那里。

    重启参数**只带 `--restart-delay`**，其余一概不传：档位与位置本来就在
    配置里（task13 §5："不做重启后恢复上一次的临时状态"）。
    于是新实例的档位/位置来源显示为"沿用上次"——这正是期望的行为。
    """
    delay_text = f"{float(delay):g}"
    tail = [RESTART_DELAY_ARG, delay_text]

    if paths.is_frozen():
        program = sys.executable or ""
        return [program, *tail]

    program = _source_interpreter()
    script = paths.program_dir() / "run.py"
    return [program, str(script), *tail]


def _source_interpreter() -> str:
    """源码运行时的解释器：优先同目录的 `pythonw.exe`，退回 `sys.executable`。"""
    current = sys.executable or ""
    if not current:
        return "pythonw.exe"  # 让系统去 PATH 里找（极端兜底，不写死盘符）

    windowless = Path(current).parent / "pythonw.exe"
    try:
        if windowless.is_file():
            return str(windowless)
    except OSError:  # pragma: no cover - 探测失败不是错误
        pass
    return current


def restart_self(
    handle: SingleInstanceHandle | None,
    *,
    delay: float = RESTART_DELAY_DEFAULT,
    program_override: str | None = None,
    args_override: list[str] | None = None,
) -> bool:
    """拉起新实例 → **成功才**放锁 → 返回"要不要关自己"。

    | 返回 | 含义 | 调用方该做什么 |
    | --- | --- | --- |
    | `True` | 新实例**真的起来了**，锁已交接 | 关掉自己（`QApplication.quit()`） |
    | `False` | 没起来 | 弹窗 `重启失败，请手动关掉再启动一次`，**继续运行** |

    ## 为什么"关自己"这件事不在这里做

    本函数只回答"能不能关"。真正退出要走 `QApplication.quit()`（让事件循环
    正常收尾、`finally` 里的热键/音效/控制栏都跑一遍），那是 `app.py` 的活。
    在这里直接 `sys.exit()` 会把收尾整段跳过——表现是"重启之后音频设备被占着，
    新实例没声音"（task10 那条坑会原样复现）。

    ## 两个 `*_override` 参数

    只给自检用：task13 §6 有一条验收是"**注入重启失败**（把子进程程序路径
    改成不存在的路径）→ 弹窗 + 当前实例不退出"。生产代码永远不传。
    """
    program: str
    args: list[str]
    if program_override is not None:
        program, args = program_override, list(args_override or [])
    else:
        built = build_args(delay)
        program, args = built[0], built[1:]

    working_dir = str(paths.program_dir())
    logger.info(
        "重启：准备拉起新实例 %s %s（工作目录 %s）",
        program,
        " ".join(args),
        working_dir,
    )

    started = False
    try:
        # `startDetached()` 是**唯一**允许的用法（agent.md §4.9）：
        # `QProcess.start()` 起的子进程会随父进程退出被一起带走。
        started = _start_detached(program, args, working_dir)
    except Exception as exc:  # pragma: no cover - startDetached 一般不抛
        logger.error("重启失败：拉起新实例时出错（%s: %s）", type(exc).__name__, exc)
        return False

    if not started:
        # **必须真判返回值**（task13 §4）：`startDetached` 在可执行文件不存在
        # 时**不会抛异常**，只会把"成功"报成假——不判的话会"锁一放、自己一关"，
        # 用户看到的就是"点了重启，桌宠没了"。
        logger.error("重启失败：新实例没能起来（%s %s）", program, " ".join(args))
        return False

    logger.info("重启：新实例已拉起，开始交接单实例锁")
    if handle is not None:
        handle.release()
    return True


#: `QProcess.startDetached()` 的返回值在 PySide6 6.x 里是 **`(bool, pid)` 元组**，
#: 不是单个 bool（Qt6 的 C++ 签名就是 `bool startDetached(..., qint64 *pid)`，
#: 绑定层把出参一并返回了）。这里只关心那个 bool。
_START_DETACHED_ARITY: Final[int] = 2


def _start_detached(program: str, args: list[str], working_dir: str) -> bool:
    """调 `QProcess.startDetached()` 并**正确取出那个布尔值**。

    ## ⚠ 实测踩到的坑（task13，**这是"重启失败不退出"这条验收的命门**）

    PySide6 6.12 里 `startDetached()` 返回的是 **`(成功?, pid)` 元组**：

    ```
    >>> QProcess.startDetached(sys.executable, ['-c','pass'], cwd)
    (True, 27964)
    >>> QProcess.startDetached('Z:/nope/x.exe', [], cwd)   # 路径根本不存在
    (False, -1)
    >>> bool((False, -1))
    True          # ← 非空元组永远为真！
    ```

    所以"`started = bool(QProcess.startDetached(...))`"这种写法**永远为真**，
    重启失败会被当成成功：旧实例照常放锁、照常退出，而新实例压根没起来
    ——用户看到的是**桌宠彻底消失**，正是 task13 §6 明写不许出现的那一幕。

    本函数把两种返回形状都认下来，并且**只认第一个元素**；
    拿到元组之外的形状（老版本 / 打桩）时按布尔语义处理。
    """
    result = QProcess.startDetached(program, args, working_dir)
    if isinstance(result, tuple):
        # `(ok, pid)`：**第一个元素才是判据**。
        return bool(result[0]) if result else False
    return bool(result)


def working_directory() -> str:
    """新实例的工作目录（**程序目录**，task13 §6 有验收看它）。"""
    return str(paths.program_dir())


def current_directory() -> str:
    """本进程的当前工作目录。**只用于日志取证**（验收要看 `Path.cwd()`）。

    ⚠ 它**未必**等于程序目录：用户可能从别处敲 `python C:\\projects\\deskPet\\run.py`。
    所以子进程的工作目录是**显式传进 `startDetached()` 的**
    （`working_directory()`），不是"继承父进程的 cwd"。
    """
    return os.getcwd()
