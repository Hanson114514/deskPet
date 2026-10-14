"""单实例保护：**同一台机器的同一个登录会话里只跑一个桌宠**。

## D6 的最终选择：命名互斥体（task13 定案）

| 方案 | 为什么不用 / 用 |
| --- | --- |
| `QLocalServer` | 要在临时目录放一个套接字文件，多一份"状态文件"的创建与清理责任；而且它是 Qt 对象，**必须在 `QApplication` 之后**才能用，会把单实例检查推到启动编排的后面 |
| **命名互斥体**（本实现） | 作用域**天然就是当前登录会话**（见下）；`ctypes` 已经有统一出口 `win32.py`；不落任何文件；`QApplication` 之前就能跑，正好满足"单实例检查放在只读查询之后、建窗口之前" |

## 作用域为什么"仅当前登录会话"

名字**不以 `Global\\` 开头**时，内核把互斥体放进当前登录会话的
`BaseNamedObjects` 命名空间。于是：

- **同一用户**再启动 → 看到同一个名字 → `ERROR_ALREADY_EXISTS` → 安静退出；
- **另一个 Windows 用户**（不同会话）→ 看到的是**自己会话里**的命名空间
  → 各跑一个，互不影响（task13 §6 有一条验收专门盯它）。

**不要**改成 `Global\\` 前缀：那会让所有登录用户互相排斥，正好违反那条验收。

## 不可用时**绝不阻断启动**

`is_available()` 为假（非 Windows / DLL 没加载上 / 连互斥体都建不出来）时
`acquire()` 返回一个**"空壳句柄"**（`held=False`），调用方照常启动，
只在日志里说明"这一次少了这层护栏"（task13 §6 第 5 条）。
理由：护栏的作用是"避免开出两个"，而**打不开桌宠**是更糟的结果——
两害相权，宁可偶尔多开一个。

## 与重启的交接

`acquire()` 交给调用方一个 `SingleInstanceHandle`，**`release()` 必须能被显式调用**
（`restart_self()` 拉起新实例之后、关自己之前）。只靠"进程退出时系统回收句柄"
是不够的：新进程按 `--restart-delay`（默认 1.2 秒）先等再抢锁，
而旧进程那 1.2 秒里**还活着**——不主动放锁的话新实例会把自己判成"已有实例"然后退出，
用户看到的就是"点了重启，桌宠没了"（task13 §6 明写不许出现这一幕）。
两边一起兜住：**新进程等 1.2 秒 + 旧进程主动放锁**。
"""

import logging
from dataclasses import dataclass
from typing import Final

from . import win32
from . import logger as log

logger = logging.getLogger(__name__)

#: 互斥体名字的固定前缀。**故意不带 `Global\\`**：作用域必须是当前登录会话
#: （见模块 docstring）。这个名字是"本项目在系统里占的那个位置"，改它等于
#: "新旧版本互相看不见"，所以不许随手改。
MUTEX_PREFIX: Final[str] = "deskpet-single-instance"

#: 名字里带用户名的原因：Windows 的会话命名空间虽然已经按登录会话隔离，
#: 但**同一用户的不同会话**（例如"切换用户"而不是"注销"时的旧会话）共享
#: 同一份 `BaseNamedObjects`。带上用户名与用户名下的 SID 摘要能让
#: "两个用户各跑一个"这条更稳，也让日志里能一眼看出是谁占着。
_NAME_SEPARATOR: Final[str] = "."


class SingleInstanceError(RuntimeError):
    """单实例保护这一层自己出的问题。**调用方一律按"不可用"降级**，不往外抛。"""


def mutex_name(suffix: str = "") -> str:
    """互斥体的名字。`suffix` 供自检隔离用（生产永远不给）。

    ⚠ **名字里不许出现反斜杠以外的路径字符**：反斜杠是 Win32 命名空间的
    分隔符（`Session\\xxx`），其它字符（`:`、`/`）在名字里是非法的，
    `CreateMutexW` 会直接返回 `ERROR_INVALID_NAME`。
    """
    user = _current_user()
    base = f"{MUTEX_PREFIX}{_NAME_SEPARATOR}{user}"
    return f"{base}{_NAME_SEPARATOR}{suffix}" if suffix else base


def _current_user() -> str:
    """当前登录用户名（拿不到就退化成 `unknown`，**绝不抛**）。"""
    import getpass

    try:
        return getpass.getuser() or "unknown"
    except Exception:  # pragma: no cover - getpass 在极少数环境下会炸
        return "unknown"


def is_available() -> bool:
    """单实例保护这一层能不能用。

    `False` 时**不阻断启动**：调用方照常往下走，只记一条日志说明
    "这一次少了这层护栏"（task13 §6 第 5 条）。

    判据是 `win32.is_available()`（DLL 真加载上了）**加上**一次真实的
    "建了又关"试探——只看 DLL 的话，"能加载 DLL 但建不出互斥体"
    （例如同名对象被别的完整性级别的进程占着）会被误报成可用。
    """
    if not win32.is_available():
        return False

    handle, _created = win32.create_mutex(f"{mutex_name()}{_NAME_SEPARATOR}probe")
    if not handle:
        return False
    win32.close_handle(handle)
    return True


@dataclass
class SingleInstanceHandle:
    """单实例保护的句柄。

    | 字段 | 语义 |
    | --- | --- |
    | `held` | **本进程现在真的占着锁吗**。空壳句柄（保护不可用 / 已有实例）恒为假 |
    | `handle` | 内核句柄（0 = 没有）；`release()` 之后仍是它，但 `held` 变假 |
    | `name` | 互斥体名字（日志与自检要看得见"占的是哪一个"） |

    ⚠ `held=False` 有两种来源，**调用方要分开处理**：
    "保护机制不可用"（照常启动）与"已经有实例在跑"（安静退出）。
    区分它们的是 `acquire()` 的返回值而不是本对象——`acquire()` 返回
    `None` 才是"已有实例"，返回对象就是"可以启动"。
    """

    held: bool
    handle: int = 0
    name: str = ""
    #: 保护机制是不是**可用**（不可用时 `acquire()` 给的是一张空壳）。
    available: bool = True

    def release(self) -> bool:
        """**主动放锁**（重启路径必须调用，见模块 docstring）。

        ## ⚠ 实测：只 `ReleaseMutex` 是不够的，必须**连句柄一起关**

        `CreateMutexW` 的 `ERROR_ALREADY_EXISTS` 判据是"**这个名字的内核对象
        还在不在**"，而不是"有没有人占着它的所有权"。只要旧进程还开着句柄，
        重新 `CreateMutexW` 拿到的就仍然是 `ERROR_ALREADY_EXISTS` ——
        **放掉所有权不会让新实例进得来**。实测（本机 Python 3.14 / Win11）：

        | 旧进程做了 | 新进程 `acquire()` |
        | --- | --- |
        | 什么都没做 | `None`（判成已有实例） |
        | 只 `ReleaseMutex` | **仍然是 `None`** ← 第一版实现就栽在这里 |
        | `ReleaseMutex` + `CloseHandle` | 拿到锁 |

        所以"放锁"在这里的实际语义是**放弃这个名字**：先放所有权（让等着的
        进程能接着跑），再关句柄（对象随之消亡）。两句一起做才叫"交接"。

        ## 幂等与失败

        重复调用安全：`held` 已经是假时直接返回 `True`，不去碰一个已经放掉的互斥体。
        放锁失败**不阻断重启**——本进程马上要退出，进程消亡时系统会回收句柄，
        新实例还有 `--restart-delay` 那一层兜底。
        """
        if not self.held:
            return True
        if not self.handle:  # pragma: no cover - held 为真时句柄一定非空
            self.held = False
            return True

        self.held = False
        # 先放所有权：让"正在等这个互斥体"的进程能接着往下走。
        # ⚠ 它失败也不算致命（本进程握着它只是为了标记"我在跑"，
        # 从来没有别的进程在等它——新实例走的是 `CreateMutexW` 的
        # `ERROR_ALREADY_EXISTS` 判据，不是 `WaitForSingleObject`）。
        released = win32.release_mutex(self.handle)
        if not released:
            logger.warning(
                "单实例锁释放所有权失败（%s，错误码 %d）；接着关句柄",
                self.name,
                win32.get_last_error(),
            )
        # **再关句柄**：这一步才是让新实例进得来的那一步（见上面的实测表）。
        closed = win32.close_handle(self.handle)
        self.handle = 0
        if closed:
            logger.info("单实例锁已交接（释放所有权 + 关闭句柄）：%s", self.name)
            log.log_restart_stage(
                log.RESTART_STAGE_RELEASE, f"旧实例已放锁（释放所有权 + 关闭句柄）：{self.name}"
            )
        else:
            logger.warning(
                "单实例锁的句柄关闭失败（%s，错误码 %d）；新实例会靠 "
                "--restart-delay 那一层兜底",
                self.name,
                win32.get_last_error(),
            )
        return released and closed

    def close(self) -> None:
        """收尾：放锁 + 关句柄。**幂等**，可重复调用。"""
        self.release()


def acquire(suffix: str = "") -> SingleInstanceHandle | None:
    """抢单实例锁。

    | 返回 | 含义 | 调用方该做什么 |
    | --- | --- | --- |
    | `SingleInstanceHandle`（`held=True`） | 抢到了 | 照常启动；退出/重启时 `release()` |
    | `None` | **已经有实例在跑** | **安静退出、返回 0**（不弹窗、不报错） |
    | `SingleInstanceHandle`（`held=False`, `available=False`） | 保护机制不可用 | **照常启动** + 一条日志 |

    `suffix` 只给自检用（生产永远不给）：自检要在同一个会话里模拟
    "两个实例"，又不能跟真正开着的桌宠抢同一个名字。
    """
    name = mutex_name(suffix)

    if not win32.is_available():
        return _unavailable(name, "Win32 内核接口不可用（非 Windows 或 DLL 没加载上）")

    try:
        handle, created = win32.create_mutex(name)
    except Exception as exc:  # pragma: no cover - ctypes 调用失败极罕见
        return _unavailable(name, f"创建互斥体时出错（{type(exc).__name__}: {exc}）")

    if not handle:
        return _unavailable(
            name, f"创建互斥体失败（错误码 {win32.get_last_error()}）"
        )

    if not created:
        # **已经有实例在跑**：句柄照样要关掉（否则泄漏一次），然后返回 None。
        win32.close_handle(handle)
        logger.info("检测到已有实例在运行（%s）：本次启动安静退出", name)
        log.log_restart_stage(log.RESTART_STAGE_LOCK, f"抢锁失败：已有实例在运行（{name}）")
        return None

    logger.info("单实例锁已获取：%s", name)
    log.log_restart_stage(log.RESTART_STAGE_LOCK, f"抢锁成功：{name}")
    return SingleInstanceHandle(held=True, handle=handle, name=name, available=True)


def _unavailable(name: str, reason: str) -> SingleInstanceHandle:
    """保护机制不可用：**记日志 + 给一张空壳**，绝不阻断启动（task13 §2 第 4 条）。"""
    logger.warning(
        "单实例保护不可用（%s）：这一次少了这层护栏，重复启动会开出多个桌宠（原因：%s）",
        name,
        reason,
    )
    return SingleInstanceHandle(held=False, handle=0, name=name, available=False)
