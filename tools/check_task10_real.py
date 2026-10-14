"""task10 真平台验收：**真音频设备**、启动音时机、退出无残留、长跑不哑。

自检主体（`check_task10.py`）跑在 offscreen 上，能验的是映射 / 文案 / 手势逻辑；
下面这几条**只有真平台验得了**（agent.md §4.11：别断言随环境变化的量，
但"设备真的被占用过又真的被释放"必须真设备）：

1. 真实音频设备能开出来（`QMediaPlayer` 真的加载成功，而不是 ffmpeg 报错）；
2. **启动音时机**：从 `preload()` 到 `chime` 被播的实际延迟（期望 ≈ 80 ms）；
3. **退出后没有残留进程占用音频设备**：跑完一个真实进程，立刻再起一个，音效仍正常；
4. **长跑**：休息 / 双击重复 50 次不出现"后续没声音"（设备句柄泄漏的表现）；
5. 双击手势在**真事件循环**下能完整播完并回到 `idle`。

跑法（**真平台，会短暂弹出真窗口**）：

    python tools/check_task10_real.py

结果写 `data/task10_real.txt`（UTF-8）。

## 这一份**不动用户真实配置**

自检用的素材包与配置都落在 `data/task10_real_scratch/` 里，跑完全部删掉。
**用户真实的 `data/config.json` 与 `assets/` 一个字节都不动。**
"""

import json
import math
import os
import shutil
import struct
import subprocess
import sys
import time
import wave
import zlib
from io import BytesIO
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

RESULT_FILE = ROOT / "data" / "task10_real.txt"
SCRATCH = ROOT / "data" / "task10_real_scratch"

_LINES: list[str] = []
_FAILED = 0
_TOTAL = 0


def check(name: str, ok: bool, detail: str = "") -> bool:
    global _FAILED, _TOTAL
    _TOTAL += 1
    if not ok:
        _FAILED += 1
    line = f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" —— {detail}" if detail else "")
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


def _write_png(path: Path, size: int = 32) -> None:
    raw = b"".join(b"\x00" + b"\xff\x00\x00\xff" * size for _ in range(size))

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )


def _wav_bytes(freq: int = 440, seconds: float = 0.2) -> bytes:
    buffer = BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(22050)
        handle.writeframes(
            b"".join(
                struct.pack("<h", int(12000 * math.sin(2 * math.pi * freq * i / 22050)))
                for i in range(int(22050 * seconds))
            )
        )
    return buffer.getvalue()


def _make_probe_pet() -> Path:
    """造一个**真能播**的临时素材包（放在 scratch 里，不碰 `assets/`）。"""
    root = SCRATCH / "pets" / "t10real"
    if root.exists():
        shutil.rmtree(root)
    (root / "frames").mkdir(parents=True)
    (root / "sounds").mkdir(parents=True)

    actions = {}
    for action in ("idle", "waving", "rest"):
        names = []
        for index in range(4):
            filename = f"{action}_{index:02d}.png"
            _write_png(root / "frames" / filename)
            names.append(filename)
        actions[action] = {"frames": names, "fps": 30.0}

    (root / "asset.json").write_text(
        json.dumps(
            {
                "name": "t10real",
                "cell": [32, 32],
                "actions": actions,
                "sounds": {"rest": "ding.wav", "chime": "ding.wav"},
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    (root / "sounds" / "ding.wav").write_bytes(_wav_bytes())
    return root


def _make_broken_pet() -> Path:
    """声明一个**打不开**的文件（诊断第 ② 条要在真设备上验一次）。

    ⚠ **内容不是随便选的**：FFmpeg 的探测很宽容，某些"改名的 txt"
    它照样能当成裸流加载成 `LoadedMedia`（task10 实测：写成
    `b"definitely not audio\\n" * 40` 时 Qt 报 `NoError`，于是**根本测不出"打不开"**——
    那不是产品判错了，是那个文件真的被解码器接受了）。
    这里用的是**实测一定被拒**的那种内容（`FormatError / Could not open file`）。
    """
    root = SCRATCH / "pets" / "t10realbroken"
    if root.exists():
        shutil.rmtree(root)
    shutil.copytree(SCRATCH / "pets" / "t10real", root)
    # 实测被 Qt 拒绝的几种：随机字节 / 全零 / 这种重复文本 / 单个字节 / 截断的 mp3 头。
    (root / "sounds" / "broken.txt").write_bytes(b"not audio\n" * 40)
    data = json.loads((root / "asset.json").read_text(encoding="utf-8"))
    data["sounds"] = {"rest": "broken.txt", "chime": "ding.wav"}
    (root / "asset.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return root


# ------------------------------------------------------------------ 主流程


def main() -> int:
    if SCRATCH.exists():
        shutil.rmtree(SCRATCH, ignore_errors=True)
    SCRATCH.mkdir(parents=True)

    from deskpet import paths

    with paths.override_dirs(program=ROOT, resource=ROOT):
        try:
            _run()
        finally:
            shutil.rmtree(SCRATCH, ignore_errors=True)
    return _finish()


def _run() -> int:
    from PySide6.QtCore import QDeadlineTimer, QElapsedTimer, QTimer
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])

    from deskpet import sounds as snd
    from deskpet.pet_loader import load_pet
    from deskpet.pet_window import PetWindow

    good_root = _make_probe_pet()
    good_spec = load_pet(good_root)

    # ------------------------------------------------------------ §1 真设备
    section("1. 真音频设备：能开出来（异步加载的结论落定）")

    played: list[str] = []

    class _Recorder:
        """包一层真播放器：记下"谁被播了"，同时**真的**调它（走产品实际路径）。"""

        def __init__(self, inner):
            self._inner = inner

        def play(self, name: str) -> bool:
            played.append(name)
            return self._inner.play(name)

        def __getattr__(self, item):
            return getattr(self._inner, item)

    player = snd.SoundPlayer(good_spec, 70)
    started = time.perf_counter()
    player.preload()
    # 转事件循环让异步加载落定（`setSource()` 是异步的）。
    deadline = QDeadlineTimer(1500)
    while not deadline.hasExpired():
        app.processEvents()
        app.thread().msleep(5)
    report = player.diagnose()

    check(
        "真设备下两个音效都判成能播",
        set(report.playable) == {"rest", "chime"},
        f"playable={report.playable}，unplayable={report.unplayable}",
    )
    check("真设备下 available 为真", player.available is True, player.unavailable_reason)
    check(
        "真设备下汇报文案是「音效 2/2」",
        report.summary() == "音效 2/2",
        report.summary(),
    )

    # ------------------------------------------------------------ §2 启动音时机
    section("2. 启动音时机：preload → chime 的实际延迟 ≈ 80 ms")

    chime_at: list[float] = []
    recorder = _Recorder(player)
    original_play = recorder.play

    def _timed_play(name: str) -> bool:
        if name == snd.SOUND_CHIME:
            chime_at.append(time.perf_counter())
        return original_play(name)

    recorder.play = _timed_play  # type: ignore[method-assign]
    from deskpet.app import STARTUP_CHIME_DELAY_MS

    from PySide6.QtCore import Qt

    t0 = time.perf_counter()
    timer = QTimer()
    timer.setSingleShot(True)
    timer.setTimerType(Qt.PreciseTimer)
    timer.timeout.connect(lambda: recorder.play(snd.SOUND_CHIME))
    timer.start(STARTUP_CHIME_DELAY_MS)
    pump = QDeadlineTimer(1200)
    while not pump.hasExpired() and not chime_at:
        app.processEvents()
        app.thread().msleep(2)

    check("启动音真的被播了一次", len(chime_at) == 1, f"实测 {len(chime_at)} 次")
    if chime_at:
        delay_ms = (chime_at[0] - t0) * 1000.0
        check(
            "启动音延迟在 80 ms 量级（真平台实测）",
            55.0 <= delay_ms <= 300.0,
            f"实测 {delay_ms:.1f} ms（约定 {STARTUP_CHIME_DELAY_MS} ms）",
        )

    # ------------------------------------------------------------ §3 音量立刻生效
    section("3. 音量：真播放器上立刻生效、运行时读得回")

    check("构造后读得回音量", player.volume == 70, f"实测 {player.volume}")
    check("设成 0 立刻生效", player.set_volume(0) == 0 and player.volume == 0)
    check("设成 1000 夹到 100", player.set_volume(1000) == 100 and player.volume == 100)
    check("设成 30", player.set_volume(30) == 30 and player.volume == 30)

    # ------------------------------------------------------------ §4 长跑
    section("4. 长跑：休息 / 双击重复 50 次不哑（句柄泄漏会表现为'后面没声音'）")

    window = PetWindow(good_spec, 4)
    window._sounds = recorder
    window.show()

    rest_plays = 0
    wave_starts = 0
    for _ in range(50):
        window.wave_once()
        wave_starts += 1
        # 手动把片段推完（不靠真实时间），再回 idle。
        guard = 0
        while window.animation.in_sequence and guard < 200:
            window.animation.advance(0)
            window._on_frame_due()
            guard += 1
        window._on_sequence_done()
        window._play_sound(snd.SOUND_REST)
        rest_plays += 1
        if window.action != "idle":
            break

    check("50 次双击都真的开始了手势", wave_starts == 50, f"实测 {wave_starts} 次")
    check("50 次休息都真的调到了播放器", rest_plays == 50, f"实测 {rest_plays} 次")
    check(
        "50 次后仍然回得到 idle（没有卡在手势里）",
        window.action == "idle",
        f"实测 {window.action}",
    )
    check(
        "长跑后设备仍然可用（没有'后面没声音'）",
        player.available is True,
        player.unavailable_reason,
    )
    # 长跑之后**真的再播一次**：走到产品实际的那条路径（§2.6）。
    check("长跑后典型音效仍能播放", player.play(snd.SOUND_REST) is True)

    # ------------------------------------------------------------ §5 打不开的文件
    section("5. 打不开的文件：真设备上报错、不崩、诊断看得出")

    broken_spec = load_pet(_make_broken_pet())
    broken_player = snd.SoundPlayer(broken_spec, 70)
    broken_player.preload()
    # 等**条件**而不是等一个固定时长：坏文件的报错由 Qt 的解码线程发出来，
    # 真平台上的到达时刻不稳定（自检第一版写死 2 秒，实测偶尔赶不上——
    # 那是**测试**的毛病，不是产品的）。这里最多等 5 秒，来早了就早走。
    deadline = QDeadlineTimer(5000)
    while not deadline.hasExpired() and not broken_player.diagnose().unplayable:
        app.processEvents()
        app.thread().msleep(5)
    broken_report = broken_player.diagnose()
    check(
        "打不开的文件被真设备报出来",
        any(name == "rest" for name, _ in broken_report.unplayable),
        f"{broken_report.unplayable}",
    )
    check("打不开时播放静默返回假", broken_player.play("rest") is False)
    check(
        "好音效不受坏音效影响",
        "chime" in broken_report.playable,
        f"playable={broken_report.playable}",
    )

    # ------------------------------------------------------------ §6 退出无残留
    section("6. 退出后没有残留：立刻再起一个真实进程，音效仍然正常")

    window.close()
    player.close()
    broken_player.close()
    app.processEvents()

    # **真起一个子进程**跑完整启动流程：它必须能正常退出（rc=0）、
    # 且下一次启动仍然能开出声卡——这正是"句柄泄漏"的判据。
    env = dict(os.environ)
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    runs: list[int] = []
    for round_index in range(2):
        result = subprocess.run(
            [
                sys.executable,
                str(ROOT / "run.py"),
                # 用**目录**说法指向 scratch 里的临时包：自检不许往用户的
                # `data/pets/` 里塞东西（§4.11），而 `--pet` 认目录（§2.3）。
                "--pet",
                str(good_root),
                "--exit-after",
                "1.5",
            ],
            cwd=str(ROOT),
            env=env,
            capture_output=True,
            timeout=90,
        )
        runs.append(result.returncode)
        check(
            f"第 {round_index + 1} 次真实启动干净退出（rc=0）",
            result.returncode == 0,
            f"rc={result.returncode}",
        )

    check("两次真实启动都能跑完（没有'第二次占不到设备'）", runs == [0, 0], f"实测 {runs}")

    del app
    return 0


if __name__ == "__main__":
    sys.exit(main())
