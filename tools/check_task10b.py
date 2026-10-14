"""随机音效（task10 后续小改）自检：`sounds` 数组契约 + 随机选一个。

按 agent.md §A.3 只写**一个**脚本、共用一个 `check()`、按节输出。
结果**写文件**（`data/task10b_check.txt`，UTF-8），不靠重定向（§4.3）。

跑法：

    python tools/check_task10b.py

## 本次改动验的是什么

1. **契约变了**：`sounds.<逻辑名>` 从"一个字符串"变成"**一个文件名数组**"；
   旧的字符串写法**已废弃**（丢弃 + 警告），不是兼容。
2. **播放变了**：每次播放在该逻辑名的**可用候选里随机选一个**。
3. **逻辑名变了**：`start` / `waving` / `rest`（旧名 `chime` 作废、不留别名）。
4. 自带两个包的 `asset.json` 已按 `sounds/` 的命名规律（`<逻辑名>_<序号>.wav`）写好，
   `start` 与 `waving` **指向同一组素材**。

## 为什么不用真播放器验"随机"

`QMediaPlayer` 要 `QApplication` + 真实的音频设备，而且"响了没有"没法断言。
本脚本用**替换 `random.choice`** 的办法验"确实调用了随机选择、且范围是可用候选"
——这比"跑一百次看文件名分布"更能证明代码走了随机那条路（§4.11 的教训：
判据要指向**被验的那行代码**，不是指向一个可能碰巧成立的外部表现）。
"""

import json
import os
import shutil
import struct
import sys
import wave
from io import BytesIO
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

RESULT_FILE = ROOT / "data" / "task10b_check.txt"

_LINES: list[str] = []
_FAILED = 0
_TOTAL = 0


def check(name: str, ok: bool, detail: str = "") -> bool:
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


def _finish() -> int:
    _LINES.append("")
    summary = f"失败 {_FAILED} 项 / 共 {_TOTAL} 项" if _FAILED else f"全过（{_TOTAL} 项断言）"
    _LINES.append(summary)
    print(f"\n{summary}")
    RESULT_FILE.parent.mkdir(parents=True, exist_ok=True)
    RESULT_FILE.write_text("\n".join(_LINES), encoding="utf-8")
    return 1 if _FAILED else 0


# ------------------------------------------------------------------ 造素材


def _tiny_wav() -> bytes:
    """一小段**真能播**的 WAV（440 Hz、0.1 秒、22050 Hz 单声道）。"""
    import math

    buffer = BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(22050)
        handle.writeframes(
            b"".join(
                struct.pack("<h", int(12000 * math.sin(2 * math.pi * 440 * i / 22050)))
                for i in range(2205)
            )
        )
    return buffer.getvalue()


def _write_png(path: Path, size: int = 8) -> None:
    """写一张最小的合法 PNG（纯手工拼，免得依赖 Qt 的写图能力）。"""
    import zlib

    raw = b"".join(b"\x00" + b"\xff\x00\x00\xff" * size for _ in range(size))

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    png = (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )
    path.write_bytes(png)


def _make_pack(root: Path, sounds, files=None, namespace: str = "probe") -> Path:
    """造一个素材包；`sounds` 原样写进索引（用来验契约解析）。"""
    root.mkdir(parents=True, exist_ok=True)
    frames = root / "frames"
    frames.mkdir(exist_ok=True)
    actions = {}
    for action in ("idle", "rest", "waving"):
        names = []
        for index in range(2):
            filename = f"{action}_{index:02d}.png"
            _write_png(frames / filename)
            names.append(filename)
        actions[action] = {"frames": names, "fps": 30.0}

    data: dict[str, object] = {"name": namespace, "cell": [8, 8], "actions": actions}
    if sounds is not None:
        data["sounds"] = sounds
    (root / "asset.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    if files:
        sdir = root / "sounds"
        sdir.mkdir(exist_ok=True)
        for filename, payload in files.items():
            (sdir / filename).write_bytes(payload)
    return root


# ------------------------------------------------------------------ 主流程


def _pump(app, seconds: float = 0.6) -> None:
    """把事件循环推几拍，让 Qt 的**异步解码**把结论给出来。

    ⚠ 这一步不能省：`settle()` 在 `LoadingMedia` 阶段**故意乐观**
    （"没问出问题就按能播算"，`SoundPlayer.settle()` 的约定），坏文件的结论
    要等 Qt 的解码线程推进状态、由 `errorOccurred` 落下来。
    实测（`tools/probe_settle.py`）：`settle()` 当场两个候选都还是 `LoadingMedia`，
    转几圈之后坏文件才变成 `InvalidMedia` / `FormatError`。
    """
    import time

    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.02)


def main() -> int:
    from deskpet import paths

    ROOT.joinpath("data").mkdir(parents=True, exist_ok=True)
    # ⚠ 不用 `tempfile.TemporaryDirectory`：它在退出时要 chmod 清理，
    # 而沙箱下的新建目录 `chmod` 会被拒（WinError 5），异常会把结果冲掉。
    # 自己造 / 自己删，清理失败也不影响断言结论。
    scratch = ROOT / "data" / "task10b_scratch"
    if scratch.exists():
        shutil.rmtree(scratch, ignore_errors=True)
    scratch.mkdir(parents=True)
    try:
        with paths.override_dirs(program=ROOT, resource=ROOT):
            _run_checks(scratch)
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    return _finish()


def _run_checks(scratch: Path) -> int:
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])  # noqa: F841 - 建播放器要用

    from deskpet import pet_loader, sounds as snd

    # ------------------------------------------------------------ §1 逻辑名
    section("1. 逻辑名真值（start / waving / rest，chime 作废）")

    check(
        "逻辑名恰好是 start / waving / rest",
        snd.SOUND_NAMES == ("start", "waving", "rest"),
        f"实测 {snd.SOUND_NAMES}",
    )
    check("SOUND_START == 'start'", snd.SOUND_START == "start", snd.SOUND_START)
    check("SOUND_WAVING == 'waving'", snd.SOUND_WAVING == "waving", snd.SOUND_WAVING)
    check("SOUND_REST == 'rest'", snd.SOUND_REST == "rest", snd.SOUND_REST)
    check(
        "旧名 chime 已作废、不留别名",
        not hasattr(snd, "SOUND_CHIME"),
        "sounds.py 里不该再有 SOUND_CHIME",
    )
    check("音量量程仍是 0–100", (snd.VOLUME_MIN, snd.VOLUME_MAX) == (0, 100))

    # ------------------------------------------------------------ §2 索引契约
    section("2. sounds 契约：文件名数组（\u65e7字符串写法废弃）")

    wav = _tiny_wav()

    # 2.1 数组 → 元组，顺序保持
    kept, warns = __import__(
        "deskpet.pet_spec", fromlist=["parse_sounds"]
    ).parse_sounds({"rest": ["a.wav", "b.wav"], "waving": None}, namespace="probe")
    check(
        "数组被收成元组且保持顺序",
        kept.get("rest") == ("a.wav", "b.wav"),
        f"实测 {kept.get('rest')!r}",
    )
    check("null 收成空元组", kept.get("waving") == (), f"实测 {kept.get('waving')!r}")
    check("合法声明不产生警告", warns == [], f"实测 {len(warns)} 条警告")

    # 2.2 旧字符串写法 → 丢弃 + 警告
    kept_str, warns_str = __import__(
        "deskpet.pet_spec", fromlist=["parse_sounds"]
    ).parse_sounds({"rest": "rest_00.wav"}, namespace="probe")
    check(
        "旧的单个字符串写法被丢弃",
        "rest" not in kept_str,
        f"实测 {kept_str!r}",
    )
    check(
        "旧的字符串写法留下一条警告",
        len(warns_str) == 1 and "废弃" in warns_str[0].detail,
        f"实测 {[w.detail for w in warns_str]}",
    )

    # 2.3 数组里混坏项 → 只丢坏项
    kept_mix, warns_mix = __import__(
        "deskpet.pet_spec", fromlist=["parse_sounds"]
    ).parse_sounds({"rest": ["ok.wav", 7, "", "good.wav"]}, namespace="probe")
    check(
        "数组里的坏项被丢、好项照收",
        kept_mix.get("rest") == ("ok.wav", "good.wav"),
        f"实测 {kept_mix.get('rest')!r}",
    )
    check("混坏项留下一条警告", len(warns_mix) == 1, f"实测 {len(warns_mix)} 条")

    # 2.4 整项类型不对 → 丢弃（不是致命问题）
    kept_bad, warns_bad = __import__(
        "deskpet.pet_spec", fromlist=["parse_sounds"]
    ).parse_sounds({"rest": 42}, namespace="probe")
    check("数值项被丢弃", "rest" not in kept_bad, f"实测 {kept_bad!r}")
    check("数值项留下一条警告", len(warns_bad) == 1)

    # ------------------------------------------------------------ §3 真实包
    section("3. 自带两个包：sounds 声明按命名规律写好")

    expected = {
        "start": ("waving_00.wav", "waving_01.wav", "waving_02.wav"),
        "waving": ("waving_00.wav", "waving_01.wav", "waving_02.wav"),
        "rest": ("rest_00.wav", "rest_01.wav", "rest_02.wav"),
    }
    for namespace in ("weedy", "weedy_sightseer"):
        try:
            spec = pet_loader.load_pet(ROOT / "assets" / namespace)
        except Exception as exc:  # noqa: BLE001 - 包坏了就是这一条失败
            check(f"{namespace} 包可用", False, f"{type(exc).__name__}: {exc}")
            continue
        check(f"{namespace} 包可用", spec is not None, spec.namespace)
        check(
            f"{namespace} sounds 声明与预期一致",
            spec.sounds == expected,
            f"实测 {spec.sounds}",
        )
        check(
            f"{namespace} start 与 waving 指向同一组素材",
            spec.sounds.get("start") == spec.sounds.get("waving"),
            f"start={spec.sounds.get('start')} waving={spec.sounds.get('waving')}",
        )
        # 声明的文件必须真的都在
        missing = [
            filename
            for filenames in spec.sounds.values()
            for filename in filenames
            if not (spec.root / "sounds" / filename).is_file()
        ]
        check(f"{namespace} 声明的音效文件都在磁盘上", missing == [], f"缺 {missing}")

    # ------------------------------------------------------------ §4 随机选一个
    section("4. play() 在可用候选里随机选一个")

    pack = scratch / "rand"
    _make_pack(
        pack,
        {"rest": ["rest_00.wav", "rest_01.wav", "rest_02.wav"]},
        {"rest_00.wav": wav, "rest_01.wav": wav, "rest_02.wav": wav},
    )
    spec = pet_loader.load_pet(pack)
    player = snd.SoundPlayer(spec, volume=70)
    player.preload()
    player.settle()

    entry = player.diagnose().entries[0]
    check(
        "条目按候选各记一份（3 个）",
        len(entry.candidates) == 3,
        f"实测 {[c.filename for c in entry.candidates]}",
    )
    check(
        "可用候选 = 3 个都在",
        len(entry.playable_candidates()) == 3,
        f"实测 {len(entry.playable_candidates())}",
    )

    # 替换 random.choice，看它到底被喂进了什么范围。
    seen: list[list[str]] = []
    real_choice = snd.random.choice

    def spy(seq):
        seen.append([c.filename for c in seq])
        return real_choice(seq)

    snd.random.choice = spy
    try:
        played = [player.play("rest") for _ in range(5)]
    finally:
        snd.random.choice = real_choice

    check("play() 每次都成功", all(played), f"实测 {played}")
    check(
        "play() 每次都走 random.choice",
        len(seen) == 5,
        f"实测调用了 {len(seen)} 次",
    )
    check(
        "random.choice 拿到的正是全部可用候选",
        all(sorted(bag) == ["rest_00.wav", "rest_01.wav", "rest_02.wav"] for bag in seen),
        f"实测 {seen[0] if seen else None}",
    )

    # 坏候选要被排除在随机范围之外
    bad_pack = scratch / "rand_bad"
    _make_pack(
        bad_pack,
        {"rest": ["rest_00.wav", "broken.wav"]},
        {"rest_00.wav": wav, "broken.wav": b"this is not audio at all"},
    )
    bad_spec = pet_loader.load_pet(bad_pack)
    bad_player = snd.SoundPlayer(bad_spec, volume=70)
    bad_player.preload()
    bad_player.settle()
    # 坏文件的结论是**异步**来的：先转几拍再断言（见 `_pump`）。
    _pump(app)

    bad_entry = bad_player.diagnose().entries[0]
    broken = next(c for c in bad_entry.candidates if c.filename == "broken.wav")
    good = next(c for c in bad_entry.candidates if c.filename == "rest_00.wav")
    check("坏候选被判为打不开", broken.playable is False, f"实测 {broken.playable}（{broken.reason}）")
    check("好候选仍能播", good.playable is True, f"实测 {good.playable}")

    bags: list[list[str]] = []

    def spy2(seq):
        bags.append([c.filename for c in seq])
        return real_choice(seq)

    snd.random.choice = spy2
    try:
        bad_player.play("rest")
    finally:
        snd.random.choice = real_choice
    check(
        "坏候选不在随机范围里",
        bags and bags[0] == ["rest_00.wav"],
        f"实测 {bags[0] if bags else None}",
    )
    check(
        "组里有坏文件，逻辑名依然算可用",
        bad_entry.playable is True,
        f"实测 playable={bad_entry.playable}",
    )

    # ------------------------------------------------------------ §5 优雅退化
    section("5. 退化路径：没声明 / 候选空 / 文件不在")

    # 5.0 一组候选**全都**坏掉：整条逻辑名才算哑（与"只坏一个"相对照）。
    allbad_pack = scratch / "allbad"
    _make_pack(
        allbad_pack,
        {"rest": ["bad1.wav", "bad2.wav"]},
        {"bad1.wav": b"not audio one\n" * 40, "bad2.wav": b"not audio two\n" * 40},
    )
    allbad_player = snd.SoundPlayer(pet_loader.load_pet(allbad_pack), volume=70)
    allbad_player.preload()
    allbad_player.settle()
    _pump(app)
    allbad_entry = allbad_player.diagnose().entries[0]
    check(
        "两个候选都被判死",
        all(c.playable is False for c in allbad_entry.candidates),
        f"实测 {[(c.filename, c.playable) for c in allbad_entry.candidates]}",
    )
    check(
        "全坏时整条逻辑名才算不可播",
        allbad_entry.playable is False,
        f"实测 {allbad_entry.playable!r}",
    )
    check("全坏时 available 为假", allbad_player.available is False)
    check(
        "全坏时随机范围为空",
        allbad_entry.playable_candidates() == (),
        f"实测 {allbad_entry.playable_candidates()}",
    )
    check("全坏时 play 静默返回假", allbad_player.play("rest") is False)
    check(
        "全坏时文案报「无法播放」",
        "无法播放" in allbad_player.summary(),
        allbad_player.summary(),
    )
    allbad_player.close()

    quiet = scratch / "quiet"
    _make_pack(quiet, None)
    quiet_player = snd.SoundPlayer(pet_loader.load_pet(quiet), volume=70)
    quiet_player.preload()
    quiet_player.settle()
    check("没声明任何音效 → play 静默返回假", quiet_player.play("rest") is False)
    check("没声明任何音效 → 文案是「无音效」", quiet_player.summary() == "无音效", quiet_player.summary())

    null_pack = scratch / "nullpack"
    _make_pack(null_pack, {"rest": None})
    null_player = snd.SoundPlayer(pet_loader.load_pet(null_pack), volume=70)
    null_player.preload()
    null_player.settle()
    check("null 声明 → play 静默返回假", null_player.play("rest") is False)
    check(
        "null 声明 → 计入「声明了但没有文件名」",
        "没有文件名" in null_player.unavailable_reason,
        null_player.unavailable_reason,
    )

    gone_pack = scratch / "gone"
    _make_pack(gone_pack, {"rest": ["没有这个文件.wav"]})
    gone_player = snd.SoundPlayer(pet_loader.load_pet(gone_pack), volume=70)
    gone_player.preload()
    gone_player.settle()
    check("文件不在 → play 静默返回假", gone_player.play("rest") is False)
    check(
        "文件不在 → 计入「缺」",
        gone_player.diagnose().missing == ("rest",),
        f"实测 {gone_player.diagnose().missing}",
    )

    # ------------------------------------------------------------ §6 未声明的文件
    section("6. 诊断第 ③ 条：sounds/ 里有却没声明")

    extra_pack = scratch / "extra"
    _make_pack(
        extra_pack,
        {"rest": ["rest_00.wav"]},
        {"rest_00.wav": wav, "bgm.wav": wav},
    )
    extra_spec = pet_loader.load_pet(extra_pack)
    extra_player = snd.SoundPlayer(extra_spec, volume=70)
    extra_player.preload()
    extra_player.settle()
    check(
        "未声明的 bgm.wav 被列出来",
        extra_player.diagnose().undeclared == ("bgm.wav",),
        f"实测 {extra_player.diagnose().undeclared}",
    )
    check(
        "声明的那个不算未声明",
        "rest_00.wav" not in extra_player.diagnose().undeclared,
    )

    # 清理播放器，免得句柄留着
    for item in (player, bad_player, quiet_player, null_player, gone_player, extra_player):
        item.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
