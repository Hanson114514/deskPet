"""三类路径的**唯一真值**：程序目录 / 用户数据 / 打包资源。

本模块是 **`__file__` 反推路径的唯一允许出处**（agent.md §3.6 / §4.10，
task02 §6 有一条验收就是"`paths.py` 之外搜不到 `__file__`"）。
其它任何模块要路径，一律问本模块要。

三类路径（迁移清单 第二部分 §2）：

| 类别 | 打包后 | 源码运行 | 可写性 |
| --- | --- | --- | --- |
| 程序目录 | exe 所在目录 | **仓库根** | — |
| 打包资源 | `sys._MEIPASS`（临时解包目录） | 仓库根 | **只读** |
| 用户数据 | `<程序目录>/data/` | 同左 | **可写** |

> 资源与程序自身**常常看起来是同一个目录，那只是巧合**：单文件打包时资源在临时解包
> 目录、exe 在别处。所以两者**永远是两个函数**，不许合并、不许互相代替。

**本模块只用标准库**：`logger` 的崩溃兜底路径要能在"包自己已经坏掉"时使用
（agent.md §2.10 / §3.7），所以禁止在这里 import Qt 或任何项目模块。
"""

import contextlib
import sys
from collections.abc import Iterator
from pathlib import Path

from .errors import DataDirError

#: 用户数据目录名（程序目录下的子目录）。日志名与自启项名都以项目名为准，
#: 但目录名按 agent.md §0 定案固定为 `data`。
DATA_DIR_NAME = "data"

#: 自带素材（打包资源）下的子目录名。
ASSETS_DIR_NAME = "assets"

#: 用户自备素材包在用户数据目录下的子目录名。
USER_PETS_DIR_NAME = "pets"

#: 测试/自检用的目录覆盖（键是字段名，值是绝对路径）。生产代码**不要**碰它。
_overrides: dict[str, Path] = {}


# ------------------------------------------------------------------ 打包判定


def is_frozen() -> bool:
    """是否**打包运行**。这是全项目"打包判定"的**唯一出处**。

    判据（两个都是冻结工具给自己打的标记，与 Qt 无关）：

    - `sys.frozen`：PyInstaller / cx_Freeze 等冻结器会设成真；
    - `sys._MEIPASS`：PyInstaller 的**单文件**模式下资源解包目录。

    之所以**不用** `QCoreApplication.applicationFilePath()`：本函数必须能在
    `QApplication` **还没建起来**时被调用（`logger.setup_logging()` 就早于 Qt 装配），
    而解包目录这个信息应该指向**运行早期就固定**的东西。
    """

    return bool(getattr(sys, "frozen", False)) or hasattr(sys, "_MEIPASS")


# ------------------------------------------------------------------ 程序目录


def _frozen_executable_dir() -> Path | None:
    """打包运行时，exe 所在目录（拿不到就返回 `None`，由上层退化）。"""
    executable = getattr(sys, "executable", "") or ""
    if not executable:
        return None

    path = Path(executable)
    if path.name.lower() in {"python.exe", "pythonw.exe"}:
        # 冻结器理论上不会给出解释器路径；真出现说明 `sys.frozen` 是别人设的假标记。
        return None
    try:
        return path.resolve().parent
    except OSError:  # pragma: no cover - 解析失败极罕见
        return None


def _module_entry_dir() -> Path | None:
    """`python -m deskpet` 时，从包目录上溯到 `src/`（**仓库根不是它**）。

    `python -m` 的 `sys.argv[0]` 是 `<...>/src/deskpet/__main__.py`，直接取 `parent`
    会得到 `src/deskpet`——数据目录就跑到源码包里去了。所以这里从 `__main__.py`
    所在的包目录**一路向上**，直到不在包目录下为止。

    **判据有两条，缺一不可**：

    1. `__main__.__spec__.name` 以 `__main__` 结尾（Python 走 `-m` 才会这么命名）；
    2. `__main__.__file__` 的文件名恰好是 `__main__.py`。

    为什么不看 `sys.orig_argv` 里的 `-m`：它会被 `-X`、`runpy` 等真实场景改变，
    而"模块名 + 文件名"是解释器自己给出的、稳定的信息。

    为什么第一条不能换成"`spec.submodule_search_locations` 非空"：
    `python -m 包名` 时 `__main__` 的 spec 是 `包名.__main__`（`__main__` 本身
    不是包），`submodule_search_locations` 是**空**的——用它判会漏掉最常见的写法。
    """
    main_module = sys.modules.get("__main__")
    spec = getattr(main_module, "__spec__", None)
    if spec is None or not getattr(spec, "name", "").endswith("__main__"):
        return None

    package_dir = getattr(main_module, "__file__", None)
    if not package_dir:
        return None

    try:
        main_file = Path(package_dir).resolve()
    except OSError:  # pragma: no cover
        return None

    if main_file.name != "__main__.py":
        # 走的是 `python 脚本.py`（哪怕脚本目录里有 __init__.py），不是 `-m`。
        return None

    inside = main_file.parent
    # 上溯：`src/deskpet` → `src` → 仓库根，直到当前目录不再是包目录。
    # 判据用"当前目录还没跳出包内"（包目录里有 `__init__.py`）。
    while (inside / "__init__.py").is_file():
        inside = inside.parent
    return inside


def _entry_dir() -> Path:
    """本进程"被怎么启动的"所对应的目录（三类路径的唯一推导点）。

    优先级：冻结 → exe 目录；`python -m` → 包目录上溯；否则 → 入口脚本目录。
    全部失败时退化成**当前工作目录**（并留一条 stderr 线索），
    因为"没有目录"比"目录不对"更糟——调用方至少还能得到一条绝对路径。
    """
    if is_frozen():
        frozen_dir = _frozen_executable_dir()
        if frozen_dir is not None:
            return frozen_dir

    module_dir = _module_entry_dir()
    if module_dir is not None:
        return module_dir

    entry = (sys.argv[0] if sys.argv else "") or ""
    if entry:
        try:
            return Path(entry).resolve().parent
        except OSError:  # pragma: no cover - 解析失败极罕见
            pass

    fallback = Path.cwd()
    print(
        f"[deskpet.paths] 无法从启动方式推出程序目录，退化为工作目录：{fallback}",
        file=sys.stderr,
    )
    return fallback


def program_dir() -> Path:
    """**程序目录**：打包后是 exe 所在目录，源码运行是仓库根。

    用户数据（`data/`）与开机自启项都以它为准。
    """
    override = _overrides.get("program")
    return override if override is not None else _entry_dir()


def resource_dir() -> Path:
    """**只读资源根**：打包后是解包目录（`sys._MEIPASS`），源码运行是仓库根。

    自带素材 `<resource>/assets/` 从这里来。**绝不往这里写任何东西。**
    """
    override = _overrides.get("resource")
    if override is not None:
        return override

    if is_frozen():
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            return Path(meipass)

    return _entry_dir()


# ------------------------------------------------------------------ 用户数据


def data_dir() -> Path:
    """**用户数据目录** `<程序目录>/data`。

    本函数**只算路径，不建目录、不碰磁盘**（"读操作只读"，agent.md §2.10）。
    要确保它存在，用 `ensure_data_dir()`。
    """
    return program_dir() / DATA_DIR_NAME


def ensure_data_dir() -> Path:
    """幂等创建用户数据目录并返回它。**失败要记日志并抛** `DataDirError`。

    写不了数据目录 = 配置与日志都立不住，属于**启动期致命**
    （agent.md §2.10 第一类），**不是**可以静默降级的只读路径。
    所以这里不吞异常，只把系统给的原因原样包进 `DataDirError`。
    """
    target = data_dir()
    try:
        target.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise DataDirError(f"无法创建用户数据目录 {target}：{exc}") from exc
    return target


def config_path() -> Path:
    """`<data>/config.json`。"""
    return data_dir() / "config.json"


def log_path() -> Path:
    """`<data>/deskpet.log`（滚动文件是它的 `.1`）。"""
    return data_dir() / "deskpet.log"


def crash_log_path() -> Path:
    """`<data>/crash.log`。**正常运行时不存在**；只有未捕获异常才会写。"""
    return data_dir() / "crash.log"


def user_pets_dir() -> Path:
    """`<data>/pets` —— 用户自备素材包根目录。

    task02 **只提供路径**：不创建、不扫描、不做"同名时用户优先"的判定。
    """
    return data_dir() / USER_PETS_DIR_NAME


def bundled_pets_dir() -> Path:
    """`<resource>/assets` —— 自带素材根目录。**只读，绝不允许写入。**"""
    return resource_dir() / ASSETS_DIR_NAME


# ------------------------------------------------------------------ 测试用覆盖


@contextlib.contextmanager
def override_dirs(
    program: Path | str | None = None,
    resource: Path | str | None = None,
) -> Iterator[None]:
    """临时覆盖程序目录 / 资源目录，退出时恢复原状。

    **用途**：自检里模拟"打包运行"（`解包目录 ≠ exe 目录`）与"把用户数据重定向到
    临时目录"（agent.md §4.11：自检不许写用户真实目录）。

    传 `None` 的字段保持当前值。**这两个目录必须已经存在**——本函数不创建目录，
    免得把"数据目录该不该建"这件事的判定挪出 `ensure_data_dir()` 一处。
    """
    previous = dict(_overrides)
    try:
        if program is not None:
            program_path = Path(program)
            if not program_path.is_absolute():
                raise ValueError(f"程序目录覆盖必须是绝对路径：{program_path}")
            _overrides["program"] = program_path
        if resource is not None:
            resource_path = Path(resource)
            if not resource_path.is_absolute():
                raise ValueError(f"资源目录覆盖必须是绝对路径：{resource_path}")
            _overrides["resource"] = resource_path
        yield
    finally:
        _overrides.clear()
        _overrides.update(previous)
