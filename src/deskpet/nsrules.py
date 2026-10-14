r"""命名空间白名单校验（素材包**目录名**的合法性）。

**目录名就是命名空间**（agent.md §2.3 / §2.9），所以这里的规则同时决定
"哪些目录能被当成素材包"与"用户在 `--pet` / 配置里写的名字合不合法"。

三条设计取向：

1. **白名单式拒绝**，而不是"事后判断路径有没有越界"——越界判断的花样永远漏
   （`..`、`/`、`\`、盘符、UNC、结尾点被 Windows 悄悄吃掉……一次列不全）。
2. **允许中文等非 ASCII，也允许中间的普通空格**（`"有 空格"` 合法）：
   素材包名是人写的，没必要逼用户用 ASCII。
3. **本模块不知道"素材"这件事**：它只回答"这个字符串能不能当命名空间"，
   不做扫描、不碰磁盘、不读 `asset.json`。越界与否由白名单本身保证。

只用标准库：加载层要在 `QApplication` 起来之前就能被判错。
"""

import unicodedata
from pathlib import Path

#: 命名空间长度上限（agent.md §2.9）。
MAX_NAMESPACE_LENGTH = 64

#: 命名空间长度下限。
MIN_NAMESPACE_LENGTH = 1

#: **禁止出现**的字符（迁移清单 3.11 / agent.md §2.9）。
#: 这些是 Windows 文件名禁用字符，外加路径分隔符（`\` 在下面单独列，因为它是转义字符）。
FORBIDDEN_CHARS = frozenset('/:*?"<>|\\')

#: 控制字符（0–31）一律拒绝。用"逐字符判定 `ord < 32`"表达，
#: 而不是列一张 32 个字符的表——后者容易漏、也读不出意图。
_CONTROL_LIMIT = 32


def rejection_reason(namespace: object) -> str | None:
    """命名空间能不能用。能用返回 `None`，不能用返回**一句可读的原因**。

    这是本模块的**唯一判定入口**：`is_valid_namespace()` 是它的布尔包装，
    加载层的错误文字直接用它（不要在两处各写一套规则）。
    """
    if not isinstance(namespace, str):
        # `Path` 之类"看起来像字符串"的对象也走这里：调用方必须先 `.name` 成字符串。
        return f"命名空间必须是字符串，实际是 {type(namespace).__name__}"

    if namespace == "":
        return "命名空间不能是空字符串"

    if len(namespace) > MAX_NAMESPACE_LENGTH:
        return (
            f"命名空间长度 {len(namespace)} 超过上限 {MAX_NAMESPACE_LENGTH}"
        )

    if namespace != namespace.strip():
        # 首尾空白：Windows 会保留，但用户在命令行里看不见差别，
        # 于是"明明写对了却找不到包"。直接拒绝更省事。
        return f"命名空间首尾不允许空白：{namespace!r}"

    if namespace.endswith("."):
        # Windows 创建目录时会把结尾的点悄悄吃掉 → 目录名与配置里的名字不一致。
        return f"命名空间不允许以点结尾：{namespace!r}"

    if namespace.startswith("."):
        return f"命名空间不允许以点开头：{namespace!r}"

    if namespace == "." or namespace == "..":
        return f"命名空间不允许是 {namespace!r}"

    for char in namespace:
        if ord(char) < _CONTROL_LIMIT:
            return f"命名空间包含控制字符 U+{ord(char):04X}"
        if char in FORBIDDEN_CHARS:
            return f"命名空间包含禁用字符 {char!r}"

    return None


def is_valid_namespace(namespace: object) -> bool:
    """`rejection_reason()` 的布尔包装（判据**只有一处**）。"""
    return rejection_reason(namespace) is None


def namespace_from_dir(path: Path | str) -> str:
    """从目录反推命名空间 = **取路径末段**（agent.md §2.9）。

    反推出来的名字**不一定合法**（目录可能是别人乱建的），调用方要自己过
    `rejection_reason()`。传入的路径**不需要真实存在**——本函数只做字符串处理，
    这样自检可以拿假路径来测，也不会意外触发磁盘访问。

    末尾带分隔符的路径（`"assets/weedy/"`）要先规范化，否则 `Path.name` 是空串。
    """
    text = str(path)
    # `Path("a/b/").name` 已经是 `"b"`；这里保留 `Path` 只为借用它的规范化，
    # 不调用 `resolve()`（那会碰磁盘、会把相对路径变成绝对路径，测试里很难断言）。
    return Path(text).name


def fold_for_compare(name: str) -> str:
    """比较用的规范化形式（大小写无关 + 兼容分解）。

    **只用于去重与查表**，不用于回显：Windows 文件名大小写不敏感，
    `Assets` 与 `assets` 指的是同一个目录，如果按原样去重会出现两个候选。
    用 `casefold()` 而不是 `lower()`：它对非 ASCII 也成立。
    """
    return unicodedata.normalize("NFC", name).casefold()
