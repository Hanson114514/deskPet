"""音效：逻辑名映射 + 播放器封装 + **优雅退化** + 三条诊断可见性。

## 只有两个逻辑名

| 逻辑名 | 用途 |
| --- | --- |
| `rest` | 主动休息（右键菜单）+ 久置自动休息**共用**（旧名 `sit`，随动作改名） |
| `chime` | **启动音** + 双击交互音 |

**映射只由素材索引 `sounds` 声明决定**（`{"rest": "文件.mp3", "chime": null}`）：
没有兜底表、不自动认 `sounds/rest.mp3`（task10 §4"不猜文件名"）。
解析已经在 task03 落地（`pet_spec.parse_sounds`）：`null` 记成空串、
坏数据只进 warnings，本模块**只消费**，不重复解析、不重复告警。

## 播放器调用的线程约定（**必须遵守**）

**所有播放器调用都在 GUI 线程**：`QMediaPlayer` / `QAudioOutput` **不是线程安全的**，
不要从别处直接调它们（agent.md §4.9 / §2.6）。本模块**不创建任何线程**，
所有公开方法都假定调用方在 GUI 线程上——旧实现那套"专属音频线程"是 MCI 时代的包袱
（task10 §5 明写不做），**不要**加回来。

保留旧教训的**方法论**："所有设备调用走同一条确定路径" + "测试必须走产品实际使用的那条路径"：
播放一律经由 `play()` → `_player_for()`，没有第二条旁路。

## 预热的"不能阻塞"怎么表达

`preload()` **不阻塞**：它只把"该预热什么"排进事件循环，结果用**信号**回来
（`sound_ready(available, reason)`）。超时兜底由调用方（`app.py`）用**另一个定时器**做，
本模块只提供"预热进行中 / 已完成"这个状态。**绝不在主线程 `sleep` 或忙等**。

## D4 的最终选择：`QMediaPlayer` + `QAudioOutput`

选它的理由：自带素材要能放 **mp3**；`QSoundEffect` 只支持 wav
（那会逼着用户转码，而 task10 §5 明写"不转码 mp3"）。
代价是 `QMediaPlayer` 每个音效要一个实例 + 一条 `QAudioOutput`，
所以播放器**按需创建并缓存**（`_players`），`close()` 统一收掉。

## 支持哪些音频格式（**不由本模块决定**）

格式支持来自 **Qt 的 FFmpeg 后端**，本模块**不维护**"哪些格式能播"的清单：

- **播放路径不按扩展名过滤**：任何后缀都会真的去尝试播放，
  "打不开"由 `_poll_error()`（`error()` + `mediaStatus()`）判定（§4.18）；
- `KNOWN_AUDIO_SUFFIXES` 只用来让日志说人话（"这个后缀我没见过，但我还是试了"），
  **不参与任何判定**；
- "到底支持什么"运行时问 Qt：`decoder_file_formats()` / `decoder_audio_codecs()`
  （`SoundReport` 里也带着这两份，启动日志会打出来）。

本机实测 Qt 报出的容器：`MP3` / `FLAC` / `Wave` / `Ogg` / `MPEG4` / `QuickTime` /
`Mpeg4Audio` / `AAC` / `WMA` / `Matroska` / `AVI` / `WMV`；
音频编码：`MP3` / `AAC` / `AC3` / `EAC3` / `FLAC` / `Wave` / `WMA` / `ALAC`。
所以 `.ogg` / `.m4a` / `.flac` / `.opus` 这类**本来就该能用**。

⚠ 两条必须记住的事实：

1. **同一扩展名能不能打开取决于文件内容**（§4.18：FFmpeg 探针很宽容，
   某些内容会被当裸流接受）。所以"支持某格式"只能用**真文件**实测，
   扩展名保证不了任何事；
2. **打包时必须带上 Qt 的 multimedia 插件与 FFmpeg 动态库**，
   否则表现为"开发机能响、打包后没声音"（本阶段不打包，先记在这里）。

## 三条诊断可见性（task10 §4）

| # | 情况 | 判据 |
| --- | --- | --- |
| ① | 索引声明了但**文件不在** | `SoundEntry.exists` 为假 |
| ② | `sounds/` 里**格式打不开** | 试播一次失败 → `SoundEntry.playable` 为假 + 原因 |
| ③ | `sounds/` 里**存在却没声明** | 扫目录得到的文件名不在任何声明里 |

三条都只进 `diagnose()` 的报告，**绝不阻断启动**（§2.10 第三类）。
"""

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

from PySide6.QtCore import QObject, QTimer, QUrl, Signal

from .pet_spec import PetSpec

logger = logging.getLogger(__name__)

#: 音效目录名（固定，与 `frames/` 并列，agent.md §2.3）。
SOUNDS_DIR_NAME: Final[str] = "sounds"

#: 音量量程。**真值只有这一处**（配置 `volume` 的 0–100 也以它为据）。
VOLUME_MIN: Final[int] = 0
VOLUME_MAX: Final[int] = 100

#: 音量读不出来时的兜底（配置层已经校验过类型，这里挡的是"绕过配置层"的调用）。
DEFAULT_VOLUME: Final[int] = 70

#: 逻辑名真值（**只有两个**，task10 §4）。`startup` 与双击都用 `chime`。
SOUND_REST: Final[str] = "rest"
SOUND_CHIME: Final[str] = "chime"
SOUND_NAMES: Final[tuple[str, ...]] = (SOUND_REST, SOUND_CHIME)

#: 汇报文案里"缺"与"无法播放"的分隔符（**格式不可改**，task10 §2 第 7 条）。
_MISSING_SEP: Final[str] = "（缺 "
_UNPLAYABLE_SEP: Final[str] = "（无法播放："

#: 预热之后隔多久把"异步加载的结论"落定（毫秒）。
#: 排到下一拍就够了：要判的只是"同步没能判定"的那几条，
#: 而且**没报错就按能播算**（`settle()` 的约定），所以宁可早定也不要拖着。
_SETTLE_DELAY_MS: Final[int] = 0

def normalize_volume(raw: object) -> int:
    """把配置里的 `volume` 归一成 0–100。**永不抛异常。**

    - 非数字 / `bool` / NaN / ±inf → `DEFAULT_VOLUME`（配置层已经挡过一次，
      这里兜的是"绕过配置层直接调"的调用方）；
    - 数值 → **夹取**到 `[0, 100]`：`1000` → `100`、`-5` → `0`。

    注意与"非法值归 0"的**区别**（task10 §4 音量那一行）：
    * 越界的**数字**是"用户想要更大/更小"，夹取才对；
    * 完全**不是数字**的值（`"大声"`）才是"读不出来"。
      配置层按类型校验，`"大声"` 会回退默认值；`config` 的默认值表保证
      写进 `ConfigStore` 的永远是合法 int，所以这里到不了那一步。
      真到了（直接调 `set_volume("大声")`）→ 归 0 —— 「归 0」这个说法
      是 task10 §4 明写的，指的是"读不出来的值"，不是"越界的数字"。
    """
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        return VOLUME_MIN
    value = float(raw)
    if value != value or value in (float("inf"), float("-inf")):
        return VOLUME_MIN
    return int(max(VOLUME_MIN, min(VOLUME_MAX, value)))


@dataclass
class SoundEntry:
    """一个**声明过的**逻辑名当前的状况（诊断与汇报都读它）。"""

    name: str
    #: 索引里声明的文件名；空串 = 声明了逻辑名但没有文件名（`null`）。
    filename: str
    #: 绝对路径（没有文件名时为 `None`）。
    path: Path | None
    #: 文件在不在。
    exists: bool
    #: **试播过并且成功了**。`None` = 还没试过（没预热 / 预热还没轮到它）。
    playable: bool | None = None
    #: 不能播放的原因（`playable is False` 时有值）。
    reason: str = ""


@dataclass
class SoundReport:
    """`diagnose()` 的产物：三条诊断可见性 + 汇报文案的原料。

    | 字段 | 含义 |
    | --- | --- |
    | `entries` | 每个**声明过的**逻辑名一条 |
    | `declared` | 声明过的逻辑名（保持索引顺序） |
    | `missing` | 声明了但**文件不在**的逻辑名 |
    | `unplayable` | 文件在、但**格式打不开**的逻辑名 → `(名字, 原因)` |
    | `undeclared` | `sounds/` 里**存在却没声明**的文件名 |
    | `available` / `reason` | 整体可用标志与原因 |
    | `decoder_formats` / `decoder_codecs` | **Qt 后端实测支持的容器与音频编码**（运行时问出来的，不是抄的表） |
    """

    namespace: str
    entries: tuple[SoundEntry, ...] = ()
    declared: tuple[str, ...] = ()
    missing: tuple[str, ...] = ()
    unplayable: tuple[tuple[str, str], ...] = ()
    undeclared: tuple[str, ...] = ()
    available: bool = False
    reason: str = ""
    #: 能播出来的逻辑名（诊断用）。
    playable: tuple[str, ...] = field(default_factory=tuple)
    #: Qt 后端实测支持的容器 / 音频编码（问不到时为空元组）。
    decoder_formats: tuple[str, ...] = field(default_factory=tuple)
    decoder_codecs: tuple[str, ...] = field(default_factory=tuple)

    def summary(self) -> str:
        """汇报文案（**格式不可改**，task10 §2 第 7 条 / §4）。

        四种形态：

        | 情况 | 文案 |
        | --- | --- |
        | 全部可用 | `音效 2/2` |
        | 一个都没声明、`sounds/` 也空 | `无音效` |
        | 一个都没声明、但 `sounds/` 有文件 | `无音效（未在索引里声明：bgm.mp3）` |
        | 部分可用 | `音效 1/2（缺 rest）（无法播放：bgm.ogg）` |
        """
        total = len(self.declared)
        usable = len(self.playable)

        if total == 0:
            if self.undeclared:
                return f"无音效（未在索引里声明：{'、'.join(self.undeclared)}）"
            return "无音效"

        text = f"音效 {usable}/{total}"
        if self.missing:
            text += f"{_MISSING_SEP}{'、'.join(self.missing)}）"
        if self.unplayable:
            names = "、".join(f"{name}（{reason}）" for name, reason in self.unplayable)
            text += f"{_UNPLAYABLE_SEP}{names}）"
        return text


def sounds_dir(spec: PetSpec) -> Path:
    """这个素材包的音效目录 `<包根>/sounds`（**不创建、不写入**）。"""
    return spec.root / SOUNDS_DIR_NAME


#: **认得的音效扩展名**（多媒体容器 + 裸音频流）。
#:
#: ## 这张表**不参与"能不能播"的判定**
#:
#: 它只用来让日志说人话："这个后缀我没见过，但我还是试了"。
#: "打不开"一律由 `_poll_error()`（`error()` + `mediaStatus()`）说了算
#: （task10 §4 / §4.18：**别拿扩展名当判据**）。
#:
#: ## 支持哪些格式由 **Qt 的 FFmpeg 后端**决定，不由本表决定
#:
#: 本机实测（`QMediaFormat.supportedFileFormats(Decode)`）Qt 报出的容器：
#: `MP3` / `FLAC` / `Wave` / `Ogg` / `MPEG4` / `QuickTime` / `Mpeg4Audio` /
#: `AAC` / `WMA` / `Matroska` / `AVI` / `WMV`；
#: 音频编码：`MP3` / `AAC` / `AC3` / `EAC3` / `FLAC` / `Wave` / `WMA` / `ALAC`。
#: 所以 `.ogg` / `.m4a` / `.flac` / `.opus` 这些**本来就该能用**，
#: 本表只是把常见写法列全，方便日志区分"认得"与"没见过"。
#:
#: ⚠ 同一个扩展名**能不能真的打开取决于文件内容**（§4.18：FFmpeg 探针很宽容，
#: 某些内容会被当裸流接受）。所以"支持某格式"这句话只能用**真文件**实测，
#: 扩展名表保证不了任何事。
KNOWN_AUDIO_SUFFIXES: Final[frozenset[str]] = frozenset(
    {
        # 裸音频流 / 常见压缩音频
        ".wav", ".wave",
        ".mp3", ".m4a", ".mp4", ".aac", ".alac",
        ".ogg", ".oga", ".opus", ".flac", ".wma",
        # 可能装着音轨的容器（用户从别处拿来的文件常常是这些）
        ".mka", ".mkv", ".webm", ".avi", ".mov", ".aiff", ".aif", ".ac3", ".eac3",
    }
)


def _is_audio_candidate(path: Path) -> bool:
    """后缀**认不认得**（**不参与任何判定**，只影响日志措辞）。

    task10 §4 定下的分层：旧实现那张"按扩展名给 MCI 设备类型"的表**整体删除**
    （Qt 不需要设备类型）。这个函数回答的是"要不要提醒一句"，不是"能不能播"：
    返回 `False` 时调用方**照样**会尝试播放，只是多记一条 info
    ——"打不开"由 `_poll_error()` 说了算，不由后缀说了算。
    """
    return path.suffix.lower() in KNOWN_AUDIO_SUFFIXES


def suffix_supported(path: Path | str) -> bool:
    """这个**后缀**在已知清单里吗（纯查询，不碰磁盘、不做判定）。

    给自检与诊断用。⚠ 它**不是**"这个文件能不能播"的答案——
    那个问题只有 `SoundPlayer` 问得出来（`_poll_error()`）。
    """
    return _is_audio_candidate(Path(path))


def supported_suffixes() -> tuple[str, ...]:
    """已知后缀清单（排序后的元组，供 `--list-*` / 诊断打印）。"""
    return tuple(sorted(KNOWN_AUDIO_SUFFIXES))


def decoder_file_formats() -> tuple[str, ...]:
    """**Qt 后端当前实际支持的容器**（运行时问 Qt，返回名字元组）。

    这是"支持哪些格式"这条事实的**唯一权威来源**：格式支持随 Qt / FFmpeg
    的构建而变，抄一张写死的表迟早会与事实分叉。问不到（没有
    `QMediaFormat` / 没有 `QApplication`）→ 空元组，**永不抛异常**。
    """
    try:
        from PySide6.QtMultimedia import QMediaFormat

        formats = QMediaFormat().supportedFileFormats(
            QMediaFormat.ConversionMode.Decode
        )
    except Exception as exc:  # noqa: BLE001 - 问不到就返回空，调用方退化
        logger.debug("问 Qt 拿支持的容器失败：%s", exc)
        return ()
    return tuple(item.name for item in formats)


def decoder_audio_codecs() -> tuple[str, ...]:
    """**Qt 后端当前实际支持的音频编码**（运行时问 Qt）。问不到 → 空元组。"""
    try:
        from PySide6.QtMultimedia import QMediaFormat

        codecs = QMediaFormat().supportedAudioCodecs(
            QMediaFormat.ConversionMode.Decode
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug("问 Qt 拿支持的音频编码失败：%s", exc)
        return ()
    return tuple(item.name for item in codecs)


def list_undeclared(spec: PetSpec) -> tuple[str, ...]:
    """`sounds/` 里**存在却没声明**的文件名（诊断可见性第 ③ 条）。

    只列**当前那一层**的文件（不递归子目录）：声明里的文件名是相对 `sounds/`
    的，子目录里的东西从来不会被声明到，列出来只会是噪音。
    目录不存在 / 读不了 → 空元组（**不是错误**：没有音效是正常状态）。
    """
    directory = sounds_dir(spec)
    try:
        if not directory.is_dir():
            return ()
        names = [
            entry.name
            for entry in directory.iterdir()
            if entry.is_file()
        ]
    except OSError as exc:
        logger.warning("音效目录读不了：%s（%s）", directory, exc)
        return ()

    declared = {filename for filename in spec.sounds.values() if filename}
    return tuple(sorted(name for name in names if name not in declared))


class SoundPlayer(QObject):
    """按素材包构建的音效播放器：**任何一步失败都只是"没有声音"**。

    行为契约（task10 §4）：

    | 项 | 约定 |
    | --- | --- |
    | 音量 | 配置 `volume`，0–100，越界夹取；非法值归 0 |
    | 预加载 | **异步**预热（`preload()` 立即返回，结果走 `sound_ready`） |
    | 启动音时机 | 由调用方（`app.py`）在预热有结果后按 80 ms 排 `chime` |
    | 重复触发 | 再次播放**先回到起点**再播，避免叠加 |
    | 优雅退化 | 可用标志变 `False` + 记原因，**绝不让程序起不来** |
    | 退出 | `close()` 关闭全部播放器（挂在 `aboutToQuit` / `closeEvent` 上） |
    """

    #: 预热完成（成功或失败都在这里说清楚）。`agent.md` §2.2 的信号表。
    sound_ready = Signal(bool, str)

    def __init__(
        self,
        spec: PetSpec,
        volume: int = DEFAULT_VOLUME,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._spec = spec
        self._volume = normalize_volume(volume)
        #: 逻辑名 → 播放器（**按需创建并缓存**；`close()` 统一收掉）。
        self._players: dict[str, object] = {}
        #: 逻辑名 → `QAudioOutput`（音量挂在它上面，与播放器一一对应）。
        self._outputs: dict[str, object] = {}
        #: 每个声明过的逻辑名的状况（诊断与汇报的原料）。
        self._entries: dict[str, SoundEntry] = {}
        #: 整体可用标志与原因（优雅退化的对外表达）。
        self._available = False
        self._reason = ""
        #: 预热是否已经跑过（`preload()` 幂等）。
        self._preloaded = False
        #: 预热期间是否有过"播不了"的失败（决定 `_available`）。
        self._playable_names: list[str] = []

        self._collect_entries()

    # ------------------------------------------------------------ 装配

    def _collect_entries(self) -> None:
        """按索引声明建出条目表，并顺手记下"声明了但文件不在"（诊断第 ① 条）。

        **不在这里试播**：试播要建 `QMediaPlayer`，而构造 `SoundPlayer` 可能发生在
        "窗口还没起来"时（`app.py` 的顺序是窗口起来之后建，但自检里会提前建）。
        `QMediaPlayer` 没有 `QApplication` 会直接崩，所以试播一律留给 `preload()`。
        """
        directory = sounds_dir(self._spec)
        for name, filename in self._spec.sounds.items():
            if not filename:
                # `null` / 空串 = "这个包确实没有这个音效"（与没写这个键等价）。
                self._entries[name] = SoundEntry(
                    name=name,
                    filename="",
                    path=None,
                    exists=False,
                    playable=False,
                    reason="索引里声明了但没有给文件名",
                )
                logger.info("音效 %s：索引里声明了但没有给文件名，静默", name)
                continue

            path = directory / filename
            exists = path.is_file()
            self._entries[name] = SoundEntry(
                name=name, filename=filename, path=path, exists=exists
            )
            if not exists:
                logger.warning("音效 %s 声明了文件但文件不在：%s", name, path)
            elif not _is_audio_candidate(path):
                # 扩展名认不出来**不是**结论：照样试一次，"打不开"由试播说了算。
                logger.info("音效 %s 的扩展名认不出来，仍然尝试播放：%s", name, path)

        if not self._entries:
            self._reason = "素材索引里没有声明任何音效"
            logger.info("素材包 %s：无音效（索引里没有 sounds 声明）", self._spec.namespace)

    # ------------------------------------------------------------ 只读

    @property
    def spec(self) -> PetSpec:
        """本播放器服务的素材包。"""
        return self._spec

    @property
    def volume(self) -> int:
        """**播放器持有的当前音量**（0–100；设置界面从它读，task11 用）。"""
        return self._volume

    @property
    def available(self) -> bool:
        """有没有至少一个**真的能播出来**的音效（优雅退化的对外表达）。"""
        return self._available

    @property
    def unavailable_reason(self) -> str:
        """不可用的原因（可选音效全都没有时给出人话）。"""
        return self._reason

    @property
    def preloaded(self) -> bool:
        """预热是否已经跑过（`preload()` 幂等判据）。"""
        return self._preloaded

    def has_sound(self, name: str) -> bool:
        """这个逻辑名**声明过没有**（"没声明就不响"，不猜文件名）。"""
        return name in self._entries

    def declared_names(self) -> tuple[str, ...]:
        """声明过的逻辑名（保持索引顺序）。"""
        return tuple(self._entries)

    # ------------------------------------------------------------ 预热

    def preload(self) -> None:
        """**异步预热**：立即返回，结果由 `sound_ready(available, reason)` 带回来。

        实现上它其实是"同步建好播放器并试一次静音播放"，**但它不阻塞事件循环**：
        建 `QMediaPlayer` + `setSource()` + `play()` 都是立即返回的（Qt 内部异步加载），
        真正的解码在 Qt 自己的线程里。这样做的理由：

        - `QMediaPlayer` 的首播会有可感知的卡顿，预热就是为了把那一次提前到启动期；
        - 若在这里 `sleep` 等"加载完成"信号，就是**主线程忙等**——
          task10 §4 明写禁止。所以本函数**不等**任何信号，只把状态推进。
        - "预热到底成没成"由调用方用一个 **2 秒超时定时器**兜底
          （`app.py` 的 `_schedule_startup_chime`），超时就当作"没有音效"照常启动。

        **幂等**：重复调用只做一次。
        """
        if self._preloaded:
            return
        self._preloaded = True

        playable: list[str] = []
        for name, entry in self._entries.items():
            if entry.path is None or not entry.exists:
                # 诊断第 ① 条：声明了但文件不在。**不算可用**，也不试播。
                continue
            reason = self._prepare(name, entry)
            if reason:
                # 同步就能判定的失败（路径转不成 URL / 对象建不出来）。
                entry.playable = False
                entry.reason = reason
                logger.warning("音效 %s 打不开（%s）：%s", name, reason, entry.path)
                continue
            # 加载是**异步**的（见 `_prepare`）：此刻不能说它"能播"，
            # 只能说"没被同步判定挡掉"。真正的结论由 `settle()` 问出来，
            # 所以这里先记为 `None`（未知）。
            entry.playable = None
            playable.append(name)

        self._playable_names = playable
        # 已经确定的失败（同步那几条）先摘掉；剩下的能不能播由 `settle()` 定。
        self._available = bool(playable)
        self._reason = self._compose_reason()
        if self._available:
            logger.info(
                "音效预热开始：%s（%s）", self.report().summary(), self._spec.namespace
            )
        else:
            logger.info("音效预热结束：%s（%s）", self._reason, self._spec.namespace)
        self.sound_ready.emit(self._available, self._reason)

        # 把"异步加载的结论"排进事件循环：**放一拍**再 `settle()`。
        # 排进事件循环而不是直接调，是为了给 Qt 的解码线程一点时间把
        # `mediaStatus()` 从 `LoadingMedia` 推进到最终状态。
        QTimer.singleShot(_SETTLE_DELAY_MS, self.settle)

    def settle(self) -> bool:
        """把**异步加载的结果**落定：返回"有没有至少一个真的能播"。

        为什么要有这一步：`QMediaPlayer.setSource()` 是**异步**的，
        调用返回时状态还是 `LoadingMedia`，坏文件的结论要等 Qt 的解码线程给出来。
        调用方（`app.py`）在预热后调一次，把结论定下来：

        - 同步就失败的 / 问出来有错的 → `entry.playable is False`；
        - 问不出问题的 → 记成 `True`（**没报错就是没坏**），免得因为
          "加载慢"就把一个好音效判死——真坏了会在第一次播放时以"没声音"表现。

        **永不抛异常**：它只是把状态读一遍。
        """
        before = self._available
        before_reason = self._reason
        for name, entry in self._entries.items():
            if entry.playable is None and entry.exists:
                # ⚠ 这里**不能**直接记成"能播"：坏文件的结论要先问一次播放器。
                # `errorOccurred` 在真平台上**并不可靠**（task10 实测：设备已被别的
                # 播放器占用时，坏文件的报错根本没发出来），所以判据取**状态本身**
                # ——`mediaStatus()` / `error()` 与那个信号说的是同一件事，
                # 但它们**问得到**，不必赌信号一定送达。
                reason = self._poll_error(name)
                if reason:
                    entry.playable = False
                    entry.reason = reason
                    logger.warning("音效 %s 打不开（%s）：%s", name, reason, entry.filename)
                    continue
                entry.playable = True
        self._playable_names = [
            name for name, entry in self._entries.items() if entry.playable is True
        ]
        self._available = bool(self._playable_names)
        self._reason = self._compose_reason()
        # 结论变了（或第一次定下来）就再报一次，让 `app.py` 的汇总日志
        # 与 `available` 说同一件事——**不让汇报文案与可用标志分叉**。
        if self._available != before or self._reason != before_reason:
            logger.info(
                "音效结论已定：%s（%s）", self.report().summary(), self._spec.namespace
            )
            self.sound_ready.emit(self._available, self._reason)
        return self._available

    def _compose_reason(self) -> str:
        """"为什么没有声音"的人话（可用的那些不参与）。"""
        if self._available:
            return ""
        if not self._entries:
            return "素材索引里没有声明任何音效"

        parts: list[str] = []
        missing = [name for name, entry in self._entries.items() if not entry.exists]
        broken = [
            name
            for name, entry in self._entries.items()
            if entry.exists and entry.playable is False
        ]
        blank = [name for name, entry in self._entries.items() if entry.path is None]
        if missing:
            parts.append(f"声明了但文件不在：{'、'.join(missing)}")
        if broken:
            detail = "、".join(f"{name}（{self._entries[name].reason}）" for name in broken)
            parts.append(f"打不开：{detail}")
        if blank:
            parts.append(f"声明了但没有文件名：{'、'.join(blank)}")
        return "；".join(parts) if parts else "没有可用的音效"

    def _prepare(self, name: str, entry: SoundEntry) -> str:
        """为 `name` 建好播放器、接上错误信号、启动加载。返回**同步就能判定的**失败原因。

        ## 为什么不能同步问 `mediaStatus()`

        `QMediaPlayer.setSource()` 是**异步加载**的：调用返回时状态仍是 `NoMedia`，
        真正"打不开"要等 Qt 的解码线程报出来。实测（task10 自检）刚 `setSource()`
        之后读 `mediaStatus()` 一律是 `NoMedia`，而紧接着 Qt 自己打出
        `Could not open media ... Immediate exit requested` ——
        **同步读状态会把每一个坏文件都判成"能播"**。

        所以"格式打不开"（诊断第 ② 条）只能靠 `errorOccurred` 信号回来，
        由 `_on_player_error()` 落到 `entry.playable` 上。本函数只做**不依赖加载**
        的那几条判定（路径 → URL、对象构造），并把错误信号接好。
        """
        try:
            from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
        except ImportError as exc:  # pragma: no cover - PySide6 自带的模块
            return f"QtMultimedia 不可用（{exc}）"

        assert entry.path is not None
        url = QUrl.fromLocalFile(str(entry.path))
        if not url.isValid() or url.isEmpty():
            return f"文件路径转不成 URL：{entry.path}"

        try:
            output = self._outputs.get(name)
            if output is None:
                output = QAudioOutput(self)
                self._outputs[name] = output
            player = self._players.get(name)
            if player is None:
                player = QMediaPlayer(self)
                player.setAudioOutput(output)
                player.errorOccurred.connect(
                    lambda error, text, key=name: self._on_player_error(key, error, text)
                )
                self._players[name] = player
            output.setVolume(self._volume / 100.0)
            player.setSource(url)
        except Exception as exc:  # noqa: BLE001 - 播放器构造失败也只是"没有声音"
            return f"{type(exc).__name__}: {exc}"
        return ""

    def _poll_error(self, name: str) -> str:
        """**问一次**播放器"这个文件到底能不能开"，返回原因（没问题时空串）。

        为什么要有这一条而不是只等 `errorOccurred`：**信号不保证送达**。
        task10 真平台实测到一个具体场景——设备已经被另一个播放器占用时，
        坏文件的报错根本没发出来，只依赖信号就会把坏文件判成"能播"。
        而 `mediaStatus()` / `error()` 与那个信号说的是同一件事，
        区别只是它们**问得到**。

        判据（两条任一成立即为坏）：

        - `mediaStatus()` 是 `InvalidMedia`；
        - `error()` 不是 `NoError`。

        **永不抛异常**：拿不到播放器 / 问不动都按"没问题"处理（乐观，
        真坏了会在第一次播放时以"没声音"的形式表现出来，不影响启动）。
        """
        player = self._players.get(name)
        if player is None:
            return ""
        try:
            from PySide6.QtMultimedia import QMediaPlayer
        except ImportError:  # pragma: no cover - PySide6 自带的模块
            return ""

        try:
            error = player.error()
            if error != QMediaPlayer.Error.NoError:
                text = player.errorString() or str(getattr(error, "name", error))
                return text
            if player.mediaStatus() == QMediaPlayer.MediaStatus.InvalidMedia:
                return "Qt 认为这个文件不是可播放的媒体"
        except Exception as exc:  # noqa: BLE001 - 问不出来就不下结论
            logger.debug("查音效 %s 的播放状态失败：%s", name, exc)
        return ""

    def _on_player_error(self, name: str, error: object, text: str) -> None:
        """Qt 报了播放错误：**把它记进条目**（诊断第 ② 条的唯一来源）。

        信号可能在预热之后才到（异步解码），所以这里不只看预热期的状态：
        任何时刻来的错误都记下来，并**停掉那一条的后续尝试**
        （`entry.playable = False` → `play()` 直接静默跳过，不再反复重试同一个坏文件）。

        `NoError` 要挡掉：Qt 会在正常的一次加载结束时也发一个 `NoError`。
        """
        entry = self._entries.get(name)
        if entry is None:  # pragma: no cover - 只有本模块建的播放器，名字一定在表里
            return
        try:
            from PySide6.QtMultimedia import QMediaPlayer

            if error == QMediaPlayer.Error.NoError:
                return
        except Exception:  # noqa: BLE001 - 拿不到枚举就按"真错误"处理
            pass

        reason = text or str(getattr(error, "name", error))
        if entry.playable is False and entry.reason == reason:
            return  # 同一个原因不重复记
        entry.playable = False
        entry.reason = reason
        # 已经在可播列表里的要从里面摘掉，否则汇报文案会把它算成可用。
        if name in self._playable_names:
            self._playable_names.remove(name)
        self._available = bool(self._playable_names)
        self._reason = self._compose_reason()
        logger.warning("音效 %s 打不开（%s）：%s", name, reason, entry.filename)

    # ------------------------------------------------------------ 播放

    def play(self, name: str) -> bool:
        """播放逻辑名 `name`；**任何一步失败都静默跳过**，返回"有没有真的播"。

        "静默跳过"是 task10 §4 明写的契约：音效缺失 / 坏掉 / 没声明
        都不该让程序有任何可见的异常，最多留一条日志。

        **重复触发先回起点**：`setPosition(0)` 之后再 `play()`，
        免得连点两次双击时两次声音叠在一起（task10 §6"不叠加成怪声"）。
        """
        entry = self._entries.get(name)
        if entry is None:
            logger.debug("音效 %s 没有在索引里声明，静默跳过", name)
            return False
        if entry.path is None or not entry.exists:
            logger.debug("音效 %s 的文件不可用（%s），静默跳过", name, entry.filename or "没有文件名")
            return False
        if entry.playable is False:
            # 已经确知打不开（`errorOccurred` 报过）：**不再重试**同一个坏文件，
            # 否则每次双击都白等一次解码超时。
            logger.debug("音效 %s 打不开，静默跳过：%s", name, entry.reason)
            return False
        player = self._players.get(name)
        output = self._outputs.get(name)
        if player is None or output is None:
            # 没预热过就直接播：补一次准备（**同一条确定路径**，不开旁路）。
            reason = self._prepare(name, entry)
            if reason:
                entry.playable = False
                entry.reason = reason
                logger.warning("音效 %s 打不开（%s），静默跳过", name, reason)
                return False
            player = self._players.get(name)
            output = self._outputs.get(name)
            if player is None or output is None:  # pragma: no cover - 走不到
                return False

        try:
            output.setVolume(self._volume / 100.0)
            player.stop()          # 先回到起点，避免叠加
            player.setPosition(0)
            player.play()
        except Exception as exc:  # noqa: BLE001 - 播放失败同样只是"没有声音"
            logger.warning("音效 %s 播放失败：%s: %s", name, type(exc).__name__, exc)
            return False
        return True

    def set_volume(self, value: int) -> int:
        """设置音量（0–100，越界夹取）；**立刻作用到已打开的播放器**。

        返回**实际采用的音量**。"立刻"是靠直接对每条 `QAudioOutput`
        调 `setVolume()` 实现的——播放器是同一个实例，改完下一次播放
        以及**正在播的那一次**都会立刻变（task10 §2 第 3 条）。
        """
        self._volume = normalize_volume(value)
        for output in self._outputs.values():
            try:
                output.setVolume(self._volume / 100.0)
            except Exception as exc:  # noqa: BLE001 - 音量没设上也只记一条
                logger.warning("音量作用到播放器失败：%s: %s", type(exc).__name__, exc)
        return self._volume

    def stop(self) -> None:
        """停掉全部正在播的音效（退出前用；不销毁播放器）。"""
        for name, player in self._players.items():
            try:
                player.stop()
            except Exception as exc:  # noqa: BLE001
                logger.debug("停掉音效 %s 失败：%s", name, exc)

    def close(self) -> None:
        """关闭全部播放器，避免句柄泄漏（task10 §4"退出"那一行）。

        **必须真的释放**：自带包虽然没有音效，但用户包会有；
        句柄不释放的表现是"退出后音频设备被占着，再启动一次就没声音了"
        （task10 §6 的验收就盯着它）。
        """
        self.stop()
        for name, player in self._players.items():
            try:
                player.setSource(QUrl())
            except Exception as exc:  # noqa: BLE001 - 释放失败也不该抛
                logger.debug("释放音效 %s 的播放器失败：%s", name, exc)
            try:
                player.deleteLater()
            except Exception as exc:  # noqa: BLE001
                logger.debug("销毁音效 %s 的播放器失败：%s", name, exc)
        self._players.clear()
        self._outputs.clear()

    # ------------------------------------------------------------ 诊断

    def diagnose(self) -> SoundReport:
        """三条诊断可见性 + **Qt 后端实测支持的格式**（结构化，调用方自己决定怎么显示）。"""
        entries = tuple(self._entries.values())
        missing = tuple(name for name, entry in self._entries.items() if not entry.exists)
        unplayable = tuple(
            (name, entry.reason or "未知原因")
            for name, entry in self._entries.items()
            if entry.exists and entry.playable is False
        )
        playable = tuple(
            name for name, entry in self._entries.items() if entry.playable is True
        )
        return SoundReport(
            namespace=self._spec.namespace,
            entries=entries,
            declared=tuple(self._entries),
            missing=missing,
            unplayable=unplayable,
            undeclared=list_undeclared(self._spec),
            available=self._available,
            reason=self._reason,
            playable=playable,
            # "支持哪些格式"这条事实**运行时问 Qt**，不抄写死的表
            # （格式支持随 Qt / FFmpeg 的构建而变，抄的迟早分叉）。
            decoder_formats=decoder_file_formats(),
            decoder_codecs=decoder_audio_codecs(),
        )

    def report(self) -> SoundReport:
        """`diagnose()` 的别名（"要一份报告"时两种叫法都有人用）。"""
        return self.diagnose()

    def summary(self) -> str:
        """汇报文案（`音效 2/2` / `无音效` / `无音效（未在索引里声明：bgm.mp3）` …）。

        **格式不可改**（task10 §2 第 7 条）。真值在 `SoundReport.summary()` 一处，
        本方法是转发——两张格式表迟早会对不上。
        """
        return self.diagnose().summary()


# ------------------------------------------------------------------ 防漂移断言
#
# 约定：模块级调用一律写在函数定义之后（曾经写反导致 NameError）。

if SOUND_NAMES != (SOUND_REST, SOUND_CHIME):  # pragma: no cover
    raise RuntimeError(f"音效逻辑名被改动了：{SOUND_NAMES}")

if (VOLUME_MIN, VOLUME_MAX) != (0, 100):  # pragma: no cover
    raise RuntimeError(f"音量量程被改动了：{VOLUME_MIN}..{VOLUME_MAX}")

# 音量归一必须满足 §4 那几条实测值，写死在断言里防漂移。
_VOLUME_CASES = {
    0: 0,
    100: 100,
    1000: 100,
    -5: 0,
}
for _raw, _expected in _VOLUME_CASES.items():  # pragma: no cover
    if normalize_volume(_raw) != _expected:
        raise RuntimeError(
            f"normalize_volume({_raw!r}) 应得 {_expected}，实际 {normalize_volume(_raw)}"
        )
del _raw, _expected
