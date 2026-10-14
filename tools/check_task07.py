"""task07 自检：久置行为（张望 / 休息 / 站起来）+ 光标轮询 + manual 模式。

按 agent.md §A.3 只写**一个**脚本、共用一个 `check()`、按节输出。
结果**写文件**（`data/task07_check.txt`，UTF-8），不靠重定向（§4.3）。

跑法（必须带 UTF-8 环境变量，否则中文输出在 GBK 控制台会炸，§4.11）：

    python tools/check_task07.py

**时间由假时钟喂进去**（`IdleStateMachine` 的时间全部从外面传），
所以"静置 20 秒 → 张望 → 再 10 秒 → 休息"这条时间线是**毫秒级**跑完的，
不用真的等 30 秒。真实节奏另由 `tools/check_task07_real.py` 在真平台上量。

**窗口用例走 offscreen 平台插件**。offscreen **不支持 `setMask`**（§4.9），
本脚本不断言"遮罩生效"，只断言动作 / 帧 / 信号这些与平台无关的事实。
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
RESULT_FILE = ROOT / "data" / "task07_check.txt"

#: 自检自己造东西的地方（**不碰 `assets/`、不碰用户真实目录**）。
SCRATCH = ROOT / "data" / "task07_scratch"

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


def _write_config(path: Path, payload: object, *, raw_text: str | None = None) -> None:
    if raw_text is not None:
        path.write_text(raw_text, encoding="utf-8")
        return
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _code_only(path: Path) -> str:
    """只保留**可执行代码**：把注释与字符串去掉。

    ⚠ 直接对整个文件做 `in` 判断会把**注释与文档字符串里提到**的名字
    当成"在调用它"（task06 就因此误判过一次 `mirrored()`）。
    这些源码层面的断言必须只看代码。
    """
    import io
    import tokenize

    source = path.read_text(encoding="utf-8")
    pieces: list[str] = []
    try:
        for token in tokenize.generate_tokens(io.StringIO(source).readline):
            if token.type in (tokenize.COMMENT, tokenize.STRING):
                continue
            pieces.append(token.string)
    except tokenize.TokenError:  # pragma: no cover - 源码总是可解析的
        return source
    return " ".join(pieces)


class _FakeClock:
    """可推进的假单调时钟（**只替换"时间从哪来"**，不动被测逻辑）。

    用它是因为窗口这条路径上的调用（`on_mouse_moved()` / 双击）**不传时间**，
    状态机会去读自己的时钟；本测试的时间线必须让两者读到同一个数。
    """

    def __init__(self, start: float = 0.0) -> None:
        self.value = float(start)

    def monotonic(self) -> float:
        return self.value

    def advance(self, seconds: float) -> float:
        self.value += float(seconds)
        return self.value


def _load_spec():
    """自带包的 `PetSpec`（**只读**，一个字节都不改）。"""
    from deskpet import pet_loader

    return pet_loader.load_pet(ROOT / "assets" / "weedy")


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

    app = QApplication.instance() or QApplication([])

    _check_constants()
    _check_idle_seconds_reading()
    _check_auto_timeline()
    _check_mouse_wakes_up()
    _check_manual_mode()
    _check_suspended_hook()
    _check_cursor_watch()
    _check_window_wiring(app)
    _check_mirror_memory(app)
    _check_teardown(app)
    return _finish()


# ------------------------------------------------------------ §1 常量与真值


def _check_constants() -> None:
    from deskpet import idle_state as IS

    section("1. 常量与真值（逐条对齐 task07 §4）")

    check("MOUSE_MOVE_EPSILON = 2 px", IS.MOUSE_MOVE_EPSILON == 2, f"{IS.MOUSE_MOVE_EPSILON}")
    check(
        "比较用的是位移**平方**",
        IS.MOUSE_MOVE_EPSILON_SQ == 4,
        f"阈值 {IS.MOUSE_MOVE_EPSILON_SQ} = 2²",
    )
    check("REST_MIN_S = 5 秒", IS.REST_MIN_S == 5.0, f"{IS.REST_MIN_S}")
    check("REST_RATIO = 0.5", IS.REST_RATIO == 0.5, f"{IS.REST_RATIO}")
    check(
        "S = max(5, T × 0.5)：T=20 → 10",
        IS.rest_wait_seconds(20) == 10.0,
        f"{IS.rest_wait_seconds(20)}",
    )
    check(
        "S 的下限生效：T=5 → 5（而不是 2.5）",
        IS.rest_wait_seconds(5) == 5.0,
        f"{IS.rest_wait_seconds(5)}",
    )
    check(
        "阶段名与动作名同形（idle / waiting / rest）",
        tuple(stage.value for stage in IS.Stage) == ("idle", "waiting", "rest"),
        f"{[s.value for s in IS.Stage]}",
    )
    check(
        "阶段常量本身就是动作名来源（没有第二张映射表）",
        IS.Stage.IDLE.action == "idle"
        and IS.Stage.WAITING.action == "waiting"
        and IS.Stage.REST.action == "rest",
        "action 属性 = value",
    )

    # 状态机**不许依赖 Qt 窗口**（"纯逻辑、可脱离窗口测"）。
    source = _code_only(ROOT / "src" / "deskpet" / "idle_state.py")
    check(
        "idle_state 不 import 任何 QtWidgets / QtGui（纯逻辑）",
        "QtWidgets" not in source and "QtGui" not in source,
        "代码里搜不到 QtWidgets / QtGui",
    )
    check(
        "idle_state 不自己排定时器（时间由外部喂）",
        "QTimer" not in source,
        "代码里搜不到 QTimer",
    )
    check(
        "计时用单调时钟（不用 time.time，系统改表不会让计时跳）",
        "monotonic" in source and "time.time" not in source,
        "代码里用 time.monotonic",
    )


# ------------------------------------------------------------ §2 读配置


def _check_idle_seconds_reading() -> None:
    from deskpet import idle_state as IS

    section("2. idle_seconds 的读法：现读 / 兜底 / 下限夹取")

    check("字符串 → 兜底 20 秒", IS.normalize_idle_seconds("很久") == 20.0)
    check("None → 兜底 20 秒", IS.normalize_idle_seconds(None) == 20.0)
    check("bool 必须挡掉（True 不是 1 秒）", IS.normalize_idle_seconds(True) == 20.0)
    check("NaN → 兜底 20 秒", IS.normalize_idle_seconds(float("nan")) == 20.0)
    check("+inf → 兜底 20 秒", IS.normalize_idle_seconds(float("inf")) == 20.0)
    check("-inf → 兜底 20 秒", IS.normalize_idle_seconds(float("-inf")) == 20.0)
    check("0 → 下限夹到 1 秒（不是回退 20）", IS.normalize_idle_seconds(0) == 1.0)
    check("-10 → 下限夹到 1 秒", IS.normalize_idle_seconds(-10) == 1.0)
    check("5 → 原样 5 秒", IS.normalize_idle_seconds(5) == 5.0)
    check("300 → 原样 300 秒", IS.normalize_idle_seconds(300) == 300.0)

    # 构造时**不快照**：改了配置，同一个状态机立刻用新值。
    box = {"value": 20}
    machine = IS.IdleStateMachine(lambda: box["value"])
    check("构造后读到 20", machine.idle_seconds() == 20.0, f"{machine.idle_seconds()}")
    box["value"] = 5
    check(
        "改配置后立刻生效（没有在构造时快照）",
        machine.idle_seconds() == 5.0 and machine.rest_seconds() == 5.0,
        f"T={machine.idle_seconds()} S={machine.rest_seconds()}",
    )
    box["value"] = 300
    check("再改成 300 也立刻生效", machine.idle_seconds() == 300.0)
    box["value"] = "很久"
    check("配置坏掉时兜底 20（不崩）", machine.idle_seconds() == 20.0)


# ------------------------------------------------------------ §3 auto 时间线


def _check_auto_timeline() -> None:
    from deskpet import idle_state as IS

    section("3. auto 模式时间线：静置 T → 张望，再静置 S → 休息")

    def timeline(idle_seconds: float, tick_ms: float = 150.0):
        """按固定 tick 喂假时间，返回每次阶段变化的时刻。"""
        machine = IS.IdleStateMachine(lambda: idle_seconds)
        changes = []
        machine.stage_changed.connect(
            lambda old, new: changes.append((new.value, now[0]))
        )
        now = [0.0]
        for _ in range(20000):
            now[0] += tick_ms / 1000.0
            machine.on_tick(now[0])
            if len(changes) >= 2:
                break
        return machine, changes

    machine, changes = timeline(20)
    check(
        "T=20：先张望（waiting）",
        changes and changes[0][0] == "waiting",
        f"实测 {changes}",
    )
    check(
        "T=20：约 20 秒后张望（容差两个 tick）",
        changes and 20.0 <= changes[0][1] <= 20.3,
        f"实测 {changes[0][1]:.2f} 秒" if changes else "没有变化",
    )
    check(
        "T=20：再过约 10 秒休息（S = max(5, 10) = 10）",
        len(changes) > 1 and 9.9 <= changes[1][1] - changes[0][1] <= 10.3,
        f"实测间隔 {changes[1][1] - changes[0][1]:.2f} 秒" if len(changes) > 1 else "没有第二次变化",
    )
    check("休息后停在 rest", machine.stage is IS.Stage.REST, machine.stage.value)

    # T=5：S = max(5, 2.5) = 5，下限生效。
    _, changes5 = timeline(5)
    check(
        "T=5：约 5 秒后张望",
        changes5 and 5.0 <= changes5[0][1] <= 5.3,
        f"实测 {changes5[0][1]:.2f} 秒" if changes5 else "没有变化",
    )
    check(
        "T=5：再过约 5 秒休息（下限生效，不是 2.5）",
        len(changes5) > 1 and 4.9 <= changes5[1][1] - changes5[0][1] <= 5.3,
        f"实测间隔 {changes5[1][1] - changes5[0][1]:.2f} 秒" if len(changes5) > 1 else "没有第二次变化",
    )

    # T=300：不该很快张望（证明读的是配置值而不是写死的 20）。
    machine300 = IS.IdleStateMachine(lambda: 300)
    now = 0.0
    for _ in range(200):  # 喂 30 秒
        now += 0.15
        machine300.on_tick(now)
    check(
        "T=300：30 秒内仍然 idle（读的是配置值，不是写死的 20）",
        machine300.stage is IS.Stage.IDLE,
        f"30 秒后是 {machine300.stage.value}",
    )
    check(
        "T=300：剩余时间约 270 秒",
        abs(machine300.remaining_seconds(now) - 270.0) < 0.3,
        f"剩余 {machine300.remaining_seconds(now):.2f} 秒",
    )


# ------------------------------------------------------------ §4 鼠标动了


def _check_mouse_wakes_up() -> None:
    from deskpet import idle_state as IS

    section("4. 鼠标动了：站起来 + 重新计时（含两个例外）")

    machine = IS.IdleStateMachine(lambda: 20)
    changes = []
    resets = []
    machine.stage_changed.connect(lambda old, new: changes.append(new.value))
    machine.timer_reset.connect(lambda: resets.append(1))

    now = 0.0
    for _ in range(300):
        now += 0.15
        machine.on_tick(now)
        if machine.stage is IS.Stage.REST:
            break
    check("先静置到 rest", machine.stage is IS.Stage.REST, machine.stage.value)

    machine.on_mouse_moved(now)
    check(
        "rest 中鼠标动 → 立刻回 idle（站起来）",
        machine.stage is IS.Stage.IDLE,
        machine.stage.value,
    )
    check(
        "站起来后重新计时 T（不是接着上一次的进度）",
        abs(machine.remaining_seconds(now) - 20.0) < 0.01,
        f"剩余 {machine.remaining_seconds(now):.3f} 秒",
    )

    # idle 里动鼠标：只归零、不切动作（timer_reset 与 stage_changed 分开）。
    before = list(changes)
    before_resets = len(resets)
    now += 5.0
    machine.on_tick(now)
    machine.on_mouse_moved(now)
    check(
        "idle 中鼠标动 → 不发 stage_changed（只归零）",
        changes == before,
        f"stage_changed 序列不变：{changes}",
    )
    check(
        "idle 中鼠标动 → 发 timer_reset",
        len(resets) > before_resets,
        f"timer_reset 次数 {before_resets} → {len(resets)}",
    )
    check(
        "归零是实打实的：剩余时间回到 T",
        abs(machine.remaining_seconds(now) - 20.0) < 0.01,
        f"剩余 {machine.remaining_seconds(now):.3f} 秒",
    )

    # waiting 中途动了也要站起来。
    now += 21.0
    machine.on_tick(now)
    check("静置够 T → waiting", machine.stage is IS.Stage.WAITING, machine.stage.value)
    now += 3.0
    machine.on_mouse_moved(now)
    check(
        "waiting 中鼠标动 → 也回 idle（不必等到休息）",
        machine.stage is IS.Stage.IDLE,
        machine.stage.value,
    )


# ------------------------------------------------------------ §5 manual


def _check_manual_mode() -> None:
    from deskpet import idle_state as IS

    section("5. manual 模式：怎么动、等多久都不影响它")

    machine = IS.IdleStateMachine(lambda: 20)
    changes = []
    machine.stage_changed.connect(lambda old, new: changes.append(new.value))
    now = 0.0

    check("进入 manual 前不是 manual", machine.manual is False)
    machine.enter_manual(now)
    check("enter_manual → rest", machine.stage is IS.Stage.REST, machine.stage.value)
    check("manual 标志打开", machine.manual is True)
    check("manual 中没有下一次自动升级", machine.next_change_at is None)

    # 鼠标乱动 + 等 5 分钟，都不许把它拽起来。
    for step in range(2000):
        now += 0.15
        machine.on_tick(now)
        if step % 7 == 0:
            machine.on_mouse_moved(now)
    check(
        "manual 中鼠标乱动 + 等 5 分钟仍保持 rest（例外的第 ① 条）",
        machine.stage is IS.Stage.REST,
        f"300 秒后是 {machine.stage.value}，stage_changed={changes}",
    )
    check(
        "manual 期间没有发生过 auto 阶段变化",
        "waiting" not in changes,
        f"stage_changed 序列 {changes}",
    )

    # 退出 manual → 站起来 + 重新计时。
    now += 1.0
    exited = machine.exit_manual(now)
    check("exit_manual 报告阶段变了", exited is True)
    check("退出 manual → idle", machine.stage is IS.Stage.IDLE, machine.stage.value)
    check("manual 标志关掉", machine.manual is False)
    check(
        "退出 manual 后重新计时 T",
        abs(machine.remaining_seconds(now) - 20.0) < 0.01,
        f"剩余 {machine.remaining_seconds(now):.3f} 秒",
    )
    check("exit_manual 幂等（再调一次返回 False）", machine.exit_manual(now) is False)

    # auto 休息中进入 manual：阶段已经是 rest，但 manual 开关必须打开。
    machine2 = IS.IdleStateMachine(lambda: 20)
    now2 = 0.0
    for _ in range(300):
        now2 += 0.15
        machine2.on_tick(now2)
        if machine2.stage is IS.Stage.REST:
            break
    changed = machine2.enter_manual(now2)
    check(
        "auto 休息中进 manual：阶段不变但 manual 打开",
        changed is False and machine2.manual is True and machine2.stage is IS.Stage.REST,
        f"changed={changed} manual={machine2.manual}",
    )
    now2 += 600.0
    machine2.on_tick(now2)
    check("它也不会被自动计时改回来", machine2.stage is IS.Stage.REST, machine2.stage.value)


# ------------------------------------------------------------ §6 挂起接口


def _check_suspended_hook() -> None:
    from deskpet import idle_state as IS

    section("6. 挂起接口（例外的第 ② 条，task08 的接线点）")

    machine = IS.IdleStateMachine(lambda: 20)
    changes = []
    resets = []
    machine.stage_changed.connect(lambda old, new: changes.append(new.value))
    machine.timer_reset.connect(lambda: resets.append(1))
    now = 0.0

    check("默认不挂起", machine.suspended is False)
    machine.set_suspended(True, now)
    check("set_suspended(True) 生效", machine.suspended is True)
    check("挂起时计时归零并通知（timer_reset）", len(resets) >= 1, f"resets={len(resets)}")
    check("挂起时没有下一次自动升级", machine.next_change_at is None)

    now += 100.0
    machine.on_tick(now)
    check(
        "挂起中静置 100 秒也**不切动作**（计时归零但不升级）",
        machine.stage is IS.Stage.IDLE and changes == [],
        f"阶段 {machine.stage.value}，stage_changed={changes}",
    )

    machine.on_mouse_moved(now)
    check(
        "挂起中鼠标动也不切动作",
        changes == [],
        f"stage_changed={changes}",
    )

    machine.set_suspended(False, now)
    check("恢复后从零重新计时", abs(machine.remaining_seconds(now) - 20.0) < 0.01,
          f"剩余 {machine.remaining_seconds(now):.3f} 秒")
    now += 20.2
    machine.on_tick(now)
    check(
        "恢复后照常自动张望（挂起只是暂停，不是关掉）",
        machine.stage is IS.Stage.WAITING,
        machine.stage.value,
    )

    machine.set_suspended(True, now)
    now += 5.0
    machine.enter_manual(now)
    check("挂起 + manual 同时存在时互不干扰", machine.stage is IS.Stage.REST, machine.stage.value)
    machine.set_suspended(False, now)
    check(
        "退出挂起时 manual 仍然拦着自动升级",
        machine.next_change_at is None,
        f"next_change_at={machine.next_change_at}",
    )


# ------------------------------------------------------------ §7 光标轮询


def _check_cursor_watch() -> None:
    from PySide6.QtCore import QPoint

    from deskpet import cursor_watch as CW

    section("7. 光标监视：阈值 / 首帧 / 起停")

    check("默认轮询间隔 150 ms", CW.DEFAULT_INTERVAL_MS == 150, f"{CW.DEFAULT_INTERVAL_MS}")

    watch = CW.CursorWatch(150)
    check("间隔可读", watch.interval_ms == 150, f"{watch.interval_ms}")

    # 用假光标替掉 QCursor.pos（**只换读数来源，不换被测逻辑**）。
    fake = {"pos": QPoint(500, 500)}
    original = CW.QCursor

    class FakeCursor:
        @staticmethod
        def pos():
            return fake["pos"]

    CW.QCursor = FakeCursor
    try:
        seen = []
        watch.moved.connect(lambda: seen.append(1))

        check("第一次读光标按『没动』处理（首帧）", watch.poll_once() is False, f"moved={len(seen)}")
        check("首帧之后记下了位置", watch.last_pos is not None, f"{watch.last_pos}")

        fake["pos"] = QPoint(501, 500)  # 1 px
        check("1 px 抖动不算动", watch.poll_once() is False, f"moved={len(seen)}")

        fake["pos"] = QPoint(501, 501)  # 相对上一次 (501,500)：平方和 = 1
        check("斜向 1 px（平方和 1 ≤ 2²）不算动", watch.poll_once() is False, f"moved={len(seen)}")

        fake["pos"] = QPoint(502, 502)  # 相对上一次 (501,501)：仍然是 1 px
        check("每次只挪 1 px 时逐次都不算动", watch.poll_once() is False, f"moved={len(seen)}")

        fake["pos"] = QPoint(505, 505)  # 相对上一次：3 px 每轴，平方和 18 > 4
        check("相对上一次位移超过阈值 → 算动", watch.poll_once() is True, f"moved={len(seen)}")

        fake["pos"] = QPoint(500, 500)  # 又动回来
        check("反方向移动也算动", watch.poll_once() is True, f"moved={len(seen)}")

        fake["pos"] = QPoint(500, 500)
        check("停在原地不算动", watch.poll_once() is False, f"moved={len(seen)}")
        check("moved 信号确实发了 2 次", len(seen) == 2, f"{len(seen)}")

        # reset 之后重新按首帧处理。
        watch.reset()
        check("reset 后忘掉上次位置", watch.last_pos is None)
        check("reset 之后第一次读数不算动", watch.poll_once() is False, f"moved={len(seen)}")

        # 读不到光标时不许抛。
        class BrokenCursor:
            @staticmethod
            def pos():
                raise RuntimeError("平台不给")

        CW.QCursor = BrokenCursor
        check("读光标失败时不抛，按没动处理", watch.poll_once() is False)
    finally:
        CW.QCursor = original

    check("没 start 时不在跑", watch.running is False)
    watch.start()
    check("start 后 running", watch.running is True)
    watch.stop()
    check("stop 后不 running（退出无残留定时器）", watch.running is False)

    # 光标监视**不依赖窗口事件**（全屏鼠标活动没有信号可订阅，agent.md §4.8）。
    source = _code_only(ROOT / "src" / "deskpet" / "cursor_watch.py")
    check(
        "不依赖窗口的 enter/leaveEvent",
        "enterEvent" not in source and "leaveEvent" not in source,
        "代码里搜不到 enterEvent / leaveEvent",
    )
    check(
        "用的是全局 QCursor.pos()",
        "QCursor" in source and "pos" in source,
        "代码里调用 QCursor.pos()",
    )


# ------------------------------------------------------------ §8 窗口接线


def _check_window_wiring(app) -> None:
    from deskpet import idle_state as IS
    from deskpet.pet_window import PetWindow

    section("8. 窗口接线：谁发出输入、谁响应 stage_changed")

    spec = _load_spec()
    machine = IS.IdleStateMachine(lambda: 20)
    window = PetWindow(spec, 6, idle_state=machine)

    check("窗口拿到了状态机", window.idle_state is machine)
    check("窗口起始动作是 idle", window.action == "idle", window.action)

    # stage_changed → 切到同名动作。
    # ⚠ 必须**从小往大推进**：状态机的"当前时刻"在上限夹取里是单调的，
    # 而第一个期限是"第一次被问到时刻 + T"——一上来就喂一个超过期限的时刻，
    # 期限反而是刚刚才钉下的，于是这一拍不会升级。
    now = 0.0
    machine.on_tick(now)  # 起点：把第一个期限钉在 20 秒后
    for _ in range(200):
        now += 0.15
        machine.on_tick(now)
        if window.action != "idle":
            break
    check(
        "状态机进 waiting → 窗口切到 waiting 动作",
        window.action == "waiting",
        f"窗口动作 {window.action}（{now:.2f} 秒）",
    )
    for _ in range(200):
        now += 0.15
        machine.on_tick(now)
        if window.action == "rest":
            break
    check(
        "状态机进 rest → 窗口切到 rest 动作",
        window.action == "rest",
        f"窗口动作 {window.action}（{now:.2f} 秒）",
    )

    # 鼠标动了 → 站起来。
    window.on_mouse_moved()
    check(
        "on_mouse_moved → 窗口回 idle",
        window.action == "idle",
        f"窗口动作 {window.action}",
    )

    # 菜单文字跟着 manual 走。
    check("未 manual 时菜单文字是『坐下休息』", window.menu_state().manual_rest is False)
    window.toggle_rest()
    check("点菜单 → manual 打开", window.manual_rest is True)
    check("点菜单 → 窗口在 rest", window.action == "rest", window.action)
    check("manual 时菜单文字变成『站起来』", window.menu_state().manual_rest is True)

    # manual 期间鼠标乱动 + 久置都不许把它拽起来。
    for _ in range(400):
        now += 0.15
        machine.on_tick(now)
        window.on_mouse_moved()
    check(
        "manual 期间鼠标乱动 + 久置：窗口仍是 rest",
        window.action == "rest" and window.manual_rest is True,
        f"动作 {window.action}，manual={window.manual_rest}",
    )

    # 双击退出 manual（本 task 只接"退出 manual"，挥手属 task10）。
    from PySide6.QtCore import QPoint, QPointF, Qt
    from PySide6.QtGui import QMouseEvent

    event = QMouseEvent(
        QMouseEvent.Type.MouseButtonDblClick,
        QPointF(5.0, 5.0),
        QPointF(5.0, 5.0),
        Qt.LeftButton,
        Qt.LeftButton,
        Qt.NoModifier,
    )
    window.mouseDoubleClickEvent(event)
    check(
        "manual 休息中双击宠物 → 退出 manual",
        window.manual_rest is False,
        f"manual={window.manual_rest}",
    )
    # ⚠ task10 起双击会**挥手**，所以这里不再是"停在 idle"：双击先退出 manual，
    #    紧接着手势接管动作（`wave_once()` → `waving`）。task07 当年那句
    #    "挥手属 task10" 说的就是这一条，现在由 task10 补上了。
    check(
        "双击后窗口不再保持休息姿势（task10 起改为进入挥手手势）",
        window.action in ("idle", "waving"),
        f"窗口动作 {window.action}",
    )
    # ⚠ 这里**不能**拿本测试的假 `now` 去比：双击走的是窗口 → 状态机的真实调用路径，
    # 那条路径不传 `now`，于是状态机读的是真实单调时钟（正确行为）。
    # 能断言的是"期限被重新排成了 T 那么远"，与它用的是哪个时钟无关。
    remaining = machine.remaining_seconds()
    check(
        "双击后重新计时（回到 auto 模式，期限重排为 T）",
        remaining is not None and abs(remaining - 20.0) < 0.05,
        f"剩余 {remaining:.3f} 秒",
    )

    # 不在 manual 里时双击 → **task10 起会挥手**（task07 当年留的"什么都不做"
    # 已被 task10 的手势取代）。这里断言的是"manual 状态没被误改"这一件事，
    # 手势本身归 task10 自检（`tools/check_task10.py` §6）。
    before_action = window.action
    window.mouseDoubleClickEvent(event)
    check(
        "不在 manual 里双击不会改 manual（手势由 task10 处理）",
        window.manual_rest is False,
        f"动作 {window.action}（进入前 {before_action}）",
    )

    # 再点一次菜单 → 站起来回 auto。
    window.toggle_rest()
    check("再点菜单进入 manual", window.manual_rest is True and window.action == "rest")
    window.toggle_rest()
    check(
        "manual 中再点菜单 → 站起回 auto",
        window.manual_rest is False and window.action == "idle",
        f"manual={window.manual_rest} 动作={window.action}",
    )

    # 挂起接口经窗口转发。
    window.set_action_suspended(True)
    check("窗口把挂起转给状态机", machine.suspended is True)
    now += 100.0
    machine.on_tick(now)
    check(
        "挂起中窗口动作不变（例外的第 ② 条接线点生效）",
        window.action == "idle",
        window.action,
    )
    window.set_action_suspended(False)
    check("取消挂起", machine.suspended is False)

    window.stop_animation()
    window.close()
    del app


# ------------------------------------------------------------ §9 镜像记忆


def _check_mirror_memory(app) -> None:
    from deskpet import config as C
    from deskpet.config import ConfigStore
    from deskpet.idle_state import IdleStateMachine
    from deskpet.mirror import MirrorState
    from deskpet.pet_window import PetWindow

    section("9. 静置到 waiting / rest 时，镜像记忆生效")

    cfg = _fresh_dir(SCRATCH / "mirror") / "config.json"
    _write_config(cfg, {})
    store = ConfigStore(cfg)
    store.load()
    mirror = MirrorState(store)

    spec = _load_spec()
    machine = IdleStateMachine(lambda: 20)
    window = PetWindow(spec, 6, mirror=mirror, idle_state=machine)

    # ⚠ 本节的推进**经过窗口的真实调用路径**（`on_mouse_moved()` / 双击都不传 `now`），
    # 那条路径读的是状态机自己的时钟。要让假时间线成立，就把时钟整个换掉
    # ——只替换"时间从哪来"，被测逻辑一行都不动。
    clock = _FakeClock()
    machine._clock = clock.monotonic

    # 先在 idle 上翻开镜像。
    check("idle 的镜像默认关着", window.mirrored is False)
    check("在 idle 上翻转成功", window.toggle_mirror() is True)
    check("idle 现在是翻的", window.mirrored is True, f"mirrored={window.mirrored}")

    # 静置到 rest：rest 没翻过 → 不翻。
    for _ in range(400):
        clock.advance(0.15)
        machine.on_tick()
        if window.action == "rest":
            break
    check("已到 rest", window.action == "rest", f"{window.action}（{clock.value:.2f} 秒）")
    check(
        "只翻过 idle 时，rest **不翻**（按状态分别记忆）",
        window.mirrored is False,
        f"rest mirrored={window.mirrored}，配置 {store.get('mirror')!r}",
    )
    check(
        "配置里只记了 idle 那一项",
        store.get("mirror") == {"idle": True},
        f"实际 {store.get('mirror')!r}",
    )

    # 把 rest 也翻上 → 立刻是翻的。
    check("在 rest 上翻转成功", window.toggle_mirror() is True)
    check(
        "rest 翻开后画面确实是翻的",
        window.mirrored is True,
        f"mirrored={window.mirrored}，配置 {store.get('mirror')!r}",
    )
    check(
        "配置里两项各自独立",
        store.get("mirror") == {"idle": True, "rest": True},
        f"实际 {store.get('mirror')!r}",
    )

    # 切回 idle：沿用 idle 自己的记忆（true）。
    window.on_mouse_moved()
    check("站起来回 idle", window.action == "idle", window.action)
    check("idle 沿用自己那份记忆（仍是翻的）", window.mirrored is True)

    # waiting 从没翻过 → 不翻；翻开之后再进 waiting 就是翻的。
    for _ in range(200):
        clock.advance(0.15)
        machine.on_tick()
        if window.action == "waiting":
            break
    check("进 waiting", window.action == "waiting", f"{window.action}（{clock.value:.2f} 秒）")
    check("waiting 没翻过 → 不翻", window.mirrored is False, f"{window.mirrored}")
    check("在 waiting 上翻转成功", window.toggle_mirror() is True)
    check(
        "waiting 适用范围成立（在 MIRROR_STATES 里）",
        "waiting" in C.MIRROR_STATES,
        f"适用范围 {C.MIRROR_STATES}",
    )
    window.on_mouse_moved()
    for _ in range(200):
        clock.advance(0.15)
        machine.on_tick()
        if window.action == "waiting":
            break
    check("再进 waiting：用的是刚记下的翻转", window.action == "waiting" and window.mirrored is True,
          f"动作={window.action} mirrored={window.mirrored}")

    window.stop_animation()
    window.close()
    del app


# ------------------------------------------------------------ §10 退出收尾


def _check_teardown(app) -> None:
    from deskpet import cursor_watch as CW

    section("10. 退出收尾：光标轮询确实停掉")

    watch = CW.CursorWatch(150)
    watch.start()
    check("启动后轮询在跑", watch.running is True)
    watch.stop()
    check("停掉后 `running` 为假", watch.running is False)

    # `app.py` 的退出路径必须显式停掉它（源码层面核对，真平台另测残留进程）。
    source = _code_only(ROOT / "src" / "deskpet" / "app.py")
    check(
        "app.py 的 finally 里停掉了光标轮询",
        "cursor_watch" in source and "stop" in source,
        "代码里有 cursor_watch.stop()",
    )
    check(
        "app.py 的 finally 里停掉了久置计时器",
        "idle_timer" in source and "stop" in source,
        "代码里有 idle_timer.stop()",
    )
    check(
        "计时挂在独立 QTimer 上，不挂在动画帧回调上（agent.md §4.8）",
        "idle_state" in source and "on_tick" in source,
        "idle_timer.timeout → idle_state.on_tick",
    )
    del app


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
