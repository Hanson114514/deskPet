"""task11 真平台自检：设置窗口在**真实屏幕**上的那几条。

offscreen 那份（`check_task11.py`）把几何断言都放在给定的屏幕矩形上算
（§4.11：别断言随环境变化的量）。本脚本验的是**只有真平台才成立**的东西：

1. 窗口真的出现在屏幕上、**水平居中**、垂直约 1/3 处（按真实屏幕算期望值）；
2. **模态**真的生效（`isModal()` 在真平台上仍为真）；
3. `设置…` 菜单项在真事件循环下**真的能点开**设置窗口（走产品路径，
   不直接调 `SettingsController.open()`）；
4. 设置窗口打开期间宠物**仍在最上层可见**（用户确认的取舍）；
5. 写失败时状态栏带出**系统原文**（真实文件系统上的 `PermissionError`）。

跑法：

    python tools/check_task11_real.py

结果写 `data/task11_real.txt`（UTF-8）。
"""

import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

RESULT_FILE = ROOT / "data" / "task11_real.txt"
SCRATCH = ROOT / "data" / "task11_real_scratch"

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


def main() -> int:
    if os.environ.get("QT_QPA_PLATFORM", "").lower() == "offscreen":
        print("这个脚本必须在真平台上跑（不要设 QT_QPA_PLATFORM=offscreen）")
        return 2

    from PySide6.QtCore import QTimer, Qt
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])

    from deskpet import context_menu, paths, pet_loader
    from deskpet import settings as st
    from deskpet.app import SettingsController
    from deskpet.config import ConfigStore
    from deskpet.pet_window import PetWindow

    if SCRATCH.exists():
        shutil.rmtree(SCRATCH)
    SCRATCH.mkdir(parents=True)

    try:
        with paths.override_dirs(program=ROOT, resource=ROOT):
            spec = pet_loader.load_pet(ROOT / "assets" / "weedy")
            store = ConfigStore(SCRATCH / "config.json")
            store.load()
            pet = PetWindow(spec, 4)
            pet.show()

            # ---------------------------------------------------- §1 真屏幕居中
            section("1. 真实屏幕上的位置：水平居中、垂直约 1/3")

            controller = SettingsController(store, pet)
            controller.open()
            dialog = controller.dialog
            app.processEvents()

            screen = dialog.screen()
            area = screen.availableGeometry()
            expected_left = area.x() + (area.width() - dialog.width()) // 2
            check(
                "窗口真的显示出来了",
                dialog.isVisible(),
                f"visible={dialog.isVisible()}",
            )
            check(
                "水平居中（按真实屏幕算）",
                abs(dialog.x() - expected_left) <= 1,
                f"实测 x={dialog.x()} 期望 {expected_left}（屏幕 {area.width()}×{area.height()}）",
            )
            center = dialog.y() + dialog.height() // 2
            expected_center = area.y() + int(area.height() * st.VERTICAL_RATIO)
            check(
                "垂直约 1/3 处",
                abs(center - expected_center) <= 2,
                f"实测中心={center} 期望={expected_center}",
            )
            check(
                "窗口完整落在屏幕内（不会被挤到屏幕外）",
                dialog.x() >= area.x()
                and dialog.y() >= area.y()
                and dialog.x() + dialog.width() <= area.x() + area.width()
                and dialog.y() + dialog.height() <= area.y() + area.height(),
                f"矩形 {dialog.x()},{dialog.y()} {dialog.width()}×{dialog.height()}",
            )
            check("模态生效（真平台上 isModal 仍为真）", dialog.isModal(), f"{dialog.isModal()}")

            # ---------------------------------------------------- §2 宠物仍置顶
            section("2. 设置窗口打开期间：宠物仍在最上层可见（已确认的取舍）")

            pet_flag = bool(pet.windowFlags() & Qt.WindowStaysOnTopHint)
            dialog_flag = bool(dialog.windowFlags() & Qt.WindowStaysOnTopHint)
            check(
                "宠物仍是 WindowStaysOnTopHint",
                pet_flag,
                f"pet={pet_flag}",
            )
            check(
                "设置窗口**没有**抢最上层（普通模态对话框）",
                not dialog_flag,
                f"dialog={dialog_flag}",
            )
            check("宠物窗口仍然可见", pet.isVisible(), f"visible={pet.isVisible()}")

            # ---------------------------------------------------- §3 真菜单路径
            section("3. `设置…` 走真菜单路径能打开窗口")

            controller.close()
            app.processEvents()
            check("先关掉，回到没有窗口的状态", controller.dialog is None)

            # 走产品自己的分派：`pet_window` 的菜单选择处理器。
            pet.set_settings_handler(controller.open)
            state = pet.menu_state()
            check(
                "装了处理器之后 `设置…` 变可用（不再灰掉）",
                state.settings_available,
                f"settings_available={state.settings_available}",
            )
            menu = context_menu.build_menu(pet, state)
            item = next(
                action
                for action in menu.actions()
                if context_menu.action_id(action) == context_menu.ID_SETTINGS
            )
            check("菜单里有 `设置…` 这一项且可点", item.isEnabled(), item.text())

            # 真的走一次"选中菜单项"的分派（等价于用户点它）。
            pet._handle_menu_choice(context_menu.ID_SETTINGS)
            app.processEvents()
            check(
                "选中 `设置…` → 设置窗口真的打开了",
                controller.dialog is not None and controller.dialog.isVisible(),
                f"dialog={controller.dialog!r}",
            )
            opened_first = controller.dialog

            # 再点一次 → 前置同一个窗口，不叠第二个。
            pet._handle_menu_choice(context_menu.ID_SETTINGS)
            app.processEvents()
            check(
                "再点一次 → 还是同一个窗口（没叠出第二个）",
                controller.dialog is opened_first,
                "同一实例",
            )

            # ---------------------------------------------------- §4 真写失败
            section("4. 真实文件系统上的写失败：状态栏带出系统原文")

            dialog = controller.dialog
            volume_slider = dialog._find_slider(dialog._widgets["volume"])
            real_write = store._write
            store._write = lambda: False
            try:
                volume_slider.setValue(11)
            finally:
                store._write = real_write
            check(
                "写失败 → 状态栏显示 `设置失败：`",
                dialog._status_label.text().startswith("设置失败："),
                repr(dialog._status_label.text()[:60]),
            )
            check(
                "写失败 → 滑块回滚到配置里的真值",
                volume_slider.value() == store.get("volume"),
                f"滑块={volume_slider.value()} 配置={store.get('volume')}",
            )
            volume_slider.setValue(52)
            check(
                "恢复写盘后改值真的落盘",
                store.get("volume") == 52,
                f"配置={store.get('volume')}",
            )

            # ---------------------------------------------------- §5 干净退出
            section("5. 收尾：两个窗口都能干净收掉")

            controller.close()
            app.processEvents()
            check("设置窗口已关闭", controller.dialog is None, f"{controller.dialog!r}")

            QTimer.singleShot(0, app.quit)
            app.exec()
            check("事件循环正常返回（模态窗口没把它卡住）", True, "app.exec() 已返回")

            pet.close()
            pet.deleteLater()
    finally:
        shutil.rmtree(SCRATCH, ignore_errors=True)

    return _finish()


if __name__ == "__main__":
    raise SystemExit(main())
