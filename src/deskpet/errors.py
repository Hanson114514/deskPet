"""项目异常基类。

**为什么单独一个模块**：后续 task（素材加载、配置、自启、重启）都要
"可恢复的失败 → 抛一个能被上层认出来的异常"，而这些异常必须能**不 import 任何重物**
就被 import（`logger` 的崩溃兜底路径只许依赖标准库，见 agent.md §2.10）。

本 task（task02）**只建骨架**，不引发任何异常；使用者从 task03 起。
"""


class DeskPetError(Exception):
    """deskPet 全部项目异常的基类。

    `logger.install_crash_handler()` 只把**未捕获**异常当崩溃；
    继承本类的异常表示"**我们认得的、有明确语义的**失败"，
    上层应当：记日志（`ERROR` 或 `WARNING`）→ 按 agent.md §2.10 的
    三类口径（启动期致命 / 用户要求的写失败 / 可恢复只读）分别处置。
    """


class DataDirError(DeskPetError):
    """用户数据目录（`<程序目录>/data/`）不可用。

    **启动期致命**（agent.md §2.10 第一类）：写不了数据目录意味着配置与日志
    全都立不住，继续跑只会得到"每次启动都是新程序"的假象。
    所以 `paths.ensure_data_dir()` 抛本异常，由 `app.run()` 写日志 + 弹窗 + 非 0 退出。
    """


class ConfigError(DeskPetError):
    """配置**写**失败（读失败一律回退默认 + 警告，绝不抛）。

    `ConfigStore.set()` **不抛**本异常——它按 task02 §4 的约定返回 `False`
    并在 `last_error()` 里留原因；本类留给"调用方明确要求报错"的场景
    （例如后续设置窗口要把系统原因原样显示给用户，agent.md §2.10）。
    """
