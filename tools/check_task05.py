"""task05 自检：动画引擎（帧推进 / 循环·往返·一次性 / 定格 / 片段序列）+ 帧率消费。

按 agent.md §A.3 只写**一个**脚本、共用一个 `check()`、按节输出。
结果**写文件**（`data/task05_check.txt`，UTF-8），不靠重定向（§4.3）。

跑法（必须带 UTF-8 环境变量，否则中文输出在 GBK 控制台会炸，§4.11）：

    python tools/check_task05.py

**本脚本不改素材**（`assets/` 是红线）：验收第 2~4 条要求"把 `idle` 的 fps
改成 24 / 60 / 坏值 / 删掉"，那几条统一用**临时素材包**在自检根里复现——
把自带包的 `asset.json` 复制一份、改掉 `fps`、指向同一个 `frames/`，
于是"只改素材就能改节奏"这件事照样被验到了，而 `assets/` 一个字节都没动。

**窗口相关的用例走 offscreen 平台插件**，不弹真窗口。
注意 offscreen **不支持 `setMask`**（§4.9），所以本脚本不断言"遮罩生效"，
只断言"换帧时确实重算了遮罩"（用桩计数）。
"""

import json
import os
import shutil
import sys
from pathlib import Path

# 自检根目录放在**工作区内**：DSH 沙箱只保证工作区可写（agent.md §4.13）。
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

#: 结果文件放在 `data/`（**已进 `.gitignore`**）。
RESULT_FILE = ROOT / "data" / "task05_check.txt"

#: 自检自己造东西的地方（**不碰 `assets/`、不碰用户真实目录**）。
SCRATCH = ROOT / "data" / "task05_scratch"

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


class FakeAction:
    """只带 `Animation` 需要的字段的假动作（**不读磁盘、不碰 Qt**）。"""

    def __init__(self, count: int, frame_ms: float = 33.3333) -> None:
        self.frames = tuple(Path(f"f{i}.png") for i in range(count))
        self.frame_ms = frame_ms
        self.fps = 1000.0 / frame_ms
        self.fps_source = None

    @property
    def frame_count(self) -> int:
        return len(self.frames)


class FakeSpec:
    """假素材包：动作名 → 帧数。"""

    namespace = "fake"

    def __init__(self, actions: dict[str, int]) -> None:
        self.actions = {name: FakeAction(count) for name, count in actions.items()}
        self.action_order = tuple(actions)

    def action(self, name: str):
        return self.actions.get(name)

    def has_action(self, name: str) -> bool:
        return name in self.actions


def _advances_to_finish(animation, limit: int = 400) -> int:
    """推进直到序列播完，返回**一共调用了几次 `advance()`**。

    **不能用 `while advance() is not None` 计数**：播完的那一次本身就返回 `None`
    （"定格 / 播完 → 不要再排下一次"），于是恰好少数一次。
    这里按"调用次数"数，才能与"一圈 = 回到起点帧"的定义对上。
    """
    for step in range(1, limit + 2):
        animation.advance()
        if animation.finished:
            return step
    return limit + 1


def _collect(animation, limit: int = 400) -> list[int]:
    """推进直到 `advance()` 返回 `None`（或到上限），返回**显示过的帧号序列**。"""
    seen = [animation.index]
    for _ in range(limit):
        if animation.advance() is None:
            break
        seen.append(animation.index)
    return seen


# ------------------------------------------------------------------ 素材夹具


def _make_spec_from(namespace: str, *, idle_fps: object = "keep", drop_idle_fps: bool = False):
    """在自检根里造一个**临时素材包**：复制索引 + 指向自带帧目录，只改 `idle.fps`。

    返回 `PetSpec`。这样"改素材就能改节奏"这条验收被真实验到，而 `assets/` 没被动过。
    """
    from deskpet import pet_loader

    source_root = ROOT / "assets" / "weedy"
    target_root = SCRATCH / namespace
    if target_root.exists():
        shutil.rmtree(target_root)
    target_root.mkdir(parents=True)

    data = json.loads((source_root / "asset.json").read_text(encoding="utf-8-sig"))
    if drop_idle_fps:
        data["actions"]["idle"].pop("fps", None)
    elif idle_fps != "keep":
        data["actions"]["idle"]["fps"] = idle_fps

    (target_root / "asset.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    # 帧目录**软链不了就整个复制**（Windows 上创建符号链接要权限）：
    # 这里只复制 idle 的帧，够本自检用，也不会把 55 MB 全拖一遍。
    frames_dir = target_root / "frames"
    frames_dir.mkdir()
    for name in data["actions"]["idle"]["frames"]:
        shutil.copy2(source_root / "frames" / name, frames_dir / name)
    for name, action in data["actions"].items():
        if name == "idle":
            continue
        action["frames"] = list(action["frames"])
    (target_root / "asset.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # 其余动作的帧文件在本夹具里不存在 → 会让整个包"不可用"。
    # 所以只留 idle 一个动作，其余从索引里删掉（本夹具只关心 idle 的帧率）。
    data["actions"] = {"idle": data["actions"]["idle"]}
    (target_root / "asset.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    return pet_loader.load_pet(target_root)


# ------------------------------------------------------------------ 主流程


def main() -> int:
    from deskpet import paths

    with paths.override_dirs(program=ROOT, resource=ROOT):
        try:
            return _run_checks()
        finally:
            shutil.rmtree(SCRATCH, ignore_errors=True)


def _run_checks() -> int:
    from PySide6.QtWidgets import QApplication

    from deskpet import animation as A
    from deskpet.pet_window import PetWindow

    app = QApplication.instance() or QApplication([])

    # ------------------------------------------------------------ §1 播放方式表
    section("1. 播放方式表（唯一真相源）")
    expected = {
        "idle": "loop",
        "running_left": "pingpong",
        "running_right": "pingpong",
        "waiting": "pingpong",
        "rest": "loop",
        "waving": "oneshot",
    }
    actual = {name: mode.value for name, mode in A.PLAY_MODES.items()}
    check("6 个规范动作的方式与卡片一致", actual == expected, f"实际 {actual}")
    check(
        "表外动作按 oneshot",
        A.mode_for("温蒂-倾听-基建-Special-x1") is A.PlayMode.ONESHOT,
        f"实际 {A.mode_for('温蒂-倾听-基建-Special-x1').value}",
    )
    check(
        "表里没有已废弃的旧名",
        not ({"jumping", "sit", "review"} & set(A.PLAY_MODES)),
        f"表里的键 {sorted(A.PLAY_MODES)}",
    )

    # ------------------------------------------------------------ §2 三种推进
    section("2. 推进：单向循环 / 往返 / 一次性")
    # ⚠ 动作名必须用**规范动作集里的真名**：播放方式表是按真名查的，
    # 自造一个 "pingpong" 当动作名只会查到"表外动作 → oneshot"，
    # 测的就不是往返了（本自检第一次跑时正是栽在这里）。
    spec = FakeSpec({"idle": 5, "waiting": 4, "waving": 4, "rest": 1})

    loop_anim = A.Animation(spec, "idle")
    loop_seen = [loop_anim.index] + [loop_anim.advance() and loop_anim.index for _ in range(6)]
    check(
        "单向循环：末帧接首帧、不回头",
        loop_seen[:7] == [0, 1, 2, 3, 4, 0, 1],
        f"帧序 {loop_seen[:7]}",
    )

    pp = A.Animation(spec, "waiting")
    pp_seen = [pp.index]
    for _ in range(9):
        pp.advance()
        pp_seen.append(pp.index)
    check(
        "往返：到末端折返、不跳回首帧",
        pp_seen[:10] == [0, 1, 2, 3, 2, 1, 0, 1, 2, 3],
        f"帧序 {pp_seen[:10]}",
    )
    check(
        "往返：折返点不丢帧（末帧与首帧在 10 拍里各出现 2 次）",
        pp_seen[:10].count(3) == 2 and pp_seen[:10].count(0) == 2,
        f"前 10 拍里末帧 {pp_seen[:10].count(3)} 次、首帧 {pp_seen[:10].count(0)} 次",
    )

    one = A.Animation(spec, "waving")
    one_anim = A.Animation(spec, "waving")
    one_seen = _collect(one_anim)
    check(
        "一次性：从末帧倒播到首帧再正播，停在末帧",
        one_seen[0] == 3 and one_seen[-1] == 3 and one_seen[-2] == 2,
        f"帧序 {one_seen}",
    )
    check(
        "一次性：停在末帧后 advance() 一直返回 None",
        one_anim.advance() is None and one_anim.advance() is None,
        f"finished={one_anim.finished}，末帧={one_anim.index}",
    )
    check("一次性：模式是 oneshot", one.mode is A.PlayMode.ONESHOT, f"实际 {one.mode.value}")

    # ------------------------------------------------------------ §3 单帧动作
    section("3. 单帧动作：不空转、不每帧重绘")
    single = A.Animation(spec, "rest")
    single_ret = single.advance()
    check(
        "单帧循环：原地不动但照常排下一拍（不是返回 None 空转）",
        single_ret is not None and single.index == 0,
        f"返回值 {single_ret}，index={single.index}",
    )
    single_one = A.Animation(FakeSpec({"waving": 1}), "waving")
    checks = [single_one.advance() for _ in range(3)]
    check(
        "单帧一次性：第一拍就收尾（返回 None）",
        checks[0] is None and single_one.finished,
        f"三次返回值 {checks}",
    )

    # ------------------------------------------------------------ §4 定格
    section("4. 定格（hold / release_hold）")
    held = A.Animation(spec, "idle")
    held.hold(3)
    check("hold(3) 后停在指定帧", held.index == 3 and held.held, f"index={held.index}")
    check("定格时 advance() 返回 None（不排下一次）", held.advance() is None, "返回值 None")
    held.release_hold()
    check(
        "release_hold() 后继续推进",
        held.advance() is not None and held.index == 4,
        f"index={held.index}",
    )
    try:
        held.hold(999)
        raised = None
    except Exception as exc:
        raised = exc
    check(
        "hold(999) 报错而不是画空白",
        isinstance(raised, A.FrameIndexError),
        f"抛出 {type(raised).__name__}：{raised}",
    )
    try:
        held.hold(-1)
        raised_neg = None
    except Exception as exc:
        raised_neg = exc
    check("hold(-1) 同样报错", isinstance(raised_neg, A.FrameIndexError),
          f"抛出 {type(raised_neg).__name__}")

    # ------------------------------------------------------------ §5 片段序列
    section("5. 片段序列：圈数 / 中断 / 夹取")
    # 往返动作的一圈含两个折返点：4 帧的一圈 = 4 拍（不是 2 拍）。
    # 一圈的定义是"**回到起点帧且方向朝前**"（task05 §4）：4 帧的区间走
    # 0→1→2→3→0 共 **4 次推进**才回到起点，所以 ('loop', 1) 是 4 拍。
    # **不能按"到达末端"计次**（那会算成 2 拍，正好是圈数的两倍）。
    seg_pp = A.Animation(spec, "idle")
    done_signals: list[int] = []
    seg_pp.sequence_done.connect(lambda: done_signals.append(1))
    seg_pp.play_sequence([("waiting", None, ("loop", 1))])
    steps = _advances_to_finish(seg_pp)
    check(
        "往返动作 ('loop', 1) 恰好 4 拍结束（不按折返点计成 2 拍）",
        steps == 4 and seg_pp.finished,
        f"用了 {steps} 拍，finished={seg_pp.finished}",
    )
    check("片段播完发出 sequence_done", len(done_signals) == 1, f"收到 {len(done_signals)} 次")

    seg_loop = A.Animation(spec, "idle")
    seg_loop.play_sequence([("idle", None, ("loop", 2))])
    steps2 = _advances_to_finish(seg_loop)
    check("单向循环 ('loop', 2) 恰好 10 拍结束（5 帧 × 2 圈）", steps2 == 10, f"用了 {steps2} 拍")

    seg_auto = A.Animation(spec, "idle")
    seg_auto.play_sequence([("waiting", None, "auto")])
    auto_steps = 0
    while auto_steps < 60 and seg_auto.advance() is not None:
        auto_steps += 1
    check(
        "auto + 循环动作：永不结束（60 拍仍在播）",
        auto_steps == 60 and not seg_auto.finished,
        f"播了 {auto_steps} 拍，finished={seg_auto.finished}",
    )

    seg_auto_one = A.Animation(spec, "idle")
    seg_auto_one.play_sequence([("waving", None, "auto")])
    one_steps = 0
    while one_steps < 100 and seg_auto_one.advance() is not None:
        one_steps += 1
    check(
        "auto + 一次性动作：播到末帧即止",
        seg_auto_one.finished and seg_auto_one.index == 3,
        f"用了 {one_steps} 拍，停在 {seg_auto_one.index}",
    )

    seg_two = A.Animation(spec, "idle")
    seg_two.play_sequence([("idle", None, ("loop", 1)), ("waiting", None, ("loop", 1))])
    two_steps = _advances_to_finish(seg_two)
    check(
        "两段序列：先播完第一段再切第二段",
        two_steps == 9 and seg_two.action == "waiting",
        f"共 {two_steps} 拍（idle 5 + waiting 4），最后动作 {seg_two.action}",
    )

    seg_interrupt = A.Animation(spec, "idle")
    seg_interrupt.play_sequence([("idle", None, ("loop", 5))])
    seg_interrupt.advance()
    seg_interrupt.set_action("waiting")
    check(
        "set_action() 中断正在播的片段序列",
        not seg_interrupt.in_sequence and seg_interrupt.action == "waiting",
        f"in_sequence={seg_interrupt.in_sequence}",
    )

    seg_clamp = A.Animation(spec, "idle")
    seg_clamp.play_sequence([("idle", (2, 99), ("loop", 1))])
    clamp_steps = 0
    while clamp_steps < 100 and seg_clamp.advance() is not None:
        clamp_steps += 1
    check(
        "越界区间被夹住（(2, 99) → 只剩 2 帧可用）",
        clamp_steps == 2 and seg_clamp.index == 2,
        f"用了 {clamp_steps} 拍，停在 {seg_clamp.index}",
    )

    empty_seq = A.Animation(spec, "idle")
    empty_seq.play_sequence([])
    check("空序列被忽略（不假装播过一遍）", not empty_seq.in_sequence, "in_sequence=False")

    # ------------------------------------------------------------ §6 坏动作
    section("6. 坏动作：抛错并列出可用动作")
    try:
        A.Animation(spec, "abc")
        bad_raised = None
    except Exception as exc:
        bad_raised = exc
    check(
        "构造时给坏动作 → UnknownActionError",
        isinstance(bad_raised, A.UnknownActionError),
        f"抛出 {type(bad_raised).__name__}",
    )
    check(
        "错误信息里列出了可用动作",
        bad_raised is not None and "idle" in str(bad_raised) and "abc" in str(bad_raised),
        f"信息：{bad_raised}",
    )
    good = A.Animation(spec, "idle")
    try:
        good.set_action("nope")
        set_raised = None
    except Exception as exc:
        set_raised = exc
    check("set_action 给坏动作同样抛错", isinstance(set_raised, A.UnknownActionError),
          f"抛出 {type(set_raised).__name__}")

    # ------------------------------------------------------------ §7 dt 上限
    section("7. 调度：dt 上限（休眠唤醒不跳帧）")
    capped = A.Animation(spec, "idle")
    capped.advance(0.0)
    before = capped.index
    capped.advance(60_000.0)
    jump = (capped.index - before) % 5
    check(
        "dt=60 秒时最多补 MAX_DT_MS 的帧（不一次跳几百帧）",
        jump <= int(A.MAX_DT_MS // capped.frame_ms),
        f"前进了 {jump} 帧，上限 {int(A.MAX_DT_MS // capped.frame_ms)} 帧",
    )
    check(
        "dt 恰好一帧时长时只走一帧",
        (lambda a: (a.advance(33.0), a.index)[1])(A.Animation(spec, "idle")) == 1,
        "dt=33 ms → 第 1 帧",
    )
    check("MAX_DT_MS 是 100", A.MAX_DT_MS == 100.0, f"实际 {A.MAX_DT_MS}")

    # ------------------------------------------------------------ §8 回到素材
    section("8. 真实素材：帧率来自 asset.json，动作名与播放方式对得上")
    from deskpet import pet_loader

    real = pet_loader.resolve_pet(None, None).spec
    if real is None:
        check("解出默认素材包", False, "连默认形象都不可用，后续跳过")
        return _finish()

    check(
        "真实包的动作集合覆盖播放方式表",
        set(A.PLAY_MODES) <= set(real.action_order),
        f"包里有 {list(real.action_order)}",
    )
    idle = real.action("idle")
    check(
        "idle 的 frame_ms 来自 asset.json（30 fps → 33.33 ms）",
        idle.fps_source.value == "asset" and abs(idle.frame_ms - 1000.0 / 30.0) < 1e-9,
        f"来源 {idle.fps_source.value}，frame_ms={idle.frame_ms:.4f}",
    )

    # ------------------------------------------------------------ §9 改 fps
    section("9. 只改素材的 fps：24 变慢 / 60 变快 / 坏值回退 / 删掉回退")
    from deskpet import pet_spec

    slow = _make_spec_from("t05_slow", idle_fps=24)
    fast = _make_spec_from("t05_fast", idle_fps=60)
    check(
        "fps 改 24 → 约 41.7 ms/帧",
        abs(slow.action("idle").frame_ms - 1000.0 / 24) < 1e-9,
        f"frame_ms={slow.action('idle').frame_ms:.4f}（来源 {slow.action('idle').fps_source.value}）",
    )
    check(
        "fps 改 60 → 约 16.7 ms/帧",
        abs(fast.action("idle").frame_ms - 1000.0 / 60) < 1e-9,
        f"frame_ms={fast.action('idle').frame_ms:.4f}（来源 {fast.action('idle').fps_source.value}）",
    )
    check(
        "慢的确实比默认慢、快的确实比默认快",
        slow.action("idle").frame_ms > idle.frame_ms > fast.action("idle").frame_ms,
        f"24fps={slow.action('idle').frame_ms:.1f} > 30fps={idle.frame_ms:.1f} "
        f"> 60fps={fast.action('idle').frame_ms:.1f}",
    )

    dropped = _make_spec_from("t05_dropped", drop_idle_fps=True)
    dropped_action = dropped.action("idle")
    check(
        "删掉 fps → 回到遗留兜底（idle 8.0 → 125 ms），不崩",
        dropped_action.fps_source is pet_spec.FrameRateSource.LEGACY
        and abs(dropped_action.frame_ms - 125.0) < 1e-9,
        f"来源 {dropped_action.fps_source.value}，frame_ms={dropped_action.frame_ms:.1f}",
    )
    for bad_value in ("快", -1, True):
        broken = _make_spec_from(f"t05_bad_{abs(hash(repr(bad_value))) % 10000}", idle_fps=bad_value)
        broken_action = broken.action("idle")
        check(
            f"fps 写成 {bad_value!r} → 不崩、回退遗留兜底 8.0",
            broken_action.fps_source is pet_spec.FrameRateSource.LEGACY,
            f"来源 {broken_action.fps_source.value}，frame_ms={broken_action.frame_ms:.1f}",
        )

    # ------------------------------------------------------------ §10 窗口
    section("10. 窗口接入：换帧只更新该帧 + 定格时不再排下一次")
    window = PetWindow(real, 6)
    painted: list[tuple[str, int]] = []
    orig_show = window.show_action_frame

    def spy_show(action: str, index: int) -> bool:
        painted.append((action, index))
        return orig_show(action, index)

    window.show_action_frame = spy_show  # type: ignore[method-assign]
    window.play_action("waving", auto_start=False)

    check(
        "play_action 后窗口动作 == 动画动作",
        window.action == "waving" and window.animation.action == "waving",
        f"窗口 {window.action} / 动画 {window.animation.action}",
    )
    check(
        "窗口显示的帧号 == 动画帧号",
        window.frame_index == window.animation.index,
        f"窗口 {window.frame_index} / 动画 {window.animation.index}",
    )
    check("没有 start 时定时器不跑", not window._driver.running, "running=False")

    # 手动推 3 帧：每次只应该重绘 1 帧，且帧号跟着动画走。
    # **不要假设帧号是递增的**：`waving` 是倒着播的，断言只比"重绘的帧号
    # == 动画当时的帧号"。
    painted.clear()
    expected_frames: list[int] = []
    for _ in range(3):
        window.animation.advance()
        expected_frames.append(window.animation.index)
        window._on_frame_due()
    check(
        "手动推进 3 拍 → 恰好重绘 3 帧，且帧号与动画一致",
        [item[1] for item in painted] == expected_frames and len(painted) == 3,
        f"重绘 {[item[1] for item in painted]}，动画帧号 {expected_frames}",
    )

    # 同一帧重复通知不得重绘（单帧动作 / 区间只有一帧时会发生）。
    painted.clear()
    window._on_frame_due()
    check("同一帧再次通知 → 不重绘", painted == [], f"重绘 {painted}")

    window.hold_frame(3)
    check(
        "hold_frame(3)：画面钉在第 3 帧且定时器停住",
        window.frame_index == 3 and not window._driver.running,
        f"frame_index={window.frame_index} running={window._driver.running}",
    )
    painted.clear()
    window._on_frame_due()
    check("定格后不会自己换帧", window.frame_index == 3, f"frame_index={window.frame_index}")

    window.stop_animation()
    check("stop_animation() 后定时器已停", not window._driver.running, "running=False")

    check("动画定时器间隔上下限在 4..250 ms",
          window._driver.MIN_INTERVAL_MS == 4 and window._driver.MAX_INTERVAL_MS == 250,
          f"{window._driver.MIN_INTERVAL_MS}..{window._driver.MAX_INTERVAL_MS}")

    # ------------------------------------------------------------ §11 启动信息
    section("11. 启动行包含动作 + 每帧毫秒 + 帧率来源")
    import logging

    records: list[str] = []

    class Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record.getMessage())

    from deskpet import app as app_module

    handler = Capture()
    app_logger = logging.getLogger("deskpet.app")
    app_logger.addHandler(handler)
    previous_level = app_logger.level
    app_logger.setLevel(logging.INFO)
    try:
        app_module._print_startup_line(window, "按屏幕自适应")
    finally:
        app_logger.removeHandler(handler)
        app_logger.setLevel(previous_level)

    line = records[-1] if records else ""
    check(
        "启动行含动作名",
        "waving" in line,
        f"那一行：{line}",
    )
    check("启动行含 frame_ms", "frame_ms=" in line, f"那一行：{line}")
    check(
        "启动行含帧率来源",
        "fps_source=asset.json" in line,
        f"那一行：{line}",
    )

    # ------------------------------------------------------------ §12 CLI
    section("12. 命令行：--action 的缺省与坏值")
    from deskpet.app import DEFAULT_ACTION, _build_parser

    parser = _build_parser()
    parsed = parser.parse_args([])
    check("--action 缺省是 idle", parsed.action == DEFAULT_ACTION == "idle", f"实际 {parsed.action}")
    check(
        "--fps 已删除、不得复活",
        not any("--fps" in item for item in parser.format_help().split()),
        "--help 里搜不到 --fps",
    )
    parsed_bad = parser.parse_args(["--action", "abc"])
    check(
        "argparse 不拦坏动作（留给运行时如实报错 + 列可用动作）",
        parsed_bad.action == "abc",
        f"解析出 {parsed_bad.action!r}",
    )

    return _finish()


def _finish() -> int:
    _LINES.append("")
    if _FAILED:
        _LINES.append(f"失败 {_FAILED} 项 / 共 {_TOTAL} 项")
    else:
        _LINES.append(f"全过（共 {_TOTAL} 项）")
    try:
        RESULT_FILE.parent.mkdir(parents=True, exist_ok=True)
        RESULT_FILE.write_text("\n".join(_LINES) + "\n", encoding="utf-8")
        shown = str(RESULT_FILE)
    except OSError as exc:  # pragma: no cover
        shown = f"（结果写不出去：{exc}）"
    print(_LINES[-1])
    print(f"结果已写入：{shown}")
    return 1 if _FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
