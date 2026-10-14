# task02 · 路径、配置与日志：能记住一句话，也能说清昨天发生了什么

## 1. 背景

- **task01 已完成**：仓库里已有 `run.py` / `main.py` / `src/deskpet/`，
  能起一个透明置顶、可拖、可用 `Esc`/`Ctrl+Q` 退出的占位窗口。
- 本 task 建立**所有后续 task 都要用的两块地基**：
  1. **统一路径模块**（"程序目录 / 用户数据目录 / 打包资源目录"三者的唯一真值）；
  2. **配置读写 + 日志**（能持久化、能容错、能事后诊断）。
- 现在**还没有**素材目录、没有 `assets/`、没有 `data/`（首次运行应由本 task 创建 `data/`）。

> **开始前必须先读**：`agent.md`、`迁移清单.md` 第二部分 §2/§3/§4、
> 第三部分 3.6 / 3.7 / 3.11 / 3.13 / 3.14 / 3.21。

## 2. 本任务目标

运行后能看到：

1. **首次运行**自动创建 `<程序目录>/data/`，并在其中出现 `deskpet.log`
   （之后每次启动，日志里都有一行 `===== YYYY-MM-DD HH:MM:SS =====` 作为分隔）。
2. 运行结束（或崩溃）后，日志文件里能读到本次运行的关键步骤；日志**同时**输出到控制台
   （有控制台时）——**新增一处输出不需要逐个改调用点**。
3. **手工编辑** `<程序目录>/data/config.json` 后重启，程序**按新值行事**（见下条验证用的键）；
   把配置改成**故意坏掉**的形态（不是 JSON / 顶层不是对象 / 字段类型不对 / 未知键），
   程序**照常启动**，并且：
   - 坏字段回退默认值 + 日志里有一条警告；
   - **未知键不会导致启动失败**，并且在下次写回时**被过滤掉**。
4. 写配置是**原子**的：写入过程中断（可人为制造）**不会留下半个 config.json**，
   也不会留下临时文件。
5. 日志超过 **256 KB** 会滚动成 `deskpet.log.1`，**只保留 1 份历史**。

> 本 task 的"用户可见行为"其实很弱（没有新窗口），**它的可见性在 `data/` 目录里**：
> 用文件管理器打开 `<程序目录>/data/` 就能验收。

## 3. 涉及文件

预计新增：

```
src/deskpet/paths.py        三类路径的唯一真值（程序目录 / 用户数据 / 打包资源）
src/deskpet/logger.py       日志装配（控制台 + 文件 + 滚动 + 启动分隔行）
src/deskpet/config.py       配置默认值表 + Config 数据对象 + ConfigStore（原子读写 / 校验 / has()）
src/deskpet/errors.py       项目异常基类（供后续 task 使用；本 task 只建骨架）
```

预计修改：

```
src/deskpet/app.py          启动时先装日志、再读配置；退出时收尾
run.py / main.py            确保路径模块在最早时机可用（不要在本 task 引入 sys.path 花招）
```

预计生成（**运行产物，不进版本控制**）：

```
data/config.json            data/deskpet.log            data/deskpet.log.1（滚动后）
```

## 4. 接口约定

### 路径（`deskpet.paths`）

| 函数 | 返回 | 语义 |
| --- | --- | --- |
| `program_dir() -> Path` | 程序目录 | 打包后是 exe 所在目录；源码运行是**仓库根** |
| `resource_dir() -> Path` | 只读资源根 | 打包后是解包目录；源码运行是仓库根 |
| `data_dir() -> Path` | `<程序目录>/data` | **可写**用户数据；不存在时由 `ensure_data_dir()` 创建 |
| `ensure_data_dir() -> Path` | 同上 | 幂等创建，失败要**记日志并抛**（写不了数据目录是配置无法工作，不是静默降级） |
| `config_path() -> Path` | `<data>/config.json` | — |
| `log_path() -> Path` | `<data>/deskpet.log` | — |
| `crash_log_path() -> Path` | `<data>/crash.log` | — |
| `user_pets_dir() -> Path` | `<data>/pets` | 本 task 只提供路径，不创建、不扫描 |
| `bundled_pets_dir() -> Path` | `<resource>/assets` | 只读，**绝不允许写入** |
| `is_frozen() -> bool` | 是否打包运行 | **打包判定的唯一出处**，其它模块不许自己判断 |

**硬约束**：除 `paths.py` 之外**任何模块都不许用 `__file__` 反推路径**（§3.6）。

### 配置（`deskpet.config`）

- `DEFAULTS: dict[str, object]` —— **默认值表是唯一真相源**（键顺序即后续设置界面的行顺序）：

  | 键 | 类型 | 默认值 | 本 task 是否需要可写 |
  | --- | --- | --- | --- |
  | `pet` | str | `""` | 是（空串 = 用户没表态） |
  | `hotkey` | str | `"Ctrl+Alt+Shift+P"` | 是（本 task 只存取，不注册） |
  | `volume` | int | `70` | 是 |
  | `idle_seconds` | int | `20` | 是 |
  | `size_level` | int | `6` | 是（**必须引用尺寸模块的默认档常量**，本 task 先写字面量并在 task04 改为引用） |
  | `position` | str | `""` | 是（格式 `"x,y"`；本 task 可先不用窗口位置，只保证读写） |
  | `mirror` | dict | `{}` | 是（状态名 → 布尔；只允许 `idle`/`waiting`/`sit` 三个键） |

- `Config` —— **不可变或显式受控**的数据对象，字段即上表键。
- `ConfigStore(path)`：

  | 成员 | 语义 |
  | --- | --- |
  | `load() -> Config` | **永不抛异常**：文件不存在 / JSON 坏 / 顶层非对象 / 类型不符 → 逐项回退默认值 + 警告 |
  | `get(key)` | 取当前值（已在内存） |
  | `has(key) -> bool` | **配置文件里真的写过这一项吗**（§4 规则 5：必须能区分"没设过"与"设成了默认值"） |
  | `set(key, value) -> bool` | 校验 + 改内存 + **原子落盘**；写失败**只记日志**，返回 `False`，**不抛** |
  | `reload()` | 重新读盘（供后续"改完立即生效"用） |

- **类型校验**：`isinstance(value, type(default))`；`bool` 不算 `int`；
  `mirror` 额外按"键属于适用范围 + 值必须是 bool"过滤。
- **原子写**：`QSaveFile` 或临时文件 + 替换（**选一种并在 `agent.md` 里写死**）。

### 日志（`deskpet.logger`）

| 符号 | 语义 |
| --- | --- |
| `setup_logging(level=logging.INFO) -> None` | 装根 logger：控制台 handler（存在时）+ 文件 handler（`encoding="utf-8"`） |
| `RotatingFileHandler` 参数 | `maxBytes=256*1024`，`backupCount=1`，`encoding="utf-8"` |
| `log_startup_banner()` | 追加一行 `===== YYYY-MM-DD HH:MM:SS =====` |
| `install_crash_handler()` | 只依赖标准库的崩溃兜底，写 `<data>/crash.log` |

- **写日志失败绝不抛异常**（不能把主流程搞崩）。
- 崩溃兜底**故意不 import 项目模块**，自己复刻一遍"程序目录 + data"规则，
  **并且必须与 `paths.py` 对一次**（加断言防漂移，§3.7）。
- **模块级调用一律写在函数定义之后**（曾因写反导致 NameError，等于没有诊断）。
- **不要**给模块起会遮蔽标准库的名字（例如不要叫 `logging.py`，本 task 用 `logger.py`）。

## 5. 不要做

- ❌ 不加载素材、不建 `assets/`、不解析 `asset.json`、不读 PNG
- ❌ 不做动画、不做尺寸档位计算（`size_level` 只存取，不解释）
- ❌ 不做窗口位置持久化（`position` 只存取，不解释、不夹取——夹取属于 task08）
- ❌ 不做设置窗口 / 自启 / 单实例 / 热键 / 音效
- ❌ 不做 `--pet` / `--sprite` / `--list-*`（属于 task03）
- ❌ 不做 `DOTPET_PROBE` 启动探针（属于"分发"阶段，本阶段不需要）
- ❌ 不引入第三方日志库、不引入 `PySide6.QtMultimedia`
- ❌ 不把 `data/` 提交进版本控制（要加 `.gitignore` 条目）
- ❌ 不改 `assets/` 下任何原始素材

## 6. 验收标准

- [ ] 删除整个 `data/` 后运行程序：`data/` 与 `data/deskpet.log` 被自动创建，程序正常启动
- [ ] 日志里能看到本次启动的分隔行，以及启动步骤；控制台（若有）同时有输出
- [ ] 手工把 `config.json` 改成 `{ 坏 JSON`，重启：程序**正常启动**，日志里有警告，配置回退默认值
- [ ] 手工把 `config.json` 改成 `[]`（顶层非对象），重启：同上，**不崩**
- [ ] 手工把 `"volume"` 写成 `"大声"`，重启：该字段回退 `70` + 一条警告；**其它字段不受影响**
- [ ] 手工塞一个未知键 `"nonsense": 1`，然后触发一次写配置：
      未知键在文件里**消失**，且**已知键一个都不丢**（尤其 `mirror`）
- [ ] `volume` 写 `True`（布尔）**不通过**类型校验（布尔不算 int）
- [ ] 写入失败路径可验证：把 `data/` 设为只读或用一个会抛异常的桩替掉写函数，
      确认 `set()` 返回 `False`、日志里有原因、**程序不崩、界面/内存值不被污染**
- [ ] 写入是原子的：写盘过程中不会出现"半截 JSON"；失败后目录里**没有残留临时文件**
- [ ] 日志超过 256 KB 后出现 `deskpet.log.1`，且**只有 1 份历史**
- [ ] `paths.py` 之外搜不到 `__file__`（可用搜索工具确认）
- [ ] 断言"数据目录在程序目录内、不在 `assets/` 里、不在 `%APPDATA%`"能通过
- [ ] 模拟"打包运行"（让 `is_frozen()` 返回真、`program_dir()` 与 `resource_dir()` 不同）时，
      配置与日志仍然落在**程序目录**下，而不是资源目录

## 7. 完成后请更新 agent.md 中“当前进度”一节

- 记录 task02 已完成 + 实际结果（`data/` 里的三个文件、日志滚动实测值）；
- **把"原子写的具体实现（QSaveFile / 临时文件+替换）"写进 agent.md 的约定表**；
- 记录 `data/` 已加入 `.gitignore`；
- 下一个 task 指向 `tasks/task03.md`。
