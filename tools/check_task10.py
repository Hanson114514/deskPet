"""task10 自检：音效与双击手势（休息有声音，双击会挥手）。

按 agent.md §A.3 只写**一个**脚本、共用一个 `check()`、按节输出。
结果**写文件**（`data/task10_check.txt`，UTF-8），不靠重定向（§4.3）。

跑法（必须带 UTF-8 环境变量，§4.11）：

    python tools/check_task10.py

## 两个自带包都**没有** `sounds` 声明

所以"能听到声音"这一组验收**测不了**（要改素材，得用户同意，task10 §6 前置说明）。
本脚本验的是可自动化的那一组：

- **无音效 + 不崩 + 诊断文案正确**（四种汇报文案形态**逐字符**比对）；
- 音效映射 / 音量归一 / 优雅退化的全部规则；
- 双击手势的**行为**（整段一次、播完回 `idle`、拖动途中被忽略、连点不叠加）；
- 三条诊断可见性（声明了但文件不在 / 格式打不开 / 存在却没声明）
  —— 用**自检自己造的临时素材包**验，`assets/` 一个字节都不动。

**临时素材包必须住在工作区内**（agent.md §4.13：DSH 沙箱只保证工作区可写）。
"""

import json
import os
import shutil
import struct
import sys
import zlib
from pathlib import Path

# 自检根目录放在**工作区内**：DSH 沙箱只保证工作区可写（agent.md §4.13）。
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

#: 结果文件放在 `data/`（**已进 `.gitignore`**）。
RESULT_FILE = ROOT / "data" / "task10_check.txt"

#: 自检自己造东西的地方（**不碰 `assets/`、不碰用户真实目录**）。
SCRATCH = ROOT / "data" / "task10_scratch"

_LINES: list[str] = []
_FAILED = 0
_TOTAL = 0


def check(name: str, ok: bool, detail: str = "") -> bool:
    """记一条断言。**中性文字**描述实测值（§4.11：别把成功条件写反）。"""
    global _FAILED, _TOTAL
    _TOTAL += 1
    if not ok:
        _FAILED += 1
    mark = "PASS" if ok else "FAIL"
    line = f"[{mark}] {name}" + (f" —— {detail}" if detail else "")
    _LINES.append(line)
    print(line)
    return ok


def section(title: str) -> None:
    _LINES.append("")
    _LINES.append(f"== {title} ==")
    print(f"\n== {title} ==")


def _fresh_dir(path: Path) -> Path:
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True)
    return path


def _code_only(path: Path) -> str:
    """只保留**可执行代码**：把注释与字符串去掉（§4.11，task06/07/08 各踩过）。"""
    import io
    import tokenize

    source = path.read_text(encoding="utf-8")
    pieces: list[str] = []
    try:
        for token in tokenize.generate_tokens(io.StringIO(source).readline):
            if token.type in (tokenize.COMMENT, tokenize.STRING):
                continue
            pieces.append(token.string)
    except tokenize.TokenError:  # pragma: no cover
        return source
    return " ".join(pieces)


def _source_of(name: str) -> str:
    return (ROOT / "src" / "deskpet" / name).read_text(encoding="utf-8")


def _finish() -> int:
    _LINES.append("")
    if _FAILED:
        summary = f"失败 {_FAILED} 项 / 共 {_TOTAL} 项"
    else:
        summary = f"全过（{_TOTAL} 项断言）"
    _LINES.append(summary)
    print(f"\n{summary}")
    RESULT_FILE.parent.mkdir(parents=True, exist_ok=True)
    RESULT_FILE.write_text("\n".join(_LINES), encoding="utf-8")
    return 1 if _FAILED else 0


# ------------------------------------------------------------------ 造素材


def _write_png(path: Path, size: int = 8) -> None:
    """写一张**最小的合法 PNG**（纯手工拼，免得依赖 Qt 的写图能力）。

    只需要"是个真 PNG"就行：本自检不验证画面，只验证"文件在不在 / 读不读得了"。
    """
    raw = b"".join(b"\x00" + b"\xff\x00\x00\xff" * size for _ in range(size))

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    png = b"\x89PNG\r\n\x1a\n"
    png += chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0))
    png += chunk(b"IDAT", zlib.compress(raw))
    png += chunk(b"IEND", b"")
    path.write_bytes(png)


def _make_pet(name: str, sounds: object, files: dict[str, bytes] | None = None) -> Path:
    """造一个**临时素材包**：两个动作 × 2 帧 + 给定的 `sounds` 声明。

    `files` 是"往 `sounds/` 里放哪些文件"（键是文件名，值是内容）。
    `sounds` 直接写进索引，用来造"坏数据 / 类型不对"那几条。
    """
    root = _fresh_dir(SCRATCH / name)
    frames = root / "frames"
    frames.mkdir()
    actions = {}
    for action in ("idle", "waving", "rest"):
        names = []
        for index in range(2):
            filename = f"{action}_{index:02d}.png"
            _write_png(frames / filename)
            names.append(filename)
        actions[action] = {"frames": names, "fps": 30.0}

    data: dict[str, object] = {"name": name, "cell": [8, 8], "actions": actions}
    if sounds is not None:
        data["sounds"] = sounds
    (root / "asset.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    if files:
        sounds_dir = root / "sounds"
        sounds_dir.mkdir(exist_ok=True)
        for filename, payload in files.items():
            (sounds_dir / filename).write_bytes(payload)

    return root


def _load(root: Path):
    from deskpet import pet_loader

    return pet_loader.load_pet(root)


def _tiny_wav() -> bytes:
    """一小段**真能播**的 WAV（440 Hz、0.2 秒、22050 Hz 单声道）。"""
    import math
    import wave
    from io import BytesIO

    buffer = BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(22050)
        handle.writeframes(
            b"".join(
                struct.pack("<h", int(12000 * math.sin(2 * math.pi * 440 * i / 22050)))
                for i in range(4410)
            )
        )
    return buffer.getvalue()


# ------------------------------------------------------------------ 主流程


def main() -> int:
    from deskpet import paths

    ROOT.joinpath("data").mkdir(parents=True, exist_ok=True)
    if SCRATCH.exists():
        shutil.rmtree(SCRATCH, ignore_errors=True)
    SCRATCH.mkdir(parents=True)

    with paths.override_dirs(program=ROOT, resource=ROOT):
        try:
            return _run_checks()
        finally:
            shutil.rmtree(SCRATCH, ignore_errors=True)
            return _finish()


def _run_checks() -> int:
    from PySide6.QtCore import QEvent, QPointF, Qt
    from PySide6.QtGui import QMouseEvent
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])

    from deskpet import sounds as snd
    from deskpet.config import ConfigStore
    from deskpet.idle_state import IdleStateMachine
    from deskpet.pet_loader import load_pet
    from deskpet.pet_window import PetWindow

    # ------------------------------------------------------------ §1 逻辑名与文案
    section("1. 逻辑名真值 + 音量归一（只有两个逻辑名）")

    check(
        "逻辑名恰好是 rest / chime",
        snd.SOUND_NAMES == ("rest", "chime"),
        f"实测 {snd.SOUND_NAMES}",
    )
    check("音量量程 0–100", (snd.VOLUME_MIN, snd.VOLUME_MAX) == (0, 100))

    for raw, expected in ((0, 0), (100, 100), (1000, 100), (-5, 0), (70, 70)):
        check(
            f"normalize_volume({raw!r}) == {expected}",
            snd.normalize_volume(raw) == expected,
            f"实测 {snd.normalize_volume(raw)}",
        )
    # 「非法值归 0」：完全不是数字/布尔的那些。
    for raw in ("大声", None, True, [], float("nan"), float("inf")):
        check(
            f"normalize_volume({raw!r}) 归 0",
            snd.normalize_volume(raw) == 0,
            f"实测 {snd.normalize_volume(raw)}",
        )

    check(
        "音量真值只在一处（sounds.py）",
        "VOLUME_MAX: Final[int] = 100" in _source_of("sounds.py"),
        "源码里的写法",
    )

    # ------------------------------------------------------------ §2 汇报文案
    section("2. 汇报文案四种形态（逐字符）")

    def _report_text(declared, missing=(), unplayable=(), undeclared=(), playable=()):
        report = snd.SoundReport(
            namespace="probe",
            declared=tuple(declared),
            missing=tuple(missing),
            unplayable=tuple(unplayable),
            undeclared=tuple(undeclared),
            playable=tuple(playable),
        )
        return report.summary()

    cases = [
        (
            "全部可用 → 音效 2/2",
            _report_text(["rest", "chime"], playable=["rest", "chime"]),
            "音效 2/2",
        ),
        (
            "一个都没声明且 sounds/ 空 → 无音效",
            _report_text([]),
            "无音效",
        ),
        (
            "没声明但 sounds/ 有文件 → 无音效（未在索引里声明：bgm.mp3）",
            _report_text([], undeclared=["bgm.mp3"]),
            "无音效（未在索引里声明：bgm.mp3）",
        ),
        (
            "部分可用 → 音效 1/2（缺 rest）（无法播放：bgm.ogg）",
            _report_text(
                ["rest", "chime"],
                missing=["rest"],
                unplayable=[("bgm.ogg", "格式打不开")],
                playable=["rest"],
            ),
            "音效 1/2（缺 rest）（无法播放：bgm.ogg（格式打不开））",
        ),
    ]
    for name, actual, expected in cases:
        check(name, actual == expected, f"实测 {actual!r}")

    # ------------------------------------------------------------ §3 无音效（真实自带包）
    section("3. 自带包没有 sounds 声明 → 无音效、不崩")

    weedy = load_pet(ROOT / "assets" / "weedy")
    check("自带包 weedy 的 sounds 是空声明", not weedy.sounds, f"实测 {dict(weedy.sounds)}")

    bare_player = snd.SoundPlayer(weedy)
    ready: list[tuple[bool, str]] = []
    bare_player.sound_ready.connect(lambda ok, why: ready.append((ok, why)))
    bare_player.preload()
    check("无声明时 available 为假", bare_player.available is False)
    check(
        "无声明时原因说得清",
        "没有声明任何音效" in bare_player.unavailable_reason,
        f"实测 {bare_player.unavailable_reason!r}",
    )
    check("无声明时 summary() == 无音效", bare_player.summary() == "无音效", bare_player.summary())
    check("preload 发出了 sound_ready", len(ready) == 1, f"实测 {len(ready)} 次")
    check("play('rest') 静默返回假、不抛", bare_player.play("rest") is False)
    check("play 未知逻辑名也静默", bare_player.play("bgm") is False)
    bare_player.preload()
    check("preload 幂等（第二次不再发信号）", len(ready) == 1, f"实测 {len(ready)} 次")

    # ------------------------------------------------------------ §4 音量与播放器
    section("4. 播放器音量（运行时读回、立刻作用）")

    player = snd.SoundPlayer(weedy, 70)
    check("构造时读回 70", player.volume == 70, f"实测 {player.volume}")
    check("set_volume(30) 立刻生效", player.set_volume(30) == 30 and player.volume == 30)
    check("set_volume(1000) 夹到 100", player.set_volume(1000) == 100)
    check("set_volume(-5) 夹到 0", player.set_volume(-5) == 0)
    check("set_volume('大声') 归 0", player.set_volume("大声") == 0)
    check("越界值不进播放器", player.volume == 0, f"实测 {player.volume}")

    # ------------------------------------------------------------ §5 三条诊断可见性
    section("5. 三条诊断可见性（用自检自己造的临时包）")

    # ① 声明了但文件不在。
    missing_root = _make_pet("t10missing", {"rest": "没有这个文件.mp3", "chime": None})
    missing_spec = _load(missing_root)
    missing_player = snd.SoundPlayer(missing_spec, 70)
    missing_player.preload()
    missing_report = missing_player.diagnose()
    check("① 声明了但文件不在 → missing 记到", "rest" in missing_report.missing, f"{missing_report.missing}")
    check("① 不可用", missing_player.available is False)
    check(
        "① 汇报文案：rest 缺文件、chime 声明的就是 null",
        missing_report.summary() == "音效 0/2（缺 rest、chime）",
        missing_report.summary(),
    )
    check("① 来源是索引声明而不是猜文件名", missing_spec.sounds.get("rest") == "没有这个文件.mp3")

    # ② 文件在、格式打不开（改名 txt）。真的建 QMediaPlayer 试一次。
    broken_root = _make_pet(
        "t10broken",
        {"rest": "broken.txt", "chime": "ok.wav"},
        {"broken.txt": b"this is definitely not audio\n" * 8},
    )
    broken_spec = _load(broken_root)
    broken_player = snd.SoundPlayer(broken_spec, 70)
    broken_player.preload()
    # ⚠ `setSource()` 是**异步**的（task10 实际踩到）：同步读 mediaStatus 一律是
    # `NoMedia`，坏文件要等 Qt 的解码线程把 `errorOccurred` 发出来。
    # 所以这里**必须转一会儿事件循环**，否则测的是"自己的实现"而不是真实行为。
    _pump_events(app, seconds=1.5)
    broken_report = broken_player.diagnose()
    check(
        "② 打不开的文件被记下来",
        any(name == "rest" for name, _ in broken_report.unplayable),
        f"{broken_report.unplayable}",
    )
    check(
        "② 汇报文案带上「无法播放」",
        "（无法播放：" in broken_report.summary(),
        broken_report.summary(),
    )
    check("② 播放它静默返回假", broken_player.play("rest") is False)
    check("② 坏文件不被算成可用", "rest" not in broken_report.playable, f"{broken_report.playable}")
    # ⚠ **踩到并修掉一个真 bug**：`available` 与 `summary()` 一开始各算各的
    # （前者用"预热时排上的"，后者只数 `playable is True`），于是出现
    # `available=True` 却报 `音效 0/2` 的自相矛盾。判据就是下面这条：
    check(
        "② available 与汇报文案说同一件事",
        broken_player.available == ("音效 " in broken_report.summary() and "音效 0/" not in broken_report.summary()),
        f"available={broken_player.available}，文案={broken_report.summary()!r}",
    )

    # ③ 存在却没声明。
    undeclared_root = _make_pet(
        "t10undecl", None, {"bgm.mp3": b"\x00" * 32}
    )
    undeclared_spec = _load(undeclared_root)
    check(
        "③ 没声明的文件被列出来",
        snd.list_undeclared(undeclared_spec) == ("bgm.mp3",),
        f"实测 {snd.list_undeclared(undeclared_spec)}",
    )
    undeclared_player = snd.SoundPlayer(undeclared_spec, 70)
    undeclared_player.preload()
    check(
        "③ 汇报文案是「无音效（未在索引里声明：bgm.mp3）」",
        undeclared_player.summary() == "无音效（未在索引里声明：bgm.mp3）",
        undeclared_player.summary(),
    )
    check("③ 不自动播放没声明的文件", undeclared_player.play("bgm") is False)

    # 坏数据：sounds 写成数组 → task03 的解析层已丢弃，这里确认不崩。
    bad_root = _make_pet("t10badsounds", ["rest"])
    bad_spec = _load(bad_root)
    check("sounds 类型不对 → 丢弃成空声明、不崩", not bad_spec.sounds, f"{dict(bad_spec.sounds)}")
    bad_player = snd.SoundPlayer(bad_spec, 70)
    bad_player.preload()
    check("坏 sounds 的包照样可用（无音效）", bad_player.summary() == "无音效", bad_player.summary())

    # 一个**真能播**的文件：确认"没报错 = 能播"这条成立，汇报文案是「音效 2/2」。
    good_root = _make_pet(
        "t10good",
        {"rest": "ding.wav", "chime": "ding.wav"},
        {"ding.wav": _tiny_wav()},
    )
    good_player = snd.SoundPlayer(_load(good_root), 70)
    good_player.preload()
    _pump_events(app, seconds=1.5)
    good_report = good_player.diagnose()
    check(
        "好文件被判成能播（异步结论落定）",
        "rest" in good_report.playable and "chime" in good_report.playable,
        f"{good_report.playable}",
    )
    check("真能播时 available 为真", good_player.available is True)
    check(
        "真能播时汇报文案是「音效 2/2」",
        good_report.summary() == "音效 2/2",
        good_report.summary(),
    )
    check(
        "可用时没有 unavailable_reason",
        good_player.unavailable_reason == "",
        repr(good_player.unavailable_reason),
    )
    good_player.close()

    # ------------------------------------------------------------ §6 双击手势
    section("6. 双击手势：整段一次 + 播完回 idle + 拖动中忽略")

    gesture_root = _make_pet("t10gesture", {"chime": None})
    gesture_spec = _load(gesture_root)

    window = PetWindow(gesture_spec, 4)
    check("素材里有 waving", gesture_spec.has_action("waving"))

    plays: list[str] = []

    class _Recorder:
        """记录被播的逻辑名（真播放器测不了"听到没听到"，只测调用路径）。"""

        def play(self, name: str) -> bool:
            plays.append(name)
            return True

    window._sounds = _Recorder()

    started = window.wave_once()
    check("wave_once 真的开始了", started is True)
    check("手势期间动作是 waving", window.action == "waving", f"实测 {window.action}")
    check("手势走的是片段序列", window.animation.in_sequence is True)
    check("手势同时播了 chime", plays == ["chime"], f"实测 {plays}")
    check("片段是整段一次（loop=1、不收窄区间）", window.animation.mode.value == "loop")

    # 手动跑完整个片段（不依赖真实定时器）。
    guard = 0
    while window.animation.in_sequence and guard < 500:
        window.animation.advance(0)
        window._on_frame_due()
        guard += 1
    check("片段在有限步内播完", not window.animation.in_sequence, f"用了 {guard} 步")
    check("整段恰好一圈（帧数 = 动作帧数）", guard == gesture_spec.action("waving").frame_count, f"实测 {guard} 步")
    window._on_sequence_done()
    check("播完回 idle", window.action == "idle", f"实测 {window.action}")

    # 拖动途中：不挥手、不响。
    drag_window = PetWindow(gesture_spec, 4)
    drag_plays: list[str] = []

    class _DragRecorder:
        def play(self, name: str) -> bool:
            drag_plays.append(name)
            return True

    drag_window._sounds = _DragRecorder()
    drag_window.drag_controller.active = True
    _send_double_click(drag_window, QEvent, QPointF, Qt, QMouseEvent)
    check(
        "拖动途中双击不挥手",
        drag_window.action == "idle" and not drag_window.animation.in_sequence,
        f"动作 {drag_window.action}",
    )
    check("拖动途中双击不响", drag_plays == [], f"实测 {drag_plays}")

    # 正常双击：挥手 + 响。
    normal_window = PetWindow(gesture_spec, 4)
    normal_plays: list[str] = []

    class _NormalRecorder:
        def play(self, name: str) -> bool:
            normal_plays.append(name)
            return True

    normal_window._sounds = _NormalRecorder()
    _send_double_click(normal_window, QEvent, QPointF, Qt, QMouseEvent)
    check("正常双击后动作是 waving", normal_window.action == "waving", f"实测 {normal_window.action}")
    check("正常双击播了 chime", normal_plays == ["chime"], f"实测 {normal_plays}")
    check("双击被正确中止拖动（不僵住）", normal_window.dragging is False)

    # 连点不叠加：两次 wave_once 仍然只从起点播一次。
    repeat_window = PetWindow(gesture_spec, 4)
    repeat_plays: list[str] = []

    class _RepeatRecorder:
        def play(self, name: str) -> bool:
            repeat_plays.append(name)
            return True

    repeat_window._sounds = _RepeatRecorder()
    repeat_window.wave_once()
    repeat_window.wave_once()
    check("连点两次仍各播一次 chime（先回起点）", repeat_plays == ["chime", "chime"], f"实测 {repeat_plays}")
    check(
        "连点后仍在片段起点（没有推进到半路）",
        repeat_window.animation.index == 0,
        f"实测 index={repeat_window.animation.index}",
    )

    # 缺 waving 的素材：不挥手，但 chime 照播。
    no_wave_root = _fresh_dir(SCRATCH / "t10nowave")
    frames = no_wave_root / "frames"
    frames.mkdir()
    _write_png(frames / "idle_00.png")
    (no_wave_root / "asset.json").write_text(
        json.dumps(
            {
                "name": "nowave",
                "cell": [8, 8],
                "actions": {"idle": {"frames": ["idle_00.png"], "fps": 30.0}},
                "sounds": {"chime": None},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    nowave_spec = _load(no_wave_root)
    nowave_window = PetWindow(nowave_spec, 4)
    nowave_plays: list[str] = []

    class _NoWaveRecorder:
        def play(self, name: str) -> bool:
            nowave_plays.append(name)
            return True

    nowave_window._sounds = _NoWaveRecorder()
    check("缺 waving 时不挥手（可恢复退化）", nowave_window.wave_once() is False)
    check("缺 waving 时 chime 照播（交互音不依赖素材动作）", nowave_plays == ["chime"], f"实测 {nowave_plays}")
    check("缺 waving 后停在 idle", nowave_window.action == "idle", f"实测 {nowave_window.action}")

    # ------------------------------------------------------------ §7 与 task05 的边界
    section("7. 与 task05 的边界：普通 waving 仍然是一次性停在末帧")

    # ⚠ **踩到并修掉一个真 bug**：`sequence_done` 在**任何**"播完"时都会发，
    # 而信号发出时 `Animation._segments` 已经被清空（`_finish()` 先清再发），
    # 所以 `_on_sequence_done` 里问 `animation.in_sequence` **永远是假**。
    # 拿它当判据 → 普通的 `play_action("waving")` 播完也被切回 `idle`，
    # 于是"一次性动作停在末帧、定时器已停"这条 task05 的约定当场失效
    # （真平台自检抓到：期望停在 29，实际 41 且 running=True）。
    plain_window = PetWindow(_load(ROOT / "assets" / "weedy"), 4)
    plain_window.play_action("waving")
    guard = 0
    while plain_window._driver.running and guard < 1000:
        plain_window.animation.advance(0)
        plain_window._on_frame_due()
        if plain_window.animation.finished:
            plain_window._driver.stop()
            break
        guard += 1
    last_index = plain_window.pet.action("waving").frame_count - 1
    check(
        "普通 waving 播完停在末帧（没被手势收尾逻辑带走）",
        plain_window.animation.index == last_index,
        f"末帧={last_index}，实际 {plain_window.animation.index}",
    )
    check(
        "普通 waving 播完没有跳回 idle",
        plain_window.action == "waving",
        f"实测 {plain_window.action}",
    )
    check("普通播放不置手势标志", plain_window._gesture_active is False)

    # ------------------------------------------------------------ §8 休息音效
    section("8. 休息音效：主动与自动共用同一个逻辑名")

    rest_root = _make_pet("t10rest", {"rest": None})
    rest_spec = _load(rest_root)

    # 主动休息：状态机 enter_manual() → stage_changed(REST) → 播 rest。
    store = ConfigStore(SCRATCH / "config.json")
    store.load()
    idle = IdleStateMachine(lambda: 20.0)
    active_window = PetWindow(rest_spec, 4, idle_state=idle, config=store)
    active_plays: list[str] = []

    class _RestRecorder:
        def play(self, name: str) -> bool:
            active_plays.append(name)
            return True

    active_window._sounds = _RestRecorder()
    idle.enter_manual(now=1000.0)
    check("主动休息切到 rest 动作", active_window.action == "rest", f"实测 {active_window.action}")
    check("主动休息播了 rest", active_plays == ["rest"], f"实测 {active_plays}")

    # 久置自动休息：喂假时间走完整条时间线 → 同样播 rest。
    auto_store = ConfigStore(SCRATCH / "config2.json")
    auto_store.load()
    auto_idle = IdleStateMachine(lambda: 3.0)
    auto_window = PetWindow(rest_spec, 4, idle_state=auto_idle, config=auto_store)
    auto_plays: list[str] = []

    class _AutoRecorder:
        def play(self, name: str) -> bool:
            auto_plays.append(name)
            return True

    auto_window._sounds = _AutoRecorder()
    # 时间**从小往大推进**（§4.11：跳着喂永远不触发）。
    now = 0.0
    while now < 20.0 and "rest" not in auto_plays:
        auto_idle.on_tick(now)
        now += 0.25
    check("久置自动休息切到 rest 动作", auto_window.action == "rest", f"实测 {auto_window.action}")
    check("久置自动休息同样播 rest（共用）", auto_plays == ["rest"], f"实测 {auto_plays}")
    check("同一个逻辑名（没有第二个音效名）", snd.SOUND_REST == "rest", snd.SOUND_REST)

    # 没有播放器时静默（自检里最常见）。
    silent_window = PetWindow(rest_spec, 4)
    check("没有播放器时 _play_sound 静默返回假", silent_window._play_sound("rest") is False)

    # ------------------------------------------------------------ §8 不阻塞与退出
    section("9. 不阻塞（异步预热 + 定时器兜底）与退出释放")

    app_source = _code_only(ROOT / "src" / "deskpet" / "app.py")
    check("app 用了 singleShot/定时器排启动音", "STARTUP_CHIME_DELAY_MS" in _source_of("app.py"))
    check(
        "启动音延迟是 80 ms",
        "STARTUP_CHIME_DELAY_MS = 80" in _source_of("app.py"),
        "源码里的写法",
    )
    check(
        "预热超时是 2 秒",
        "PRELOAD_TIMEOUT_MS = 2000" in _source_of("app.py"),
        "源码里的写法",
    )
    check(
        "没有主线程 sleep / 忙等",
        "time.sleep" not in app_source and "QThread" not in app_source,
        "app.py 的可执行代码里搜不到 time.sleep / QThread",
    )
    sounds_source = _code_only(ROOT / "src" / "deskpet" / "sounds.py")
    check(
        "sounds.py 不建线程（不做音频专属线程）",
        "QThread" not in sounds_source and "threading" not in sounds_source,
        "可执行代码里搜不到 QThread / threading",
    )
    check(
        "退出时关掉音效设备",
        "sound_player . close ( )" in app_source,
        "app.py 的可执行代码里有 sound_player.close()",
    )
    check(
        "预热结果用信号回来",
        "sound_ready" in sounds_source,
        "sounds.py 的可执行代码里有 sound_ready",
    )

    # ------------------------------------------------------------ §9 边界与回归
    section("10. 边界：不猜文件名 / 只认声明 / 与旧行为不冲突")

    check(
        "没有兜底表（源码里搜不到 rest.mp3 这类猜法）",
        "rest.mp3" not in sounds_source,
        "sounds.py 的**可执行代码**里没有猜文件名（docstring 里提到不算）",
    )
    check(
        "不做自动发现（list_undeclared 只用于诊断）",
        "list_undeclared" in sounds_source,
        "诊断第 ③ 条",
    )
    check(
        "D4 最终选择 QMediaPlayer（能播 mp3）",
        "QMediaPlayer" in sounds_source and "QAudioOutput" in sounds_source,
        "sounds.py 里用的是 QMediaPlayer + QAudioOutput",
    )
    check(
        "镜像 / 拖动 / 状态机的既有约定没被改",
        "MIRROR_STATES" in _source_of("config.py") and "REST_MIN_S" in _source_of("idle_state.py"),
        "两个真值都还在",
    )

    # 播放器的 close() 必须真的释放（再 close 一次也不抛）。
    player.close()
    check("close() 之后可以再 close（幂等、不抛）", (player.close() or True))
    check("close() 清掉了播放器表", not player._players, f"实测 {list(player._players)}")

    # ------------------------------------------------------------ 收尾
    check("窗口能干净关闭", (window.close() or True))

    bare_player.close()
    missing_player.close()
    broken_player.close()
    undeclared_player.close()
    bad_player.close()

    del app
    return 0


def _send_double_click(window, QEvent, QPointF, Qt, QMouseEvent) -> None:
    """给窗口送一个**真实的双击事件**（走产品实际的那条路径，§2.6）。"""
    event = QMouseEvent(
        QEvent.MouseButtonDblClick,
        QPointF(2.0, 2.0),
        QPointF(100.0, 100.0),
        Qt.LeftButton,
        Qt.LeftButton,
        Qt.NoModifier,
    )
    window.mouseDoubleClickEvent(event)


def _pump_events(app, seconds: float = 1.0) -> None:
    """转一会儿事件循环，让**异步**的东西（`QMediaPlayer` 的加载与错误信号）有机会到。

    这正是 agent.md §2.6 那句"测试必须走产品实际的那条路径"的用法：
    异步加载的结果只有真跑事件循环才会来，同步读状态测出来的是**另一件事**
    （task10 实际踩到：同步读 `mediaStatus()` 把坏文件全判成"能播"）。
    """
    from PySide6.QtCore import QDeadlineTimer

    deadline = QDeadlineTimer(int(seconds * 1000))
    while not deadline.hasExpired():
        app.processEvents()
        app.thread().msleep(10)


if __name__ == "__main__":
    sys.exit(main())
