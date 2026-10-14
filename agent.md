# deskPet · 项目交接与约定（agent.md）

> **本文件的作用**：新会话的**唯一必读交接文档**。`requests.md` 是用户对我的要求（**不得改动**）；
> 规则：**一个会话 = 一个 task**。会话开始先读本文件 + 当前 task 卡，结束时更新"§5 当前进度"。
- 不要管迁移清单已经被我删掉了，commit时也不要管，其他task也不要乱改，读要求-编程-测试(修复)-commit-结束，如有特别重大的改动会严重影响其他任务才记入这个文档
---

### 只做要求做的事，其余一律不许多管，有问题直接告诉我我再决定

## §A 开工纪律（**最高优先级，先读这一节**）

> 本节编号是 **§A**，不要和"§0 已定案的重大决策"撞号。引用时写 **§A**。
> 背景：task03 的工作量不大（3 个模块、约 1350 行），消耗却异常高。复盘结论是
> **浪费不在"做得太多"，而在这几条**。这是硬性纪律，违反任何一条就等于这个会话已经跑偏。

### A.1 禁止无限试错（**一票否决**）

- **同一个问题失败 2 次就必须停**。第 3 次不允许"换个写法试试"——回到**最小复现**，或**直接问用户**。
- **不许把生产代码当调试工具**：怀疑某个函数时，先写一个**只读**的最小脚本把事实打印出来，**拿到结论才动 `src/`**。
- **改之前先说清假设**：一句话写明"我认为 X 是原因，因为 Y"，以及这轮打算看什么事实。
- **临时调试代码当轮清干净**：调试开关 / `print` / 临时脚本，不留到下一轮"以防万一"。
- **调试输出写文件，不要靠重定向**：进程会在文件可读之前退出（§4.3 同一条教训）。

> task03 的教训：`python -m deskpet` 的路径问题耗了约 10 轮，改生产代码、加开关、忘 `import os`……
> **最后一行 trace 就证明原实现是对的**，改动全部回退。若第一轮先写 15 行只读脚本打印 `sys.path`，一轮就能收工。

### A.2 减少非必要越界改动（**只做当前 task 卡要求的事**）

- **不修需求外的"已知边界"**：本阶段不支持的入口 / 现象，正确动作是**记录 + 继续**。
- **不改下游 task 的实现前提**：只改当前卡；下游卡里过期的名字，用卡首加一句"旧名读作新名"解决。
- **不预读下游 task 卡**（task03 一次读进 task05–10 共 340 行，全部没用上）。
- **文档改动与代码改动同量级**：命名类改动先用 `grep -n` 列出全部命中点再决定改哪些。
- **不把 `agent.md` 抄进代码注释**：注释写结论 + 指回小节号，不要复制一遍。
- 碰到"能不能顺手也做了"时，**默认答案是"不做"**；想越界先问用户。

### A.3 省上下文的工作方式

1. **装文档按需**：只读本文件 §A / §1 / §2 / §5 + **当前那张 task 卡**；只读卡里点名的小节。
2. **代码分块推进**：每块 ≤ ~200 行，写完立刻用 3 行断言验一下再往下写；禁止 500 行一气呵成再返工。
3. **注释分级**：只在"反直觉、后人一定会改错"处写长注释（如 PNG 偏移、模态弹窗），其余 1–3 行结论。
4. **自检只写一个脚本**，按节输出、共用一个 `check()`；先跑 3–5 项的最小版确认脚手架对，再加内容。
5. **一次工具调用干一件事**，不要为了"顺便"把无关的读/写塞进同一批。

---

## 0. 已定案的重大决策（不要再改，除非用户明确要求）

| 决策 | 内容 |
| --- | --- |
| **透明** | 素材是**透明背景 PNG**；运行时**没有色键步骤**。旧实现的 `#FF00FF` 色键、容差 6、RGB 烘焙全部作废 |
| **点击穿透** | **`setMask(QRegion)`**，按当前帧不透明像素生成窗口区域；代价是**边缘锯齿**（已接受）；`hitmask.json` 不再需要 |
| **项目命名** | 仓库/项目名 **`deskPet`**，Python 包名 / 模块前缀 **`deskpet`**（两者不混用）；日志 `deskpet.log`、自启项 `deskpet.lnk` |
| **动作名** | `idle` / `running_left` / `running_right` / `waiting` / **`rest`** / **`waving`**（**下划线**，2025 用户定案）。旧名 `review`→`waiting`、`sit`→`rest`、`jumping`→`waving` **全部作废，不留别名** |
| **默认形象** | **`weedy`**（`phoebe` 不再重做，最多当补充包导入）；真值只在 `pet_spec.DEFAULT_PET_NAMESPACE` 一处 |
| **帧率** | 由素材 `asset.json` 的 `actions.<动作>.fps` 决定；缺失 → 遗留兜底表 → `DEFAULT_FPS = 12.0`。两个自带包已全部写 `fps: 30.0`，兜底表实际不会被触发；**`--fps` 已删除，不得复活** |
| **镜像翻转** | 右键菜单**一条**，只翻转当前状态，按状态分别记忆写入 `config.mirror`；适用范围只有 `idle` / `waiting` / `rest` |
| **截屏 / 录屏** | **本阶段不做**，不占 task 编号 |

---

## 1. 项目结构

```
C:\projects\deskPet\
├── agent.md          ★ 本文件：实现约定 / 已知坑 / 当前进度（每个会话都要读、都要更新）
├── requests.md       项目规则（用户的地盘，不得改动）
├── tasks/            task01.md … task14.md，一个会话做一张卡
├── run.py            开发入口：把 src/ 加进搜索路径，调用 deskpet.app.run()
├── main.py           打包入口：顶层静态 import（打包时模块才收得全），调用同一个 run()
├── assets/<命名空间>/ ★ 自带素材（只读，随分发走）
│       asset.json    素材索引（必需）        frames/*.png  显示帧（必需，带 alpha）
│       sounds/*      音效（可选，索引里声明了才用）
├── data/             ★ 用户数据（可写；.gitignore 里已忽略，首次运行才生成）
│       config.json · deskpet.log（滚动 .1，上限 256 KB，留 1 份）· crash.log（正常时不存在）
│       pets/<命名空间>/   用户自备素材包（结构与自带一致，同名时**用户优先**）
├── src/deskpet/      源码包（见 §1.2）
├── tools/            开发期工具（构建期脚本等）
└── docs/             设计与踩坑记录（可选；现有 task03 截图）
```

- **两个入口不能合并**：`run.py` 是运行时改搜索路径的开发脚本；`main.py` 的 import 写在顶层，
  静态分析（打包）才收得全模块。两者指向**同一个** `run()`。
- **红线**：`assets/` 下的原始素材**不得修改**（要改先问用户）；`data/` **不进版本控制**。

### 1.2 源码结构（`src/deskpet/`）

按**职责**分层；上层可以依赖下层，下层**不许**反向依赖。**task01–04 已交付的**（存在、可用）：
`paths` `logger` `errors` `config` `nsrules` `pet_spec` `pet_loader` `size` `rendering` `pet_window` `app`。

| 模块 | 层 | 职责 | 状态 |
| --- | --- | --- | --- |
| `paths.py` | 基础 | 三类路径的唯一真值（程序目录 / 用户数据 / 打包资源）；`is_frozen()` 的唯一出处；**`__file__` 反推路径只允许在这里** | ✅ |
| `logger.py` | 基础 | 日志装配、启动分隔行、崩溃兜底（只依赖标准库） | ✅ |
| `errors.py` | 基础 | 项目异常基类（`DataDirError` 等） | ✅ |
| `config.py` | 基础 | 默认值表 + `Config` + `ConfigStore`（原子读写 / 类型校验 / `has()`）；`MIRROR_STATES` | ✅ |
| `nsrules.py` | 数据 | 命名空间白名单校验（长度 / 禁用字符 / 首尾空白 / 结尾点） | ✅ |
| `pet_spec.py` | 数据 | `PetSpec` / `ActionSpec` + **帧率解析唯一实现**；规范动作集与默认形象的真值 | ✅ |
| `pet_loader.py` | 数据 | 两根扫描、`asset.json` 解析、**一次性列全校验**、统一解析"一个说法"、`load_frame_image()` | ✅ |
| `size.py` | 数据 | 10 级档位（**精确有理数**）、像素换算、屏幕自适应、右下角锚点与夹取 | ✅ task04 |
| `hit_test.py` | 数据 | 命中判定唯一入口（含镜像坐标翻转）；`region_for_frame` 的真实现 | ✅ task08 |
| `sounds.py` | 数据 | 音效映射 + 播放器 + 优雅退化 + 诊断 | ✅ task10 |
| `autostart.py` | 系统 | 开机自启：`query_state`（**纯只读**）/ `enable`（快捷方式，失败回退 `.cmd`）/ `disable`（幂等）/ `startup_dir` / `cleanup_legacy_markers` | ✅ task14 |
| `single_instance.py` | 系统 | 单实例保护（**D6 = 命名互斥体**；作用域仅当前登录会话） | ✅ task13 |
| `restart.py` | 系统 | 重启自己（拉起新实例 → 放锁 → 退出；含 `--restart-delay`） | ✅ task13 |
| `win32.py` | 系统 | **Win32 薄绑定**（唯一的 `ctypes` 出口；非 Windows 上导入不炸、只报"不可用"） | ✅ task12 |
| `hotkey_spec.py` | 系统 | 快捷键**说法**：两套修饰键掩码、`parse_combo` / `format_combo`（纯逻辑） | ✅ task12 |
| `hotkey.py` | 系统 | 全局热键：子线程消息循环 + 候选链回退 + 彻底释放（**D5 定案**） | ✅ task12 |
| `rendering.py` | 视图 | 帧 → 目标尺寸的绘制对象 + LRU 缓存（键含**动作/帧号/档位/镜像**） | ✅ task04 / 镜像 task06 |
| `animation.py` | 逻辑 | 帧推进 / 播放方式（loop / pingpong / oneshot）/ 定格 / 片段序列（**纯逻辑**，只有一个 `QObject`） | ✅ task05 |
| `idle_state.py` | 逻辑 | 久置行为状态机（**纯逻辑，不依赖 Qt**；时间由外部喂） | ✅ task07 |
| `cursor_watch.py` | 逻辑 | 全局光标轮询 + "有没有真的动了" | ✅ task07 |
| `drag.py` | 逻辑 | 拖动状态机（`DragState` / `DragController`）+ 方向→动作的纯函数 | ✅ task08 |
| `mirror.py` | 逻辑 | 镜像状态表（按状态记忆，读写配置） | ✅ task06 |
| `geometry.py` | 逻辑 | 夹取 / 锚点 / 缺省落点 / `"x,y"` 位置解析（**转发 `size`，不重写**） | ✅ task08 |
| `pet_window.py` | 视图 | 宠物主窗口（事件处理器、显示、隐藏、遮罩、按档位定尺寸 + 右下角锚点切档、**动画驱动 `AnimationDriver`**、**右键菜单入口**、**底部预留边距**） | ✅ |
| `control_bar.py` | 视图 | 尺寸控制栏（独立顶层窗口 + layout） | ✅ task09 |
| `context_menu.py` | 视图 | 右键菜单（每次弹出前重建） | ✅ task06 |
| `settings.py` | 视图 | 设置窗口（字段描述表驱动） | ✅ task11 |
| `keycapture.py` | 视图 | 快捷键录入控件（**骨架**，task12 接线） | ✅ task11 |
| `app.py` | 装配 | `QApplication` 引导、命令行解析、启动编排、信号接线、退出收尾 | ✅ |

---

## 2. 接口统一约定

### 2.1 文件与类命名规则

| 对象 | 规则 | 例子 |
| --- | --- | --- |
| Python 模块 | `snake_case.py`，**不许遮蔽标准库**（不要 `logging.py` / `types.py`） | `pet_loader.py` |
| 包名 | 全小写 `deskpet`；仓库/项目名是 `deskPet`，**两者不要混用** | — |
| 类 | `PascalCase`，名词结尾 | `PetWindow` / `ActionSpec` / `ConfigStore` |
| 数据结构 | 优先 `@dataclass(frozen=True)`（不可变，便于断言） | `PetSpec` / `ActionSpec` |
| 纯函数 | `snake_case`，动词开头；**副作用要能从名字看出来** | `pixel_size()` / `clamp_level()` |
| 私有成员 | 单下划线 `_x`；**不要**用双下划线做"私有" | — |
| 常量 | `UPPER_SNAKE`；**同一个真值只允许定义一处** | `DEFAULT_FPS` / `MIRROR_STATES` |
| 类型注解 | 公开函数**必须**有参数与返回类型注解 | `def pixel_size(cell: tuple[int, int], level: int) -> tuple[int, int]` |
| `from __future__ import annotations` | **不写**。目标解释器 **3.14**（PEP 649/749，注解默认惰性求值），加上去是零收益样板；也没有运行时解析注解的场景。task01 的五个模块里带着它，**由用户自行删除，后续 task 不要再加回来** | — |

### 2.2 信号命名规则

**统一格式：`<名词>_<过去分词>`（描述"已经发生的事实"），全小写 + 下划线。** 命令一律用**方法调用**，
不要写成命令式信号。带数据的信号**优先传结构化对象**（dataclass），不要传长字符串让人再解析。
**跨线程只允许用信号/槽**（§2.6）。信号名一旦用了就**不要改名**。

| 信号 | 发出者 | 含义 |
| --- | --- | --- |
| `action_changed(action)` | 动画/控制器 | 当前动作已经换成 `action` |
| `frame_advanced(next_ms)` | 动画 | 前进了一帧；`next_ms` 是距下一帧的毫秒数（`None` = 定格，不排下一次） |
| `sequence_done()` | 动画 | 片段序列播完 |
| `stage_changed(old, new)` | 久置状态机 | 姿势阶段变化（`idle` / `waiting` / `rest`） |
| `timer_reset()` | 久置状态机 | 只归零计时，不切动作 |
| `position_changed(pos)` | 宠物窗口 | 窗口位置变了（拖动结束/夹取后） |
| `size_level_changed(level)` | 尺寸控制器 | 档位变了 |
| `mirror_changed(action, on)` | 镜像状态表 | 某个状态的镜像翻转变了 |
| `config_changed(key, value)` | `ConfigStore` | 配置项变了（供"改完立刻生效"用） |
| `settings_changed(key, value)` | 设置窗口 | 用户改了一项设置 |
| `hotkey_pressed()` | 热键管理器 | 收到全局热键 |
| `visibility_changed(visible)` | 宠物窗口 | 隐藏 / 呼出 |
| `sound_ready(available, reason)` | 音效 | 预热完成（成功或失败原因） |
| `recording_stopped(path)` | 录屏（**尚未实现**） | 命名规则先立在这里 |
| `error_occurred(scope, detail)` | 任意 | 可恢复错误的**结构化**通知（界面据此显示） |

### 2.3 目录与文件命名约定

| 对象 | 约定 |
| --- | --- |
| 素材包目录 | 目录名**就是**命名空间；1–64 字符；见 §2.9 白名单 |
| 索引文件 | 固定 `asset.json`（**必需**）；`pet.json` / `frames.json` 已废弃，无向后兼容 |
| 帧目录 / 音效目录 | 固定 `frames/` / `sounds/` |
| 命中遮罩 | **不再需要**（`setMask` 按帧 alpha 生成区域）；不产出、不读取 `hitmask.json` |
| 帧文件名 | **由索引的 `frames` 列表决定**，不猜命名规律；实测两种写法并存（`idle_00.png`、`idle_000.png`），按帧数决定序号位数 |
| 日志 / 配置 / 自启项 | `data/deskpet.log`（滚动 `.1`）· `data/crash.log` · `data/config.json`（原子写，**不给人手改坏就起不来**）· 「启动」文件夹里的 `deskpet.lnk`（回退 `deskpet.cmd`，**那里只允许这一个文件**） |
| 临时文件 | **不要**在用户可见目录里留临时文件；原子写失败后必须收拾干净 |
| 输出文件（未来） | 一律 `<程序目录>/data/output/`，文件名 `<类型>_<YYYYmmdd-HHMMSS>.<ext>`，**绝不覆盖已有文件**（重名加 `-1` / `-2`） |

### 2.4 `config.json` 字段约定

| 键 | 类型 | 默认值 | 含义与约束 |
| --- | --- | --- | --- |
| `pet` | str | `""` | 素材包命名空间。**空串 = 用户还没表态**（默认形象的真值只在 `pet_spec` 一处） |
| `hotkey` | str | `"Ctrl+Alt+Shift+P"` | 呼出快捷键。空串 = 未设置（此时隐藏功能不可用） |
| `volume` | int | `70` | 音效音量 0–100；越界夹取；非法值归 0 |
| `idle_seconds` | int | `20` | 久置时间（秒）。界面量程 5–300；**判定时读**，下限夹到 1 |
| `size_level` | int | 第 6 级 | 上次用的**档位**（1–10），不存像素。**真值在 `size.DEFAULT_LEVEL`，`config.DEFAULT_SIZE_LEVEL` 只做引用**（task04 已落地）。启动时：命令行 > `has("size_level")` > 按屏幕自适应；**配置里的越界值走 `clamp_level()` 夹取 + 警告**（命令行越界则是报错退出，两条规则别混） |
| `position` | str | `""` | 上次拖动到的左上角，格式 `"x,y"`（物理像素）；空串 = 贴右下角。**用字符串**与命令行说法一致 |
| `mirror` | object | `{}` | 按状态记忆的镜像：`{"idle": true, "rest": false}`；只允许 `idle` / `waiting` / `rest`（真值在 `config.MIRROR_STATES`），值必须是 bool |

**通用规则**：

1. **读取永不致命**：文件不存在 / JSON 损坏 / 顶层不是对象 / 字段缺失 / 类型不符 → 回退默认值 + 一条警告，**绝不阻止启动**。
2. **写入原子**：临时文件 + 替换；中途失败不留半个文件，失败时收拾临时文件。
3. **只保存已知键**：写回按默认值表的键过滤（**`mirror` 也是已知键，别丢**）。
4. **类型校验按默认值类型**：`isinstance(value, type(default))`；**`bool` 不算 `int`**。
5. **必须能区分"没设过"与"设成了默认值"** → 用 `has(key)`；**不要**用"读到的值 ≠ 默认值"代替。
6. **读不出来一律当成"没设过"**，让调用方走缺省路径。
7. 加配置项只需在默认值表加一行（界面与读写自动跟上）。

**task02 已落地的实现（后面只做替换，不要另起一套）**：

| 项 | 定案 | 理由 / 位置 |
| --- | --- | --- |
| **原子写** | `tempfile.mkstemp`（同目录）+ `fsync` + `os.replace()`，失败在 `finally` 里删临时文件 | **不用 `QSaveFile`**：`config.py` 是"基础"层，不许反向依赖 Qt（注：`ConfigStore` 本身是 `QObject`，只用 `QtCore`） |
| **写失败的表达** | `set()` **不抛**，返回 `False` + `logger.error`；原因留在 `last_error(key)`（含系统原文） | 界面的"写失败必须报错"据它显示（§2.10 第二类） |
| **`has()` 语义** | 读盘时记录"文件里真的写过**且通过校验**"；`set()` 成功也记为写过 | 用户主动选默认档位时 `has()` 必须为真 |
| **`mirror` 过滤** | 合法但不是对象 → **整项拒收**；是对象 → 只留白名单键 + bool 值，其余丢弃但**照样采用剩下的**（结果可能是空对象） | "过滤后为空"≠"没设过"，别回退 |
| **`config.json` 默认不创建** | 只在 `set()` **真的改变值**时才落盘（值没变不发信号、不写盘） | 与"读操作只读"一致；首次运行后 `data/` 里只有 `deskpet.log` 是正常的 |
| **配置对象** | `ConfigStore` 是 `QObject`，有 `config_changed(key, value)` 信号 | 供后续"改完立刻生效" |
| **默认值真值** | `DEFAULTS` 是唯一真相源；`Config` 的字段名/顺序/默认值与它一致，**模块级断言守着** | 加一行默认值忘了加字段会当场 `RuntimeError` |

### 2.5 `asset.json` 字段约定

```json
{
  "name": "温蒂",
  "description": "温蒂：待机、挥手、左右走动、张望与休息。",
  "cell": [500, 500],
  "actions": {
    "idle": {"frames": ["idle_00.png", "idle_01.png"], "fps": 30.0},
    "rest": {"frames": ["rest_00.png", "rest_01.png"], "fps": 30.0}
  },
  "sounds": {"rest": "phoebe_chubby_0.mp3", "chime": null}
}
```

| 键 | 必需 | 类型 | 约束 |
| --- | --- | --- | --- |
| `name` | 否 | str | 展示名。缺失/空白 → 退回命名空间（**不显示"未命名"**） |
| `description` | 否 | str | 一句话说明；非字符串 → 空串 |
| `cell` | **是** | `[数, 数]`（**数组不是对象**） | 两个**正数**、布尔不算；必须等于帧文件实际像素尺寸。**核对只对"第一个动作的第一帧"做一次，且只算警告**：对不上时画面照样能出，不值得让整个包不可用 |
| `actions` | **是** | 对象 | 动作名 → `{"frames": [...], "fps": 数}` |
| `actions.<动作>.frames` | **是** | 数组 | 帧文件名（相对 `frames/`）；**顺序即播放顺序**，一律写全、不猜规律。空数组 / 非字符串项算**问题** |
| `actions.<动作>.fps` | 否 | 数（**可为小数**） | 缺失/非法 → 走帧率优先级（§2.7）。**解析在加载层一次算清**，对外只给 `frame_ms = 1000.0 / fps`（浮点） |
| `sounds` | 否 | 对象 | 逻辑名 → 文件名或 `null`。**只认声明**，没有兜底表。`null` 记为 `""`；非字符串值丢弃 + 一条警告（**不是**致命问题） |

- **读取用 `utf-8-sig`**（容 BOM；实测带 BOM 的索引能正常解析），**写用无 BOM 的 `utf-8`**。
- `asset.json` 是"人写一半、工具写一半"：切帧工具会**整个重写**它，但必须先把认得的人写键
  （`name` / `description` / `sounds` / **各动作的 `fps`**）原样搬过去。
- 动作名以素材 `actions` 的键为准，**有几个就是几个**：不补、不改名、不造别名，
  代码侧**不许**为找不到的动作硬塞替身（缺 `waving` 就不挥手，按可恢复路径退化）。
- **规范动作集已统一**（§0 / D12）：编程侧与素材侧都用下划线写法；`review` / `sit` / `jumping` 全部作废。

**当前素材状态（task03 实测，后续 task 的验收基线）**：

| 包 | 命名空间 | 展示名 | 动作数 | 音效 | `cell` | alpha |
| --- | --- | --- | --- | --- | --- | --- |
| 温蒂 | `weedy` | 温蒂 | **6**（`idle`/`rest`/`running_left`/`running_right`/`waiting`/`waving`） | 无 | `[500, 500]` | 1 位（只有 0/255） |
| 温蒂（倾听） | `weedy_sightseer` | 温蒂（倾听） | **7**（多 `温蒂-倾听-基建-Special-x1`，600 帧） | 无 | `[500, 500]` | 1 位 |

`assets/phoebe/` **不存在**。抽帧工具见 §4.12。

### 2.6 线程通信约定

**当前阶段：不使用任何工作线程。** 所有逻辑都在 GUI 线程（`QTimer` 足够，且 `QPixmap` 只能在
GUI 线程创建/使用）。**不要**为了"看起来专业"提前引入 `QThread`。

若将来确实需要线程（录屏、大批量解码），必须遵守：

| 规则 | 说明 |
| --- | --- |
| 只允许 `QThread` + **信号/槽**通信 | 不要当成"共享内存的并发" |
| **不共享可变状态** | 工作线程不许直接读写 GUI 对象；需要的数据在启动时**拷贝**传进去 |
| 结果回传 | 一律通过**信号**（附带结构化对象），由 GUI 线程决定怎么用 |
| 设备类对象 | 播放器、窗口、`QPixmap` **只能在 GUI 线程碰**；`QImage` 才可以跨线程传 |
| 取消与退出 | 线程要能被安全停止（`requestInterruption` / 显式停止信号），退出时必须 `wait()` |
| **测试必须走产品实际路径** | 旧项目有"自检全绿、产品无声"的教训：自检把两个操作放在同一线程了 |

### 2.7 帧率约定

**优先级（从高到低）**：

1. `asset.json` 里 `actions.<动作>.fps`；
2. **遗留兜底表**（过渡期兼容层，值取自旧 Tkinter 实现）：
   `idle 8.0` / `running-left 11.0` / `running-right 11.0` / `waiting 8.0` / `sit 8.25` / `jumping 8.0`；
3. **`DEFAULT_FPS = 12.0`**。

| 规则 | 约定 |
| --- | --- |
| 唯一写入位置 | 包根 `asset.json` 的 `actions.<动作>.fps`（**不新建 `frames/<动作>/asset.json`**，帧仍平铺） |
| 归一化 | 加载层统一解析为 **每帧显示时长 `frame_ms = 1000.0 / fps`（浮点，不取整）**；动画层**只消费** |
| 真值只一处 | 遗留表与 `DEFAULT_FPS` 各只允许定义一处（都在 `pet_spec.py`，模块级断言守着键集合） |
| 坏 `fps` | 非数字 / `bool` / `<= 0` / NaN / ±inf → 丢弃 + 一条警告，**按"没有 fps"继续走下一层**，绝不抛异常 |
| 命令行 | **`--fps` 已删除，不得复活** |
| 遗留表何时删 | 用户补完 `fps` 之后即可整体删除（D9）。**当前两个包全是 `fps: 30.0`，该表一次都不会被触发**——它现在只剩"用户把 `fps` 删掉时观感不变"这一层意义 |

**task03 已落地的实现（后面只做消费，不要另起一套）**：唯一实现是
`pet_spec.resolve_frame_rate(action, raw_fps) -> (fps, FrameRateSource)` 与
`pet_spec.frame_ms_for(action, raw_fps) -> (fps, frame_ms, source)`；坏值判定收在
`pet_spec.normalize_fps()` 一处。`ActionSpec` 对外暴露 `frame_ms` / `fps` / `fps_source` / `frame_count`，
`--list-actions` 打印的就是这几样。

### 2.8 透明、命中判定与镜像的约定

| 项 | 约定 |
| --- | --- |
| 帧格式 | **带 alpha 通道的 PNG**（透明背景由素材提供）。**运行时没有色键步骤**，加载层只做贴图与缩放 |
| 加载层职责 | 读 PNG → `QImage`（`Format_ARGB32_Premultiplied` 或等价）→ 按档位缩放 → 缓存。**不做颜色判定** |
| 点击穿透 | **`setMask(QRegion)`**：按当前帧的**不透明像素**生成窗口区域，区域外由系统级穿透 |
| 掩码生成 | **唯一入口** `hit_test.region_for_frame(frame_image, mirrored) -> QRegion`；"取哪一帧"与"怎么生成"**分开**（task08 把 `pet_window.region_for_image` 搬过去，不要写第二份） |
| 掩码时机 | **只在换帧 / 换档 / 换镜像时重算**；同动作同档位内复用（避免每帧重算的抖动与开销） |
| 镜像与掩码 | 区域必须**一起镜像**（`QRegion` 镜像或按镜像后的图生成） |
| 已知代价 | `QRegion` 是**二值、无抗锯齿**的 → **边缘有锯齿**。这是本方案明确的取舍，不要求本阶段解决；观感记在 §4.9 |
| 坐标 | 掩码与判定用**窗口内的物理像素**（`event.position()`）；位移用**全局坐标**（`event.globalPosition()`）；**两者不可混用** |
| 降级（**永不抛异常**） | 帧图为空 / 区域算不出来 / 帧号越界 / 动作不存在 → **退化为不设遮罩（整帧可点）** |
| 镜像 | **只作用于 `idle` / `waiting` / `rest`**；**水平镜像，在渲染层做**；不改素材 |
| 镜像记忆 | **按状态分别记忆**，写进 `config.mirror`；下次切回该状态**沿用上次** |
| 镜像与缓存 | 帧缓存键 = `(动作, 帧序号, 档位, 镜像)` —— **漏掉镜像 = "翻转后拿到未翻转的图"** |
| 镜像与锚点 | 水平翻转折点对称，**不影响**锚点与档位（"右下角锚点不动"仍成立） |
| 换帧/换档/换镜像 | 所有衍生数据（帧缓存、`QRegion`）**必须失效重算** |

**task01 已落地、task03 续用的部分**：区域生成入口是 `pet_window.region_for_image(image) -> QRegion | None`
（**不是** `region_for_frame`）：它**只看图，不管"取哪一帧"**。不透明阈值走 Qt 自带的
`QImage.createAlphaMask()`（约 `alpha >= 128`）；`None` / 空图 → `None`；**没有 alpha 通道 = 整块不透明**；
带 `devicePixelRatio` 的图**先折回逻辑尺寸**再生成区域（否则能点到的范围比看到的大一圈，高 DPI 下必现）。
消费者是 `PetWindow.apply_mask(image)`，返回"是否真的设上了遮罩"；区域为空 → `clearMask()`。

### 2.9 命名空间白名单（素材包目录名）

- 长度 **1–64**；目录名**就是**命名空间；从目录反推 = 取路径末段。
- **禁止**：`/` `\` `:` `*` `?` `"` `<` `>` `|`、控制字符（0–31）、`..`、`.`、以点开头、首尾空白、结尾的点（Windows 会悄悄吃掉）。
- **允许**中文等非 ASCII；**允许中间的普通空格**；非字符串 / 空 / 超长一律拒绝。
- **白名单式拒绝**，而不是事后判断路径是否越界（越界判断的花样永远漏）。

### 2.10 错误处理方式（统一口径）

**分三类，不许混：**

| 类别 | 处理 | 例子 |
| --- | --- | --- |
| **启动期致命** | 写日志 + 弹窗（能弹就弹）+ 非 0 退出 | 连默认素材包都不可用；`data/` 建不出来 |
| **用户明确要求的写操作失败** | **必须报错**并把系统原因**原样**带出来；界面**回滚**到真实状态 | 关不掉自启、写配置失败、换热键失败 |
| **可恢复/只读路径** | 记日志（`WARNING`）+ **退化**，绝不抛异常、绝不阻断 | 配置坏 / 素材坏 / 音效缺失 / 遮罩坏 |

**细则**：

- **绝不 `except OSError: pass`**（"清理类"操作可以尽力而为，但用户要求的写必须报错）。
- **读操作只读**：打开窗口、显示状态**不得写磁盘**（旧项目曾因"读状态顺手自愈"导致打开设置就报错）。
- **失败原因优先于成功状态**：状态栏渲染时 `error` 存在就显示失败原因，**绝不显示假的成功状态**。
- **弹窗 vs 日志**：需要用户知道并可能采取行动的（换角色被拒、重启失败、写失败）→ **弹窗 + 日志**；
  用户无需行动的内部退化 → **只记日志**。**只读查询（`--list-*`）与命令行用法错误一律 stderr + 退出码**，
  因为它们发生在建 `QApplication` 之前（§4.9 的模态弹窗坑）。
- **日志**：`data/deskpet.log`，同时输出到控制台（有的话）；每次启动一行 `===== YYYY-MM-DD HH:MM:SS =====`。
  写日志失败**绝不抛异常**。输出**不要**依赖控制台编码（§4.3）。
- **崩溃兜底**：`data/crash.log`，这一层**只依赖标准库**（包自己可能正是坏掉的那一环），
  并且**必须和 `paths.py` 对一次**（有断言防漂移，§4.13）。
- **模块级调用一律写在函数定义之后**（旧项目曾因此 `NameError`，等于没有诊断）。

### 2.11 启动编排与退出（`app.py`，顺序不要改乱）

1. `logger.install_crash_handler()` —— **最先**（之后任何一步炸了都有 `crash.log`）；
2. `_build_parser().parse_args()` —— **建目录之前**：`--list-*` 是只读查询，不该被"建不出 `data/`"挡住；
3. `paths.ensure_data_dir()` —— 建不出来 = **启动期致命** → 弹窗 + 控制台致命日志 + 返回 1
   （此时**还没装 logger**：日志文件本身住在 `data/` 里，先建目录、再谈往里面写）；
4. `logger.setup_logging()` + `log_startup_banner()` —— 控制台 + 文件；装不上**只降级，不抛**；
5. `ConfigStore().load()` —— 读配置，永不抛；
6. `--list-pets` 在装日志前返回，`--list-actions` 在**建 `QApplication` 之前**返回，
   **`--list-sizes` 在 `QApplication` 之后**返回（要读真实屏幕做自适应）；
7. 建窗口 → 事件循环；退出时记一行"退出（退出码 N）"。

### 2.12 尺寸档位与缩放（task04 定案）

| 项 | 约定 |
| --- | --- |
| 档位真值 | 10 级，写成 `(zoom, subsample)` **精确有理数**（`size.LEVEL_RATIOS`）；像素 = `cell * zoom // subsample`，**全程整数**。**不许用浮点累乘**（0.333/0.666 会让档位塌缩） |
| 默认档位 | `size.DEFAULT_LEVEL = 6`。`config.DEFAULT_SIZE_LEVEL` **只做引用**，别处不许再写 `6` |
| 目标占比 | `size.SCREEN_TARGET_RATIO = 0.24`，按**高度**选档位（不按宽度）。**`CALIBRATION` 是标定基准常量，不是当前屏幕** |
| 档位决定顺序 | **命令行 > `has("size_level")` > 按屏幕自适应**。第二档必须用 `has()`，不能用"值 ≠ 默认值" |
| 越界两套规则 | **命令行**越界 → 报错退出（`argparse`）；**配置/内部**越界 → `clamp_level()` 夹取 + 警告。**别混** |
| 切档顺序 | **先重建该档的帧，再改窗口尺寸**（反了会短暂留白/裁边）。唯一入口 `PetWindow._apply_level()` |
| 锚点 | **右下角锚点不动**（脚底与右边不动，向"左上"长大）。锚点在切档前取、切完档反推左上角 |
| 夹取时机 | **夹取必须发生在写进权威位置之前**（§4.5）。`move_to()` 本身**不夹取**，夹取在 `_move_clamped()` |
| 三种尺寸对齐 | 窗口尺寸 / 内容尺寸 / 精灵渲染尺寸**必须相等**（同一次 DPI 换算，不要乘两遍 dpr） |
| 缩放算法 | **`SmoothTransformation`**（D3 已定案）。放大**只在换档/首次取帧时**做，**绝不每帧做** |
| 帧缓存键 | `(动作, 帧序号, 档位, **镜像**)`，唯一拼键处是 `rendering.FrameRenderer.cache_key()`。**task06 已补上镜像**（§4.7 / §2.14） |
| 缓存上限 | **LRU，上限 `rendering.DEFAULT_CACHE_LIMIT = 64` 张**。不设上限 = 来回切档把内存吃满 |
| 尺寸来源 | 按**帧文件实际尺寸**缩放（`rendering.image_size()`），不是 `spec.cell`——`cell` 与实际不符时只是警告（§2.5），此时以真图为准免得拉伸变形 |

### 2.13 动画推进与调度（task05 定案）

| 项 | 约定 |
| --- | --- |
| 播放方式真值 | **`animation.PLAY_MODES` 一处**：`idle`/`rest` 单向循环、`running_left`/`running_right`/`waiting` 往返、`waving` 一次性。**表外动作按 `oneshot`**（`FALLBACK_MODE`，`weedy_sightseer` 的第 7 个动作走这条） |
| 帧率只消费 | `Animation.frame_ms` **直接用 `ActionSpec.frame_ms`**；`animation.py` **不重复解析、不重复告警**（坏 fps 的警告已经在加载层记过） |
| 调度 | **单次 `QTimer` + 每帧重排**：`advance()` 返回"下一次换帧的毫秒数"，调用方 `start(next_ms)`。**不是**固定间隔空转 |
| **定时器类型** | **必须 `Qt.PreciseTimer`**（见 §4.9 最后一条：`CoarseTimer` 会把 33 ms 上取整到 46.8 ms） |
| 间隔夹取 | `[4, 250] ms`（`AnimationDriver.MIN/MAX_INTERVAL_MS`）：下限防忙等、上限保证退出与信号被及时处理 |
| **"定格"的表达** | `advance()` 返回 `None` → **不 `start()`**，定时器自己停住。**"还在播吗"就是 `timer.isActive()`**，不另设布尔开关（多一个开关就多一处会对不上的状态） |
| 三种返回 `None` | 定格 / 一次性动作停在末帧 / 片段序列播完（同时发 `sequence_done`）。共同点是"**不要再排下一次**" |
| dt 上限 | `animation.MAX_DT_MS = 100`：休眠唤醒后按 `dt` 补齐帧数但**最多补 100 ms 的量**，不跳帧 |
| **一圈的定义** | **回到起点帧且方向朝前**。往返的一圈**含两个折返点**（4 帧区间 = 4 次推进 = 1 圈）；**按"到达末端"计次会把圈数算成两倍** |
| 片段不改本质 | `play_sequence()` **不写 `_mode`**：片段只收窄**帧区间**并接管**何时结束**。`("loop", N)` 段按 `LOOP` 推进（一圈定义才唯一），`"auto"` 段仍按动作自己的方式走 |
| 一次性动作的起点 | **从末帧倒着播**（`waving` 手抬起→收手，翘着手接呼吸更自然；旧实现同此，属观感约定）。片段里则**从区间起点正着播**（显式请求"播这一段"时倒放不是任何人的意图） |
| 单帧动作 | 循环/往返**原地不动但照常排下一拍**；一次性则第一拍就收尾。`advance()` **不许把"没换帧"报成一次换帧**（否则窗口每 33 ms 白重绘同一张图，CPU 偏高） |
| 换帧去重 | `PetWindow._on_frame_due` 里"帧号没变就不重绘"；遮罩同理（§2.8"同动作同档位内复用"） |
| 换档与动画 | `_apply_level()` 按**动画当前帧**渲染，不是 `_frame_index` 快照 |
| `--fps` | **已删除，不得复活**（D14）。唯一调试入口是 `Animation(fps_override=...)` / `set_fps_override()`，**不写回配置** |

### 2.14 右键菜单与镜像（task06 定案）

| 项 | 约定 |
| --- | --- |
| 菜单文字真值 | **附录 B 一处**，落在 `context_menu.py` 的 `TEXT_*` 常量：`调整大小…` / `坐下休息` / `站起来` / `镜像翻转` / `设置…` / `隐藏宠物` / `退出` |
| 菜单 id 真值 | **`QAction.setData()` 承载**（`context_menu.action_id()` 读它）。**接线只看 id，不看文字**——文字改了接线照旧 |
| 菜单顺序 | `MENU_ORDER` 一处：`resize`/`action`/`mirror`/`settings`/`visibility`/`(separator)`/`quit` |
| 每次弹出前重建 | `build_menu(parent, state)` 现造；勾选态与文字按**当前状态**现算，**不留跨次状态**（免得两处状态要对齐） |
| 菜单层级 | **parent 必须是宠物窗口**，否则跟随置顶会丢到别的窗口后面 |
| 未接线项 | `调整大小…`/`设置…`/`隐藏宠物` **`setEnabled(False)`（灰色不可点）而不是隐藏**，保持菜单位置稳定。接上时把 `MenuState` 里对应字段置真即可，`_availability()` 不用改 |
| `坐下休息` ↔ `站起来` | **同一个菜单项**（id 恒为 `action`），文字由 `MenuState.manual_rest` 决定 |
| 手动休息 | `PetWindow._manual_rest` 只表达"右键点出来的那次休息"，**不做任何计时**。自动张望/坐下是 task07 的状态机，**两者别混** |
| **拖动中右键** | **明确忽略**（`contextMenuEvent` 里 `if self._dragging: return`）。理由：拖动是"按下→移动→松开"的连续操作，中途插菜单会把这次拖动切成两段 |
| 镜像适用范围 | **真值在 `config.MIRROR_STATES`**（`idle`/`waiting`/`rest`）；`mirror.MIRRORABLE` **直接引用它**（`is` 同一对象），别再抄一份 |
| 镜像在哪做 | **渲染层** `QImage.flipped(Qt.Horizontal)`——不生成新素材、不改 `asset.json`、不动原 PNG。**`mirrored()` 在当前 PySide6 已弃用，别改回去** |
| 镜像与缓存 | 缓存键 4 维 `(动作, 帧序号, 档位, 镜像)`；漏掉镜像 = "翻转后拿到未翻转的缓存图" |
| 镜像与遮罩 | `apply_mask()` 收的是**已经翻过**的图，所以"能点到的范围"与"看到的画面"永远一致；换镜像走 `PetWindow._on_mirror_changed()` → `_repaint_current()` |
| 镜像与位置 | 水平翻转折点对称 → **锚点与档位不受影响**（实测：翻转前后遮罩外接矩形宽高、窗口位置、尺寸全不变） |
| 镜像记忆 | 按状态分别记忆，写 `config.mirror`；切换动作时**各自沿用**上次的值 |
| 镜像写失败 | `MirrorState.toggle()` 返回 `False` **且不改内存、不发信号** → 勾选回滚，绝不显示假成功 |
| 只翻当前状态 | `_on_mirror_changed` 里 `action != 当前动作` 就直接返回：翻别的状态时当前画面不动 |

**task02 遗留、task06 修掉的一个真 bug（`config.ConfigStore.set()`）**：`set()` 曾经
**先把值写进内存再 `_write()`**，写失败只返回 `False` 而不回滚内存。后果有两条，
第二条是 task06 的写失败自检才抓到的：

1. `get()` 与实际磁盘不符（界面"内存说成功、磁盘是旧值"）；
2. 下一次 `set()` 拿**已经改过的**内存去比"值没变"，走"不写盘"的短路并返回 `True`
   —— 用户看到翻转成功了，**磁盘上一个字节都没落**。

**已修**：写失败时 `self._values[key] = previous` 回滚（见 `config.set()` 里的注释）。
判据是自检里那条"恢复写盘后再 toggle，这次**真的**写进了文件"。

### 2.15 久置行为与光标轮询（task07 定案）

| 项 | 约定 |
| --- | --- |
| 常量真值 | `idle_state.py` 一处：`MOUSE_MOVE_EPSILON = 2`（比较用**平方**）、`REST_MIN_S = 5`、`REST_RATIO = 0.5` |
| 升级规则 | 静置 `T`（配置 `idle_seconds`）→ `waiting`；再静置 `S = max(REST_MIN_S, T × REST_RATIO)` → `rest` |
| 阶段 = 动作名 | `Stage` 的取值就是 `idle` / `waiting` / `rest`，`stage_changed` → 切**同名**动作，**无映射表** |
| `idle_seconds` | **每次判定回调现读**（构造时**不快照**）；坏值 → 20 秒；下限**夹到 1 秒** |
| 计时怎么表达 | **状态机 + 独立 `QTimer`**（`app.IDLE_TICK_MS = 250`）。**绝不挂动画帧回调**（§4.8） |
| 时间从哪来 | 外部喂 `on_tick(now)`，`now=None` 才读 `time.monotonic()`；**不用 `time.time()`** |
| 光标轮询 | `CursorWatch(150)` 自己记上次位置；**位移平方 > 2²** 才发 `moved`；**首帧不算动** |
| 两个例外 | 计时归零但**不切动作**：① `manual`（**已做**）② 拖动中（**接口** `set_suspended(bool)`，task08 接线） |
| 退出收尾 | `finally` 里 `cursor_watch.stop()` + `idle_timer.stop()` |

### 2.16 拖动、命中与位置（task08 定案）

| 项 | 约定 |
| --- | --- |
| 命中判定入口 | `hit_test.hit_test(frame_image, pos, rendered_size, mirrored)`；区域生成 `hit_test.region_for_frame(image, mirrored=False)`。`pet_window.region_for_image()` 只剩**转发**（旧名留给 task01–07 的自检） |
| **窗口不重复判定** | 能收到 `mousePressEvent` 就说明按在遮罩内（区域外由系统级穿透）。事件处理器**不再调 `hit_test()`**——那会造出第二份判定真值。`hit_test()` 供自检 / 诊断 |
| 镜像坐标 | `x' = width - 1 - x`（翻**查询点**，不是翻图）；镜像时**先缩到 `rendered_size` 再翻点** |
| 拖动状态 | `drag.DragState`：`base_pos`（按下时的权威位置）/ `grab_offset` / `press_global` / `last_direction` / `last_target` |
| 拖动信号 | `drag.DragController.drag_started` / `drag_finished(pos)`；`PetWindow.position_changed(pos)` |
| 方向纯函数 | `drag.direction_action(dx, last)`：只看符号（**无速度阈值**）；`dx==0` 返回 `last`（沿用），`last` 也没有则 `None` → 调用方按 `DEFAULT_DIRECTION = running_right` 起跑 |
| 拖动中的动作 | 按下先切 `idle` → 有水平位移才切跑动；**竖直移动不切**（沿用上次方向） |
| 位置决定顺序 | `命令行 --position` > `has("position")` > **缺省贴右下角**（`size.DEFAULT_MARGIN = 24`，用**整块屏幕**的 `geometry()`） |
| 位置来源文字 | `geometry.POSITION_SOURCE_*`：`命令行指定` / `沿用上次` / `默认落点`；**启动行必须打印** |
| 位置格式 | `"x,y"`（**字符串**）；`geometry.format_position()` / `parse_position()` 是唯一的拼法与解析 |
| 越界两套规则 | **命令行**坏格式 → `argparse` 报错退出（rc=2）；**配置**里的坏值 → 忽略 + 一行日志 + 回缺省落点。与"档位越界"同一种分法 |
| 写配置时机 | **只在松手与换档**（`_persist_position()` 是唯一出口）；拖动过程**不逐帧写** |
| 夹取时机 | 夹取在 `_move_clamped()` 里、**早于** `move_to()` 写权威位置（§4.5） |
| 用户位置优先 | `_user_positioned`：拖动过之后**绝不弹回缺省落点**；切档只做右下角锚点保护 |
| 挂起接口 | `PetWindow.set_action_suspended(bool)` → `IdleStateMachine.set_suspended()`；拖动开始挂起、结束恢复（恢复 = **计时归零重排**） |
| `manual` 与拖动 | 拖动开始**退出 `manual`**（"别一直坐着了"，task07 §4 的三条退出方式之一） |
| 双击 | **按下的那一刻**是不是 `manual` 看 `_manual_rest_at_press`（`_start_drag` 会把 manual 退掉，此刻再问永远是假）；处理完必须 `abort_drag()`（见 §4.15） |

### 2.17 尺寸控制栏（task09 定案）

| 项 | 约定 |
| --- | --- |
| 常量真值 | **`control_bar.py` 一处**：`BAR_HEIGHT = 40` / `GAP = 8` / `RESERVED = BAR_HEIGHT + GAP + 4`（= 52）。`PetWindow` **不写第二份**，边距由调用方传（`reserve_bottom(margin)`） |
| 窗口形态 | **独立顶层窗口**（`Qt.Tool` + `FramelessWindowHint` + `WindowStaysOnTopHint`）+ **必须显式 `setAttribute(Qt.WA_QuitOnClose, True)`**（§4.9：否则关掉它 `app.exec()` 不返回）。**不画进宠物画布**（那里的透明区会被当穿透） |
| 布局 | `QHBoxLayout`：`QLabel` + `−` + `+` + `×`，`setFixedHeight(40)`；**控件不用绝对坐标**，`self.move()` 只用于**窗口自身**定位 |
| 固定尺寸 | 栏 `setFixedSize(sizeHint())`、标签 `setMinimumWidth(96)`、按钮 `setFixedWidth(30)`。**理由**：文字长度变化不许撑动布局（"缩放中按钮不动"的前提），实测档位 1↔10 栏宽恒为 293 |
| 文字 | `size.level_text(level, total)` → `{倍数:.2f}x（{档位}/{总数}）`（**全角括号**），如 `1.00x（6/10）`。**格式真值在 `size.py` 一处** |
| 按钮字形 | `−` = **U+2212 减号**、`×` = **U+00D7 乘号**（不是 ASCII 连字符/字母 x）。实测真实素材下显示正常、**不是方框**，未换字符 |
| 定位 | **右边缘与宠物右边缘对齐**（`pet.right()+1-width`，**不是水平居中**）；优先下方 → 上方 → 夹在屏幕内。三种情况**都保持右对齐** |
| 跟随 | **按增量平移**（`ControlBar.follow(dx, dy)`），由 `PetWindow.position_changed` 驱动。**禁止重算锚点**（屏幕边缘会被裁、来回拖会抖） |
| **"缩放中按钮不动"怎么做** | **底部边距机制**：打开控制栏时 `reserve_bottom(RESERVED)` 把宠物整体上移、**底边固定**；切档走右下角锚点（§2.12）→ 底边与右边都不动 → 控制栏一动不动。**判据必须用屏幕坐标**（`mapToGlobal`），窗口内坐标不变只是布局的事 |
| 边距幂等 | `PetWindow._applied_margin` **单独记"已经施加的量"**：权威位置里已含上一次抬升，重算基准底边时要先还回去。否则每次调用再抬一次（实测 776→724→672…）。**判据**：连续 `reserve_bottom(52)` 两次位置不变 |
| 边距生效条件 | **只在位置来自缺省落点时**（`set_reserved(True)`）。命令行 `--position` / 配置里的位置 / **用户拖动过** → `False`，机制不生效（"用户位置优先"，§2.16） |
| 关闭 | 收起栏 + `reserve_bottom(0)` 放回贴屏幕底边（边距归零）；机制已失效时**保持用户拖到的位置** |
| 幂等 | `ControlBarController` **只持有一个 `ControlBar` 实例**；重复打开直接返回 |
| 档位写配置 | `PetWindow.set_size_level()` 里 `_persist_level()` 写 `size_level`。**只在真换档时**（档位没变时函数开头就 `return`）→ 到边界连点**不碰磁盘** |
| **换档不是拖动** | `set_size_level()` 会改宠物左上角 → `position_changed` **照样发出来**。跟随逻辑必须用 `_stepping` 标志**挂起**，否则控制栏会斜着跑（见 §4.17 的真 bug） |
| 菜单项 | `调整大小…` 的 `MenuState.resize_available` = **装没装处理器**（`set_resize_handler()`）。没装 → 仍然灰掉。动作 = **重置静置计时**（`idle_state.on_mouse_moved()`）+ 开关控制栏 |
| 退出收尾 | `finally` 里 `bar_controller.close()`（独立顶层窗口要显式收起，否则它比宠物活得久） |

### 2.18 音效与双击手势（task10 定案）

| 项 | 约定 |
| --- | --- |
| **逻辑名只有两个** | `rest`（主动休息 + 久置自动休息**共用**）/ `chime`（启动音 + 双击交互音）。真值 `sounds.SOUND_NAMES` |
| 映射来源 | **只认素材索引 `sounds` 声明**；无兜底表、不自动认 `sounds/rest.mp3`、不从目录自动发现 |
| 后端（**D4 定案**） | **`QMediaPlayer` + `QAudioOutput`**（要能播 mp3；`QSoundEffect` 只支持 wav）。**不需要额外依赖**，PySide6 自带 `QtMultimedia`（实测可用） |
| 线程 | **所有播放器调用都在 GUI 线程**（`QMediaPlayer` 不是线程安全的，§4.9）。**不建音频线程**；唯一出口是 `SoundPlayer.play()` |
| 音量 | `sounds.VOLUME_MIN/MAX` = 0/100 一处；**越界数字夹取**（1000→100）、**非数字归 0**。`set_volume()` 直接作用到已打开的播放器 |
| 异步 | `preload()` **不阻塞**（立即返回，结果走 `sound_ready` 信号）；启动音用 `QTimer.singleShot(STARTUP_CHIME_DELAY_MS=80)`，预热超时 `PRELOAD_TIMEOUT_MS=2000` 由**另一个定时器**兜底。**绝不 `sleep` / 忙等** |
| **判"能不能播"** | **不能只看 `errorOccurred`**（真平台实测：设备被别的播放器占用时那个信号**根本不发**）。判据是 `player.error()` + `mediaStatus() == InvalidMedia`（`sounds._poll_error()`），由 `settle()` 落定 |
| `settle()` | 预热后调一次把异步结论定下来。**没问出问题就按能播算**（加载慢的好音效不许被判死） |
| 汇报文案 | **格式真值在 `SoundReport.summary()` 一处**：`音效 2/2` / `无音效` / `无音效（未在索引里声明：bgm.mp3）` / `音效 1/2（缺 rest）（无法播放：bgm.ogg）` |
| 三条诊断 | ① 声明了但文件不在；② 格式打不开；③ `sounds/` 里有却没声明。**只记日志**，不弹窗（§2.10 第三类） |
| 休息音效落点 | **`PetWindow._on_stage_changed()` 一处**（阶段变成 `rest` 就响）。主动与自动天然共用，**不按"是谁点的"分** |
| 双击手势 | 双击 → `play_sequence((("waving", None, ("loop", 1)),))` **整段一次** + 播 `chime`，播完 `sequence_done` → 回 `idle`。**不裁帧** |
| **拖动中双击** | **明确忽略**（`drag_controller.active` 时直接返回，不挥手不响） |
| 手势收尾判据 | **`PetWindow._gesture_active`**，**不是** `animation.in_sequence`：`sequence_done` 发出时 `_segments` 已被清空，那一刻问它**永远是假**（见 §4.18 的 bug） |
| 缺 `waving` | 不挥手，但 **`chime` 照播**（它是"双击这个交互"的声音，与素材有没有挥手动作无关） |
| 退出 | `finally` 里 `startup_chime_timer.stop()` + `preload_timeout_timer.stop()` + `sound_player.close()`（句柄不释放 = 再启动一次没声音） |
| **音频格式** | **不维护"哪些格式能播"的清单**：支持什么由 **Qt 的 FFmpeg 后端**决定。播放路径**不按扩展名过滤**，"打不开"由 `_poll_error()` 判定。`KNOWN_AUDIO_SUFFIXES` 只影响日志措辞；"到底支持什么"运行时问 `decoder_file_formats()` / `decoder_audio_codecs()`（启动日志会打出来）。⚠ **打包必须带上 Qt multimedia 插件与 FFmpeg 动态库**，否则"开发机能响、打包后没声音" |

### 2.19 设置窗口与写入结果（task11 定案）

| 项 | 约定 |
| --- | --- |
| **字段描述表** | **`settings.FIELDS` 一处**（有序 = 界面顺序）。`FieldSpec` 六列：`group` / `key` / `title` / `hint` / `kind` / `minimum` / `maximum` / `unit` / `implemented`。**界面代码不许手写行顺序或控件类型** |
| `FieldKind` | `COMBO`（角色）/ `CHECK`（自启）/ `KEYSEQ`（快捷键）/ `SLIDER`（音量、久置时间） |
| **`key=""` 的行** | `开机自启` **没有配置键**：它是系统状态（task14 的 `autostart`），不是 `config.json` 的一项。别为它造一个假键 |
| 未实现的项 | `implemented=False` → 控件 `setEnabled(False)` + 标题追加 `（暂未开放）`，**不是隐藏**（布局稳定）。本 task：`开机自启` / `呼出快捷键` |
| 窗口形态 | `QDialog` + **`setModal(True)`**（抓住输入焦点）；**不加 `WindowStaysOnTopHint`**——宠物仍按既有约定在最上层飘着（已确认的取舍） |
| 尺寸与截断 | `560 × 516` 逻辑单位 + `setMinimumSize` + 五行装在 **`QScrollArea`** 里；状态栏与按钮行在滚动区**之外** → 拉小也点得到按钮。**不要**靠"把数字调大" |
| 定位 | `center_on_screen()`：水平居中、**垂直取屏幕高度的 1/3 处**并夹进 `availableGeometry()`（`VERTICAL_RATIO` 一处） |
| **单例** | `app.SettingsController`：**只持有一个窗口**；重复 `设置…` → `reload_from_config()` + `center_on_screen()` + `raise_()`（**不 show 第二个**）。关掉后**释放引用**（`WA_DeleteOnClose` + `finished` → `_dialog=None`） |
| 角色候选 | `settings.pet_choices(current)`：**列出所有发现的包**（坏的也列）；`PetChoice.label(current)` 给出 `● 展示名（命名空间）` 与 `　⚠ 不可用`。当前项**排第一位**；配置指向的包已消失时**补一项**（否则下拉没有当前项） |
| **写失败回滚** | `ConfigStore.apply()` → `SetResult(ok/changed/error/detail)`；失败时控件**回滚到 `store.get(key)`**（配置里的真值），状态栏 `设置失败：<系统原文>` |
| **不许有写入回路** | **`SettingsWindow._applying` 守卫标志**：凡是程序自己动控件（初值填充 / 回滚）一律置真，槽函数见真即返回。**没有它 → 回滚又触发一次写入 → 死循环**（自检用计数器盯着"一次改动只写一次"） |
| **失败不报成功** | `settings_changed(key, value)` **只在写入成功时发**；状态栏渲染先看 `error`（`_last_failure`），有就显示失败原因，**不在拼字符串时决定** |
| **读操作只读** | 打开/重开设置窗口只 `get()`，**不写盘**（判据：`config.json` 都不该被创建）。填充控件必须包在 `_applying` 里 |
| 逃生门 | `打开启动文件夹` 在**左下角**（按钮行里排最左），动作 = `QDesktopServices.openUrl(QUrl("shell:startup"))`。**不自己拼 `%APPDATA%`**（启动文件夹的推导真值留给 task14） |
| 快捷键骨架 | `keycapture.KeyCaptureEdit`：`MODIFIER_ORDER = Ctrl→Alt→Shift→Win`；**只看实测按下的键**（不看 `modifiers()`）；修饰键单独按下不算；至少一个修饰键；Esc 取消；**本类不碰配置**。展示名 `display_text`（**别叫 `text()`**，会与 Qt 撞名） |
| **角色切换的当前边界** | 可用包 → 写 `pet` + 状态栏 `重启后生效`（**自动重启属 task13**）；坏包 → **不写配置**（不让下次启动必然踩同一个坑）+ 提示详情 + 界面退回真实状态。task13 补：询问重启、坏包时把配置改回默认形象、写失败提示"保存失败，重启后会回到原来的角色" |
| 菜单接线 | `设置…` 的可用性 = **装没装处理器**（`PetWindow.set_settings_handler()`），与 `调整大小…` 同一模式。处理器里**先 `idle_state.on_mouse_moved()`**（重置静置计时）再开窗口 |
| **任务栏高度** | `QScreen.geometry()` 在真机上是 **1392**（1440 − 48 任务栏）；`availableGeometry()` 也是 1392。**别把 1440 当屏幕高度写进断言** |

### 2.20 全局热键与隐藏呼出（task12 定案）

| 项 | 约定 |
| --- | --- |
| **D5 最终选择** | **子线程 + `GetMessageW` 消息循环 + `queue` + GUI 线程 100 ms 取件定时器**（task12 §4 两条路线里的第二条）。`hotkey_filter.py` **没有创建**——原生消息过滤器那条路没走 |
| 为什么选它 | 后台线程建自己的窗口、跑自己的消息循环 → **不碰主线程的消息钩子**（没有"隐藏期间钩子被系统摘掉"的风险）、**唤醒是事件驱动的**（平时阻塞在 `GetMessageW`，隐藏期间 CPU 0）、**退出安全**（一条 `PostThreadMessageW(WM_QUIT)` 就收工） |
| **120 ms 轮询** | **已去掉**。120 那个数一次都没出现在代码里。GUI 线程只剩 `PICKUP_INTERVAL_MS = 100` 的**取件**定时器（从内存队列取，空队列时是微秒级；**不是轮询系统状态**） |
| ⚠ **注册认线程** | `RegisterHotKey` 必须在**拥有该窗口的那个线程**里调用。从别的线程调会返回 **`1408`（无效窗口；它属于另一线程）**，**不是** `1409`。所以 `hotkey._HotkeyThread.call()` 把所有 Win32 调用都投回子线程执行 |
| 常量真值 | `hotkey.CANDIDATES`（回退链五条）、`hotkey.DEFAULT_HOTKEY`（**与 `config.DEFAULTS["hotkey"]` 同源，有模块级断言守着**）、`hotkey.PICKUP_INTERVAL_MS = 100`、`hotkey_spec.MOD_*`（系统掩码唯一真值） |
| **两套掩码** | 系统热键：`Shift 4 / Ctrl 2 / Alt 1 / Win 8`（`Ctrl+Alt+Shift` = **`0x7`**，不是 `0xF`）。界面状态位那套（`0x0001/0x0004/0x0008`）**本 task 一次都没用到**——录入只看 `event.key()` |
| 掩码转换位置 | **只有一处**：`hotkey_spec`（`MOD_*` 常量 + `parse_combo` / `format_combo` / `win_mods_to_names`）。别的模块不许手算掩码 |
| 说法格式 | `"Ctrl+Alt+Shift+P"`，**展示顺序固定 `Ctrl → Alt → Shift → Win`**。解析大小写不敏感、收 `Control`/`Meta`/`Windows` 等别名 |
| 校验 | 必须**至少一个修饰键** + **恰好一个主键**；非法 → `ComboError` 带原因（文案 `快捷键必须包含 Ctrl / Alt / Shift / Win 之一`） |
| 注册顺序 | 首选（配置里的）→ 失败按 `CANDIDATES` 依次试（跳过与首选重复的）→ 全失败 `combo = None` |
| 回退后 | **写回配置**（只在 `changed=True` 时写一次）+ 日志 `原快捷键 X 不可用，已改用 Y` |
| **`combo is None` 时必须禁用隐藏** | 菜单 `隐藏宠物` 的可用性 = **装了处理器 AND 真有热键**（`MenuState.visibility_available`）。`hide_pet()` 自己**再拦一道**返回 `False`（默认 `_hotkey_available = False`，即"不知道就按没有处理"） |
| 提示文案真值 | `context_menu.REASON_NO_HOTKEY` 一处：`没有可用的呼出快捷键，隐藏后将无法呼出。请先在「设置」里指定一个快捷键。` |
| 隐藏语义 | **保留状态不销毁**：只 `hide()`，窗口 / 帧缓存 / 档位 / 位置全留；**动画定时器停掉**（隐藏期间不烧 CPU），呼出时**从原来那一帧接着播** |
| 呼出置顶 | `show_pet()` 里 `setWindowFlag(Qt.WindowStaysOnTopHint, True)` **然后**才 `show()` + `raise_()`（改标志会让系统重建底层窗口，顺序反了等于"显示完立刻被藏掉"） |
| 热键 = 切换 | 按一次隐藏、再按一次呼出（用户 2025 确认）。菜单项是**单向**的"隐藏" |
| **分两拍处理** | `_on_hotkey_pressed` 里 **`QTimer.singleShot(0, self.toggle)`**——不当场动窗口。理由：热键回调发生在"Qt 正在处理某个消息"的栈上，直接 `show()` 会让窗口管理器在嵌套激活流程里重入 |
| 控制栏跟着走 | `ControlBarController.hide_for_pet()` 只 `hide()` 栏，**不碰位置与预留边距**；`was_visible` **单独记标志**（宠物藏起来时 `isVisible()` 永远是假，拿它当判据会让"呼出时放回栏"失效） |
| 换热键 | 设置窗口那一行进 `HotkeyChanger`：**先注册成功、再写配置**（反了会让磁盘上留下一个用不了的组合）。失败 → `restore(previous)` **真的把旧热键抢回来** + 控件回滚 + 状态栏说原因。**绝不落到"没有热键可用"** |
| ⚠ 每个新设置窗口都要重装 changer | `SettingsController.open()` 里每造一个 `SettingsWindow` 都要 `set_hotkey_changer()`——不装的话第 2 次以后的录入**只写配置、不注册系统热键**（典型的假成功） |
| 退出 | `finally` 里**最先**调 `hotkey_controller.stop()` → `manager.unregister()`（`PostThreadMessage(WM_QUIT)` → `join(2s)`）；子线程 `finally` 里 `UnregisterHotKey` + `DestroyWindow` |
| 窗口 proc 签名 | `WNDPROC` 四个参数与返回值**都必须是指针宽度**（`WPARAM`/`LPARAM`/`LRESULT`）；而且回调对象**必须模块级持有强引用**（`_WINDOW_PROC`），否则是野指针。详见 §4.20 |

### 2.21 单实例与重启自己（task13 定案）

| 项 | 约定 |
| --- | --- |
| **D6 最终选择** | **命名互斥体**（`win32.create_mutex()` → `CreateMutexW`）。`QLocalServer` **没有采用**：它要在临时目录放套接字文件、且必须在 `QApplication` 之后才能用 |
| 作用域 | **仅当前登录会话**。靠"名字**不以 `Global\` 开头**"+ 用户名（`deskpet-single-instance.<user>`）。**不许**加 `Global\`（那会让所有登录用户互相排斥） |
| 常量真值 | `single_instance.MUTEX_PREFIX`；`win32.ERROR_ALREADY_EXISTS = 183` |
| `acquire()` 三态 | 抢到 → 句柄；**已有实例 → `None`**（调用方**安静退出 rc=0**）；**机制不可用 → 空壳句柄**（`held=False`,`available=False`，**照常启动**） |
| **"不可用"≠"已有实例"** | 两者的区分就是上面这两行。把"不可用"也返回 `None` = 护栏坏掉时**桌宠起不来**，比多开一个糟得多 |
| **放锁 = 释放所有权 + 关闭句柄** | ⚠ **实测**：只 `ReleaseMutex` 时 `ERROR_ALREADY_EXISTS` **照样为真**（判据是"这个名字的内核对象还在不在"，不是"有没有人占着"），新实例**进不来**。见 §4.21 |
| 启动顺序（硬约束） | `--list-pets` → `--list-actions` → **单实例** → 建 `QApplication`。`--list-sizes` 是特例：它必须晚于 `QApplication`，所以**压根不抢锁**（`query_only_run`），不是"抢了再放" |
| 退出收尾 | `finally` 里 `instance.release()`。**走重启路径时它已经在 `restart_self()` 里放过了**（`release()` 幂等） |
| `--restart-delay` | 默认 **1.2** 秒（`restart.RESTART_DELAY_DEFAULT`）。**新进程先等它再抢锁**；参数名真值 `restart.RESTART_DELAY_ARG`。**缺省时一秒都不等**（日常双击启动不受影响） |
| 为什么可以用 `time.sleep` | 这个等待在**建 `QApplication` 之前**（`app._wait_before_lock()`）——没有事件循环，`QTimer` 在这里永远不会触发。与 task10 音频预热禁 `sleep` **不冲突**（那一处在事件循环里、阻塞的是界面） |
| 交接两边一起兜 | **旧进程主动放锁 + 新进程等 1.2 秒**。缺任何一边都会出现"点了重启桌宠没了" |
| 重启顺序 | **先拉起新实例、再关自己**（`restart.restart_self()`）。关自己走 **`app.quit()`**（`QTimer.singleShot(0, …)` 分两拍），**不是** `sys.exit()`——后者会跳过收尾（热键/音效设备不放，新实例没声音） |
| ⚠ **必须真判返回值** | `QProcess.startDetached()` 在 PySide6 6.x 返回 **`(ok, pid)` 元组**，`bool(整个元组)` **永远为真** → 重启失败被当成功 → 旧实例放锁退出、新实例没起来 = **桌宠彻底消失**。取 `result[0]`。见 §4.21 |
| 失败不退出 | 启动失败 → 弹窗 `重启失败，请手动关掉再启动一次`（`restart.RESTART_FAILED_MESSAGE`）、**不放锁**、当前实例继续跑 |
| `build_args()` | 打包 → `sys.executable`（exe）；源码 → **同目录的 `pythonw.exe`**（不带控制台）+ `paths.program_dir()/run.py`。只带 `--restart-delay`（档位/位置本来就在配置里） |
| 工作目录 | 显式传 `paths.program_dir()` 给 `startDetached()` 的第三个参数（**不继承父进程 cwd**） |
| 换角色流程 | `settings` 只问"用户同意吗"（`ConfirmHandler`）与"帮我重启"（`RestartHandler`），**不碰 `QProcess`、不弹询问框**；两件事由 `app.RestartController` 提供 |
| 坏包 | 提示详情 + 退回默认形象 + **把配置改回默认形象**（写**空串** = 回到"没表态"，**不是**写 `weedy`）+ **不重启** |
| 写失败 | `保存失败，重启后会回到原来的角色`，**且不重启**（磁盘上还是老角色，重启只会让用户看到"换了但没换"） |
| ⚠ 每个新设置窗口都要重装 | 与 task12 的热键同一条教训：`SettingsController.open()` 里每次都 `set_restart_handler()`，否则**第 2 次以后换角色只写配置、不问也不重启** |
| 角色回退写回配置 | `app._persist_pet_fallback()`：配置里的包不可用 → 写回空串。**只在 `query_only=False` 时调**（只读查询一个字节都不许写） |
| 时序留痕 | `logger.log_restart_stage()`，前缀 `重启时序[等待|抢锁|放锁]`（验收要从日志里读出这个时序） |

### 2.22 开机自启（task14 定案）

| 项 | 约定 |
| --- | --- |
| **读与修分开** | `autostart.query_state()` **纯只读**（打开设置窗口走它）；写只有 `enable()` / `disable()` / `cleanup_legacy_markers()` 三个入口，**只能由用户明确动作触发** |
| 判定 = "能不能用" | **不是"文件在不在"**：用 `read_shortcut.ps1` 读回目标，读不出 → 坏；指向别处/参数不符 → 未开启。`.lnk` 不在但 `.cmd` 在 → 按 `.cmd` 判 |
| 常量真值 | `autostart.py` 一处：`SHORTCUT_NAME = "deskpet.lnk"` / `FALLBACK_NAME = "deskpet.cmd"` / `STATUS_PREFIX = "开机自启："` / 五段状态文案 / `LEGACY_MARKERS` / `STARTUP_RELATIVE`。`settings.STATUS_PREFIX` **直接引用**它 |
| ⚠ 文案两类 | **完整句子**（`TEXT_DISABLED`/`TEXT_BROKEN`/`TEXT_ELSEWHERE`，自带"未开启"）与**后缀**（`TEXT_WORKDIR_SUFFIX`/`TEXT_FALLBACK`，接在"已开启"后）。混用会渲染出 `已开启已开启［…］`（第一版就是这么错的） |
| 自我定位 | `paths.autostart_target() -> (目标, 参数)`：打包 → exe 自己；源码 → 同目录 `pythonw.exe` + `<程序目录>/run.py`。**绝不写死**（与 `restart.build_args()` 判据一致，**故意不合并**） |
| 启动文件夹 | `startup_dir()`：向系统问（`[Environment]::GetFolderPath('Startup')`，就是 `SHGetKnownFolderPath`）；问不到退回 `%APPDATA%` 推导 + 警告。**进程内缓存**一次（`_startup_dir_cached()`），否则每渲染一次状态就起一个 PowerShell |
| 外部脚本 | `tools/make_shortcut.ps1` / `tools/read_shortcut.ps1`，**UTF-8 with BOM**（§4.3）。结果**写文件再 `utf-8-sig` 读回**，**不解析 stdout** |
| 结果文件 | 放**临时目录**（`mkstemp`），**绝不放启动文件夹**（那里任何文件都会被当启动项，§4.10） |
| 子进程 | 固定位置 `C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe`（**不用 `pwsh`**）+ `-NoProfile -NonInteractive -ExecutionPolicy Bypass`，并用 `STARTF_USESHOWWINDOW`/`SW_HIDE` **不闪黑框** |
| **`enable()` 的成功判据** | 写完**再做一次 `query_state()` 核验**（"文件存在"≠"功能正常"）；核验不过 → `error` 带出来，**不算成功** |
| `enable()` 的顺序 | 先删残留 `.cmd` → 试 `.lnk` → 成功就返回；失败 → 写 `.cmd`；**两条原因都进日志**（`logger.log_autostart_outcome`，前缀 `自启[开启\|关闭\|清理]`） |
| `disable()` 幂等 | 本来就没有 → `enabled=False`、`error=None`（**幂等成功**）；删不掉 → `error` 带系统原文 + `enabled=True`（没删掉就是还开着） |
| **只删两个名字** | `disable()` 只删 `deskpet.lnk`/`deskpet.cmd`；`cleanup_legacy_markers()` 只删 `LEGACY_MARKERS` 清单里的名字。**不做通配删除**（会误伤用户的启动项） |
| 回退文案 | `.cmd` 那条路状态是 `已开启［回退方式：登录时会闪一下命令行窗口］`，`error` 仍是 `None`（**这是成功，不是失败**） |
| 诊断文案 | 最可能的原因 + 逃生门写在 **`HINT_BLOCKED`**（日志里），**不塞进状态栏那段固定文案**；**不写**"重新开启即可修正"这种没验证过的承诺（§4.2） |
| 界面接线 | `settings` 那一行**没有配置键**（`FieldSpec.key=""` + 内部标记键 `settings.AUTOSTART_KEY`）；值来自只读查询，写在 `_on_autostart_check()` 里走处理器 |
| **回滚的真值来源** | 失败 → `_rollback_autostart()` **重新查一次**系统状态（不是取反、也不是回滚到打开窗口时的快照） |
| ⚠ 成功**不再查第二次** | `AutostartOutcome` 是**唯一结论**。第一版写成"再查一次、以查询为准"，结果是"处理器写进假文件夹、界面查真文件夹"→ 把刚做成的结果**覆盖成相反的值**（假的失败），而且 `enabled` 取查询、`status_text` 取处理器会自相矛盾 |
| 清理挂在哪 | `app.AutostartController.open_hook()`（打开设置窗口）+ `apply()`（勾选/取消）。**不挂启动路径**——启动不是用户对自启的明确表态 |
| `_applying` 守卫 | 自启那一行的初值填充 / 回滚 / 成功刷新**都要包在守卫里**：`setChecked()` 会发 `toggled`，没有守卫就等于"打开设置窗口顺手写一次系统自启项"（正好撞头号红线） |
| 每个新窗口都要重装 | 与 task12 换热键、task13 重启同一条：`SettingsController.open()` 里每次都 `set_autostart_handler()`，否则**第二次打开设置勾选只改界面、不动系统** |

**⚠ 打包待办（本阶段不打包）**：两个 `.ps1` 是**外部文件**，
打包数据必须**显式声明**（`datas` 里带上 `tools/make_shortcut.ps1` /
`tools/read_shortcut.ps1`，且 `autostart.tools_dir()` 按
`paths.program_dir()/tools` 找）。漏掉的表现是"**打包后开机自启永远显示不可用**"，
而**源码运行完全看不出来**——`make_shortcut.ps1` 找不到时 `enable()`
会走 `.cmd` 回退，两条都失败才报错（task14 §4 / §4.3 同一类坑）。


---

## 3. 新对话工作流

### 3.1 会话开始（固定顺序）

0. **读 §A（开工纪律）** —— 最高优先级，尤其 A.1（禁止无限试错）与 A.2（减少非必要越界改动）。
   **没读这一节就开始动手，是本项目最常见的跑偏方式。**
1. 读本文件 **§5 当前进度** —— 确认上一个 task 完成了什么、下一个是哪个。
2. 读 **对应的 `tasks/taskNN.md`** —— 全文，尤其"不要做"与"验收标准"。
3. 读 `requests.md` —— 项目红线（不自动装依赖 / 不改 `assets/` / 不自行打包 / 一个阶段一次）。
4. 如果卡里要求的某个**待决策项**还没定（§5），**先问用户**，不要自己选。

### 3.2 会话进行中

- **一次只做一张 task 卡**，不提前做后面的功能。
- 需要新依赖 → **停下来问用户**（只允许 `pip`，不要自己装）。改动 `assets/` → **必须先问**。
- 每完成一个**可独立运行**的阶段 → 可以提交：`feat(阶段): 描述`（提交信息写到仓库外的临时文件再引用）。
- 遇到"文档说的和实际不一致" → **以实现为准**，并**立刻回来修订文档**（不要留两份说法）。
- 遵守 §A；每完成一个模块/阶段用一句话自查："这一轮有没有改到卡范围之外的东西？有没有在试？"

### 3.3 会话结束（固定收尾）

1. 逐条跑完 task 卡的 **§6 验收标准**（能自动化的就自动化，不能的写清"人工怎么看"）。
2. **更新本文件 §5 当前进度**：完成情况 + 一句实际结果 + 下一个 task。
3. 把本 task **新踩到的坑 / 新定下的约定**写进本文件（§2 相应位置或 §4）。
4. 我删了别管这条
5. 若实际实现与 task 卡有出入 → **回来修订 task 卡**。
6. 按 `requests.md` 执行 `git commit`（格式 `feat(阶段): 描述`）。
7. **§A 自查（收尾必做，写进提交信息或 §5）**：有没有同一个问题试了 3 次以上？有没有改到卡范围之外？
   **文档改动量 vs 代码改动量谁大**？临时调试代码 / 探针脚本清干净了没有？

---

## 4. 已知坑与注意事项



### 4.1 默认值与真值：分散就会分叉

- 默认形象的真值**只允许存在一处**；配置项 `pet` 的默认值是**空串**（"没表态"），不许在配置里再写一次形象名。
- 默认尺寸档位同理：**引用 `size.DEFAULT_LEVEL`**，不许另写一个 `6`。帧率的遗留表与 `DEFAULT_FPS` **各只允许一处**。
- 尺寸档位必须**先确定可达集合**（精确有理数），再从中挑档位；
  "先定 10 个浮点目标再挑最接近的组合"会让多个档位**塌缩到同一尺寸**。**约束先行。**

### 4.2 "文件存在" ≠ "功能正常"；"读" ≠ "写"

- 判断"功能有没有生效"要**检查它到底能不能用**（读回快捷方式目标），不是看文件在不在。
- **读状态一律只读**；"修"只在用户明确操作时发生。*验证方式*：把写函数换成会抛异常的桩，证明只读路径走不到写路径。
- **别用缓存/标记文件代替真实状态**，尤其当那个文件位于**会搬家**的目录时。
- 诊断信息里**不要写"重新开启即可修正"这种没验证过的承诺**。

### 4.3 失败路径本身也会失败

- **错误处理自己也会炸，而且炸得比原错误更隐蔽**。写错误路径时要想"这条路径自己会不会炸"。
- **别靠解码子进程的标准输出来拿错误信息**：子进程输出编码随环境而变（同一脚本一种环境 UTF-8、
  另一种 GBK），猜错的表现是**报错信息变乱码——把唯一的线索毁掉**。
  规则：**让脚本把结果写进文件（UTF-8 with BOM），父进程用 `utf-8-sig` 读回来**。
- 含非 ASCII 的 `.ps1` **必须存成 UTF-8 with BOM**，否则 PowerShell 5.1 按 ANSI 解码 → 中文乱码 →
  语法都过不去 → 用户只看到最后一步的 `Permission denied`，与真正原因毫无关系。
- **调试输出写文件，不要靠重定向**：进程会在文件可读之前退出。

### 4.4 给人改的 JSON 要容 BOM

- 按纯 `utf-8` 读带 BOM 的文件会在**第 1 个字符**就解析失败，报"第 1 行第 1 列"，**完全看不出跟 BOM 有关**。
- 规则：凡是**人写的** JSON（`asset.json`，以及将来任何给人编辑的索引）一律用 **`utf-8-sig`** 读；写仍用无 BOM 的 `utf-8`。

### 4.5 越界的位置值不能存进"权威位置"

- 只有最终几何被夹到屏幕内、而**权威位置变量**存原始值 → 换过显示器后桌宠可能在屏幕外，写进配置后还会**跨次启动带过去**。
- 规则：**夹取必须发生在存进权威位置之前**。断言：用 `(99999, 99999)` 与 `(-500, -500)` 两种越界值盯着。

### 4.6 拖动：不许回读窗口坐标、不设速度阈值、不逐帧写配置

- 位移要用**自己记的基准位置**算（异步生效会读到旧值，表现是"只走一小截"）。
- 切跑动动作**只看水平位移的符号**；**不设速度阈值**（会让慢速拖动时动作来回闪）。
- 纯竖直移动**沿用上次方向**；完全没有方向依据时以朝右起跑。
- **只点一下没拖动**也要做一次完整的位置校正通知（处理"拖到边缘被夹住、位置没变"）。
- 拖动过程**不逐帧写配置**（只在拖动结束与换档时写），否则临时文件满地都是。
- **task01 定下的最小实现（后面沿用）**：
  - `PetWindow._base_pos` 是**权威位置**，**只有 `move_to()` 会写它**；不许用 `pos()` / `geometry()` 回读。
  - 按下时把当时的 `_base_pos` 存成**拖动基准**；`drag_by(dx, dy)` 的参数是**相对按下点的累计位移**
    （不是相邻两次移动的增量）。**按增量累加会把窗口越拖越飞。**
  - 命中由 `setMask` 负责：能收到 `mousePressEvent` 就说明按在图形上，所以"只有按在人物身上才起拖"**不需要**在事件里再判坐标。

### 4.7 切换/换帧后，衍生数据必须失效重算

- 旧表现：整批换帧后命中遮罩还停在上一套画上（实测 70 帧里 69 帧对不上）。
- 新架构下"衍生数据"从**文件**变成了**内存缓存**，风险一样甚至更高：缓存键必须含**全部**影响渲染的维度：
  `(动作, 帧序号, 档位, 镜像)`。
- `setMask` 的 `QRegion` 同理：换帧/换档/换镜像都要失效重算（否则表现为"人物变了但可点区域没变"）。

### 4.8 不动鼠标就不能用"轮询动画帧"来计时/测温

- 全屏鼠标活动**没有信号可订阅**，所以读 `QCursor.pos()` 的轮询是必要的；但**计时本身**要由状态机 + 定时器表达。
- **热键/隐藏相关的轮询绝不能挂在动画帧回调上**：窗口隐藏后精灵停帧、帧回调不再触发，热键就永远没人处理——
  **桌宠再也叫不回来**。
- 光标静止时的 **1px 抖动不算"动了"**（阈值 2px）；**首次看到光标按"没动"处理**。

### 4.9 Qt / PySide6 特有的风险（旧项目没有）

| 风险 | 说明与对策 |
| --- | --- |
| 定时器**不保证准点** | 不要写"每 83 ms 加一帧"；要么用绝对时间差吸收，要么"算完一帧再重排"。`frame_ms` 可能是小数 |
| **信号回路** | 界面里"回滚控件值"会再触发一次"值变化 → 写配置"。必须**屏蔽信号 / 加守卫标志** |
| `setMask` 的**锯齿** | **已定案的取舍**。task01 实测（占位圆角矩形）：放大 6 倍看外缘有约 **1 像素**台阶，1 倍观察几乎看不出。真实素材（源 GIF 是 1 位 alpha，边缘天生是台阶）叠加后的观感等 task08 补记。**不要**改成"自己按 alpha 判定"——那会失去真穿透 |
| **坐标两种** | `event.position()`（窗口内）vs `event.globalPosition()`（屏幕）；高 DPI 下还差一个 `devicePixelRatio`。**不可混用** |
| `QPixmap` **只能在 GUI 线程** | 跨线程只能传 `QImage` |
| **DPI 缩放** | 屏幕自适应需要"真实屏幕 + DPI 缩放"，所以**窗口必须先起来**再决定档位 |
| **`QMediaPlayer` 不是线程安全的** | 所有播放器调用都在 GUI 线程 |
| **`QShortcut` 不是全局热键** | 隐藏后窗口没有焦点，只能用 Win32 `RegisterHotKey` |
| **两套修饰键掩码数值不同** | 界面状态位 `Shift 0x0001 / Ctrl 0x0004 / Alt 0x0008`；系统热键 `Shift 0x0004 / Ctrl 0x0002 / Alt 0x0001 / Win 0x0008`。`Ctrl+Alt+Shift` 在系统热键里是 **`0x7`** 不是 `0xF`；**`0x0004` 在事件状态位里是 Control 不是 Shift**。转换**只允许一个函数** |
| **AltGr 报成 Alt** | 中文键盘布局下 AltGr = Ctrl+Alt。录入快捷键**以实测按下的键为准**；**不能无条件剥掉 Alt**（默认组合含 Alt） |
| `QProcess.start()` 会带走子进程 | 重启必须用 `QProcess.startDetached()`，并**真判返回值** |
| `QDialog` 内容过高截断 | 不要靠"把窗口调大"，用 `setMinimumSize` + `QScrollArea` + `QDialogButtonBox` |
| **`QPainter` 按 paint device 的 `devicePixelRatio` 缩放坐标系** | 画到带 DPR 的图/窗口上时坐标**一律用逻辑单位**，**不要**再自己乘一遍 dpr（乘了就是缩放两次：task01 实测 250 逻辑单位的图被画成 313、右下角被切掉）。**正确顺序**：先 `setDevicePixelRatio()` 再用逻辑坐标画，或画完才标注 DPR。**只在缩放 ≠ 100% 时才显形**，最容易静默出错 |
| **`Qt.Tool` 窗口不会让事件循环退出** | Qt 不为 `Qt.Tool` 设 `WA_QuitOnClose`（实测 `False`），且 `Qt.Tool` 不计入"最后一个主窗口" → `close()` 之后 `app.exec()` **不返回**（"Esc 退不掉"）。**必须**显式 `setAttribute(Qt.WA_QuitOnClose, True)` |
| **`QWidget` 没有 `hasMask()`** | 遮罩要问 `mask()`：`window.mask().isEmpty()` 才是"没设遮罩"。写 `hasMask()` 会 `AttributeError`（启动即崩） |
| **offscreen 平台插件不支持 `setMask`** | `QT_QPA_PLATFORM=offscreen` 跑自检时插件会打印 "This plugin does not support setting window masks"，"遮罩生效"**测不出来**。**点击穿透必须在真平台验证**：可自动化的事实是 Win32 `WindowFromPoint` 返回**另一个窗口**才算真穿透 |
| **`QScreen.grabWindow(0)` 的 alpha 永远是 255** | 它抓的是**合成后**的屏幕，桌面本身不透明，所以"截图里窗口四角 alpha=0"**测不出透明**。**验证透明只有两条路**：① 颜色比对——窗口内空白区与窗口外桌面**同色**；② `WindowFromPoint` 判穿透。想直接看 alpha 只能读**帧图本身** |
| **`show_fatal_error()` 是模态的，会阻塞事件循环** | 它内部 `dialog.exec()`。在**已经建了 `QApplication`** 之后调用它，进程会停在那里等用户点确定，命令行自动化跑不下去（`--exit-after` 也救不了）。**规矩**：只读查询与命令行给错值**都放在建 `QApplication` 之前**，用 stderr + 非 0 退出码收场；弹窗只留给"用户可能是双击启动、没有终端可看"的**启动期致命** |
| **PNG 尺寸不能读在偏移 8** | PNG 是"签名(8) + chunk 长度(4) + chunk 类型(4) + 宽(4) + 高(4)"，**宽度在偏移 16**。读在 8 会拿到 chunk 长度（13）：`cell` 一致性检查曾报出荒唐的 `13 × 1229472850`。判据：先验 `header[12:16] == b"IHDR"`，再读 16/20 |
| **`QTimer` 默认是 `Qt.CoarseTimer`，会把间隔向上取整到系统时钟节拍**（task05 实测，**必用 `PreciseTimer`**） | Windows 的默认定时器精度是 **15.6 ms**。请求 33 ms 时 `CoarseTimer` 实际给出 **46.8 ms**（33→46.8），于是"素材是 30 fps、放出来只有 21 fps"，且**看起来完全像是动画逻辑写错了**。`setTimerType(Qt.PreciseTimer)` 后实测 **33.6~33.8 ms**，与素材的 33.3 ms 只差半毫秒。判据：拿一个只挂 `AnimationDriver`、**不建窗口**的探针量平均间隔——干干净净的 33.6 ms 就是对的。 |
| **真窗口下会比纯调度慢一截，这是 Windows 合成器的开销，不是动画 bug** | 同一套驱动在**可见的带遮罩半透明窗口**上每帧重绘，实测从 33.6 ms/帧变成 **41.7 ms/帧**（约 74 帧 / 3 秒，而非 90 帧）。逐项 profile 过：`renderer.frame()` 命中缓存、`pixmap.toImage()`、`setMask()`、`update()`、`region_for_image()` **全都在 1 ms 以内**，所以差值来自窗口重绘本身（遮罩窗口每帧变形状，合成器要重做一次）。**验收节奏时要用"纯调度"那个数**，别拿真窗口的数去否定引擎（差点当成产品 bug 改坏逻辑）。 |

### 4.10 与框架无关、容易忽视的系统级事实

- 「启动」文件夹里**任何**文件都会被 shell 当启动项尝试执行（**与扩展名无关**），所以那里**只能**放自启项本身、
  且**恰好一个**；状态/标记文件一律放用户数据目录。
- 热键必须是**组合**（至少一个 `Ctrl`/`Alt`/`Shift`/`Win`），只能有一个主键，注册要**按候选链回退**并写回配置；
  **一个可用热键都没有时必须拒绝隐藏**（否则叫不回来）；**退出时必须彻底释放**。
- 单实例作用域是**当前登录会话**；放在**只读查询（`--list-*`）之后**。
- 重启自己：**先拉起新实例再关自己**；新进程带 `--restart-delay`（**默认 1.2 秒**）先等再抢锁；
  旧进程**主动放锁**；子进程**完全脱离**。
- **默认落点用整块屏幕的 `geometry()`，不是排除任务栏的 `availableGeometry()`**：附录 C 的"边距 24 逻辑单位"
  是从**屏幕边缘**量起的。**后果**：底部有任务栏时宠物会压在任务栏上沿约 24 逻辑单位（task01 实测）。
  task09 的控制栏预留会把它再抬起来，**到那时再决定要不要改**。
- **高 DPI 下两套坐标差一个 dpr**：Qt 给的窗口坐标是**逻辑**单位，Win32 API（`WindowFromPoint` 等）要的是**物理**像素。
- **无控制台打包后没有 stdout/stderr**，"双击没反应"的排查入口**只有日志文件**
  （本阶段不打包，但"所有输出都要落文件"这条从现在起就遵守）。

### 4.11 自检 / 验证自身的陷阱

- 断言把成功条件**写反**，详情文字说"失败"却显示 PASS → 用**中性文字**描述实测值。
- 用 `+` 拼详情时传入非字符串会抛 `TypeError` **打断整个自检** → 统一格式化。
- **别断言随环境变化的量**（屏幕分辨率、用户当前配置、真实启动文件夹内容），要断言**不变量**；标定基准用**常量**。
- **自检不许写用户真实目录**：测素材导入时必须把用户素材根重定向到临时目录。
- 依赖鼠标/时序的断言要先**移到新位置**，否则"是否移动"会正确地判定"没动"。
- 跑自检/脚本一律带 UTF-8 环境变量：`PYTHONUTF8=1` + `PYTHONIOENCODING=utf-8`（否则中文输出在 GBK 控制台 `UnicodeEncodeError`）。
- **退出码非 0 不等于有断言失败**：先看最后一行是"全过"还是"失败 n 项"。
- **认"源码里有没有某个名字"时必须先剥掉注释与字符串**（task06 踩过、task07 又踩一次）：
  文档里**解释"为什么不用 `QTimer`"**会被 `in` 判成"在用它"。对策见 `check_task07.py` 的 `_code_only()`。
- **往状态机喂假时间要"从小往大推进"，且不与真实时钟混喂**：第一个期限是"第一次被问到时刻 + T"
  钉下的，跳着喂永远不触发；"当前时刻"还有单调钳制，混喂会让两条时间线打架。
- **量 CPU 前先关掉还在播动画的窗口**：遮罩窗口每帧重绘是合成器开销（§4.9），开着它量轮询会读到
  21%~25%，而轮询本身 **0.00%**——差一个数量级，**别当成产品 bug 去改逻辑**。
- 结论性经验：**断言失败时先怀疑测试写错了**——旧项目多次出现"代码对、测试错"。

### 4.12 素材与构图的固有特性（不是 bug，别去"修"）

- `waving`（旧名 `jumping`）的人物被素材画成约 **74.5%** 大小，进出各有约 26% 的尺寸变化。
  **这是素材固有特性，不在运行时缩放对齐。**
- 量尺度**不能只用一个特征**（低头时帽檐转折会突变）。图集里名为 `running` 的动作实际是**坐姿摆手**，不是跑动。
- 休息动作（`rest`，旧名 `sit`）是**躺姿**，人物宽高比约 1.7；但**画布仍是 500×500**（"躺"的是人物，不是画布）。
- **自带素材是抽帧产物**：`raw/<命名空间>/*.gif` →（`tools/extract_gif_frames.py`）→
  `assets/<命名空间>/{asset.json, frames/<动作>_<序号>.png}`。动作名 = GIF 文件名；`raw/` 是源、`assets/` 是产物，
  **产物可随时用工具重跑覆盖**（人写的 `name` / `description` / `sounds` / 各动作已有的 `fps` 会被保留）。
- **画布统一 500×500**（所有动作、两个包都是），人物在画布里的位置/大小**由素材自己决定**，不做运行时对齐。
- **源 GIF 的透明是 1 位 alpha（只有 0 / 255）**，所以抽出的 PNG **边缘天生没有抗锯齿**。这不是素材坏了。
  task03 实测：`idle` / `rest` / `waving` 第一帧的 alpha 只有 `{0, 255}` 两档、**半透明像素 0 个**、洋红像素 0 个、
  最外一圈不透明像素 0 个 → **素材 alpha 是干净的**。验收时**颜色干净**（无洋红/底色残边）依旧必须成立，
  **阶梯状硬边是预期的**。
- **实测尺寸等于 `cell`**：抽帧输出的 PNG 是 500×500，`cell` 一致性检查报干净。
- **帧率**：GIF 帧间隔 30 / 40 ms 交替（2:1），平均 33.333 ms → **30 fps**，所以两个包每个动作都写了 `fps: 30.0`。
- `assets/weedy_sightseer` 的 `温蒂-倾听-基建-Special-x1` 是 **600 帧 / 20 秒**的完整演出
  （带道具、人物会走出画布），在索引里就是一个普通动作，**怎么播由后面的 task 决定**。
- 素材体积：`assets/` 约 **55 MB**（1683 个文件：1681 张帧 + 2 个 `asset.json`），`raw/` 约 **22 MB**。

### 4.13 路径与启动方式的边界（**不是 bug，别再改一遍**）

- **"程序目录"跟着入口脚本走，而 `python -m` 是特例**：`python run.py` 时 `sys.argv[0]` 的父目录是仓库根；
  但 `-m` 时 `sys.argv[0]` 是 `<...>/src/deskpet/__main__.py`，直接取父目录会得到**源码包**，整体错一级。
  判据见 `paths._module_entry_dir()`：**`__main__.__spec__.name` 以 `__main__` 结尾**
  **且** `__main__.__file__` 恰好是 `__main__.py`，然后从包目录一路向上直到不在包内。
  **三个反例（都要有断言守着）**：
  - `-m` 时 `spec.submodule_search_locations` 是**空的**（`__main__` 自己不是包）→ 用它当判据会漏掉最常见的写法；
  - `python 某个脚本.py` 时若脚本目录里有 `__init__.py`，Python **也会**给 `__main__` 塞一个带
    `submodule_search_locations` 的 spec → 用它当判据会**误判成 `-m`**；
  - 别读 `sys.orig_argv` 里的 `-m`：`-X`、`runpy` 都会改变它。
- **`python -m deskpet` 的"程序目录"就是 `src/`**（规则同上，task02 定下、task03 没改）。
  推论：`-m` 下 `resource_dir()` 也是 `src`，`<程序目录>/assets` = `src/assets` **不存在**，
  于是它会明确报"连默认形象都不可用"并 `rc=2`，`data/` 也会落在 `src/data`。
  **本阶段支持的入口是 `python run.py` 与 `python main.py`**。**不要**为了 `-m` 往路径规则里塞
  "往上找 `assets/`"这类项目专属启发式——那会让"程序目录"从可推理的定义退化成猜谜。
- **崩溃兜底那份路径规则会与 `paths.py` 漂移**：`logger._stdlib_program_dir()` 当初只写了"冻结 / 入口脚本"
  两个分支，`-m` 下它与 `paths` 推出的 `data/` 不一致。防线是模块级 `_assert_paths_consistent()`——
  **它会自己报出来，别把那条 stderr 当噪音**。
- **写日志的"分隔行"不能被格式器加工**：`log_startup_banner()` 必须**直接写文件 handler 的 `stream`**（再 `flush`）；
  走 `logger.info()` 会被加上"时间 级别 logger名"前缀。
- **`RotatingFileHandler` 会为了"记录比上限还大"而死循环**：单条记录超过 `maxBytes` 时会"刚滚完又立刻触发滚动"。
  对策是**主动截断单条记录**（`_TruncatingFormatter`，上限 4 KB），**并且**在 `emit()` 里自己接管滚动；
  另外 `logging.raiseExceptions = False` + 容错 `emit()`，保证日志层自己出错不炸主流程。
- **配置的原子写要连"序列化"一起兜住**：只 `except OSError` 会让意外异常甩给调用方（`set()` 的约定是**不抛**）→
  捕 `Exception`，把 `类型: 原因` 原样写进 `last_error()`。
- **自检自己也会踩沙箱**：DSH 的文件沙箱只保证**工作区**可写，在 `%TEMP%` 下新建目录会被拒绝（`PermissionError`）。
  自检的临时根目录要放在**工作区内**。
- **"程序目录 = 被启动的那个脚本所在目录"**：自检里要模拟一次真实启动，脚本必须放进它自己的 `bin/`，
  否则 `data/` 会落在自检根目录旁边，断言全错。
- **注入桩不能把被测的 `try/except` 一起换掉**（task02 排查了很久）：`ConfigStore.set()` 的约定是
  "`_write()` 失败 → 返回 False"，而 `_write()` **内部就吞异常**。所以"把 `_write` 换成会抛异常的桩"测的是**桩**。
  正确的两件事要分开测：① "失败被翻译成 False + 原因" → 桩**返回 False**；② "`_write()` 自己兜得住异常" →
  用**真实现** + 注入 `os.replace` 抛异常。
- **别断言"分隔行等于此刻的时间"**（子进程与父进程时钟不会完全一致）→ 断言**格式**（正则），不断言时刻。
- **`Get-Content` 看 UTF-8 日志/源码会显示成乱码**（PowerShell 按 ANSI 解码），那是**显示**问题，不是文件坏了：
  用 `Path.read_text(encoding="utf-8")` 或 `ascii()` 复核；**数行数也不要用 `Get-Content`**。

### 4.14 环境与工具类

- 用 PowerShell 的 `Set-Content` 会把 UTF-8 写成 ANSI，**写坏过中文源码** → 一律用编辑工具改文件。
- **不要给模块起会遮蔽标准库的名字**（例如 `logging.py`）——框架内部会 import 标准库。
- 提交信息写到**仓库外**的临时文件再引用（把带引号的文本直接塞进命令行会解析失败）。
- 排查问题时，往生产模块里加临时开关**最容易忘的是配套 import**（曾因漏 `import os` 直接 `NameError`）。
- **offscreen 平台插件的虚拟屏幕是 800×800**（task04 实测）：它**装不下第 10 级的 1000×1000**，
  于是"每一级都完整可见"在 offscreen 下**必然 FAIL**——那是测试平台的限制，不是产品 bug。
  对策：该条验收**只能在真平台跑**（`tools/check_task04.py --real`），
  锚点/夹取这类纯几何断言则在给定的屏幕矩形上验。**别为了让它变绿而改产品逻辑。**
- **自检脚本住在 `tools/` 时，"程序目录"就变成了 `tools/`**（§4.13：程序目录跟着入口脚本走），
  `assets/` 自然找不到、`data/` 也会落错地方。正确对策是
  `paths.override_dirs(program=ROOT, resource=ROOT)`——**它正是为此存在的**，不要去改路径规则本身。
- **逻辑高度 vs 物理高度**：`QScreen.geometry()` 给的是**逻辑**值（1080p @125% 报 864），
  而"1080p"说的是**物理**高度。两者混用会让自适应档位差好几级（task04 的标定基准就栽在这里）。
- **800×800 的虚拟屏幕也会咬到"拖动"这类断言**（task08 实测）：第 6 级是 500×500，
  在它上面**最大左上角就是 (300,300)**，于是"往右拖 120 px"会被正确地判定成"没动"。
  写期望值时要用**产品自己的夹取函数**按当前屏幕算一遍，别写死真机上的数字
  （`check_task08.py` 的 `_clamped()` 就是干这个的）。

### 4.15 没有 `QGuiApplication` 时 `QBitmap.fromImage()` 会**段错误**（task08 实测）

- `QBitmap.fromImage()` / `QRegion(QBitmap)` 在**没有 `QGuiApplication` 实例**时
  会**直接崩掉进程**（Windows 退出码 `0xC0000005`），**不是**抛异常——
  所以 `try/except` 拦不住，"降级：算不出来就整帧可点，永不抛异常"那条约定
  在无 app 的场景下变成了"直接消失"，**比抛异常更糟**。
- 实测：同一段代码在有 `QApplication` 时正常给出区域（40×40 图上
  不透明块 `x∈[5,14], y∈[10,29]` → `QRect(5, 10, 10, 20)`），
  没有时进程当场退出、**一行输出都留不下**（连重定向的文件都是空的）。
- 对策：`hit_test.can_build_region()` 先问一句"现在能算吗"，**不能就返回 `None`**
  （=`整帧可点`）。只有**导入期 / 自检**这类没有 app 的场景会走到。
- **判据**：自检里用**子进程**验（真崩了只带走子进程）；
  `python tools/check_task08.py` 的 §5 就是这一条。
  别在自检里直接调——那会把整个自检带走。

### 4.16 双击会先来一个 `mousePressEvent`，于是"拖动"被顺手开起来（task08 实测）

- Qt 的顺序是 **press → release → dblclick**。双击的第 2 次按下**先**触发
  `mousePressEvent`，于是"只有按在人物身上才起拖"的实现已经把窗口置成
  `dragging`、并**挂起了静置逻辑**；而这一次按下**没有**配对的 release
  走到"松手"（它的 release 在 dblclick 之后）。
- 表现：**双击之后宠物僵住**——`dragging` 恒为真 → 右键菜单永远不弹、
  静置逻辑永远被挂起（再也不会张望）。task07 的自检当场抓到
  （双击后 `suspended` 变成 `True` 且不再恢复），而不是猜出来的。
- 对策：`mouseDoubleClickEvent` 里在 `super()` **之后**调 `abort_drag()`
  ——**必须在 `super()` 之后**：QWidget 的默认实现会把这次双击再翻成一次
  `mousePressEvent`，放在前面中止的话紧随其后的 press 会立刻把拖动又开起来。
- "中止"与"松手"是两件事，**故意不复用** `_finish_drag()`：松手要写配置 +
  发 `drag_finished`（用户真的挪了位置），中止什么都不留（那两次点击没有位移）。
  但**动作控制权照常还回去并重新计时**，否则静置逻辑会一直停摆。
- 推论（写事件处理器时的通则）：**任何"按下才开始"的状态机，都要想清楚
  双击 / 三击那条路径**——它的第二次按下没有配对的松手。

### 4.17 "位置变了"的信号分不清"用户拖动"与"程序换档"（task09 实测）

- `PetWindow.set_size_level()` 按右下角锚点切档 → **左上角必然变** →
  `move_to()` 照样发 `position_changed`。而控制栏的增量跟随就挂在这个信号上，
  于是**点一次 `+`，控制栏就按那个增量斜着平移一次**。
- 表现：连点 `+` 时控制栏越跑越偏，用户得追着鼠标点——
  正好违反 task09 最关键的那条验收（"从 1 连点到 10 鼠标不用动"）。
  **真平台自检当场抓到**：栏从 `2306,1372` 一路漂到 `1974,891`。
- 对策：跟随逻辑里加**挂起标志**（`ControlBarController._stepping`），
  `_step()` 换档期间置真、`sync_follow()` 见真就返回；换完档**重设跟随基准**
  （`_synced_pet_pos`），否则下一次拖动第一下会跳。
- **`position_changed` 的语义其实是"位置变了"，不是"用户挪了"**。
  将来任何"只在用户拖动时才该发生"的行为，都要先想清楚这条——
  信号本身**分不出**是谁改的位置。
- 推论（写自检的通则）：**"某个坐标全程不变"这种断言必须用屏幕坐标
  （`mapToGlobal`）**。窗口内坐标不变只是布局稳定，两者不是一回事。

### 4.18 音效层的三个真陷阱（task10 实测）

- **`QMediaPlayer.setSource()` 是异步的，同步读 `mediaStatus()` 永远拿到 `NoMedia`。**
  第一版实现读它判"能不能播"，**把每一个坏文件都判成"能播"**；是自检里
  "往 `sounds/` 放一个打不开的文件"那条当场抓到的（日志里 Qt 自己打着
  `Could not open media`，而我们的代码说它没问题）。
- **`errorOccurred` 也不保证送达。** 实测：设备已被另一个播放器占用时，
  坏文件的报错**根本没发出来**（连 `error()` 都是 `NoError`）。
  所以判据要取**能问到的状态本身**（`error()` + `mediaStatus()`），
  并留一个 `settle()` 让调用方在预热后把结论定下来——**不要赌信号一定到**。
- **"改名的 txt"不一定被拒**：FFmpeg 的探测很宽容，某些内容会被当成裸流
  加载成 `LoadedMedia` 且 `NoError`。自检**造坏文件时要选实测会被拒的内容**
  （`b"not audio\n" * 40` / 随机字节 / 单字节可以；`b"definitely not audio\n"*40`
  和带 PNG 头的字节**不行**）。这不是产品判错，是那个文件真的被解码器接受了。
- **`sequence_done` 在"任何一次播完"时都会发**，不只是片段序列；而且信号发出时
  `Animation._segments` **已经被清空**（`_finish()` 先清再发）→ 在那个槽里问
  `animation.in_sequence` **永远是假**。第一版拿它当"手势播完"的判据，于是普通的
  `play_action("waving")` 播完也被切回 `idle`，把 task05 的
  "一次性动作停在末帧、定时器已停"整条约定打掉（**真平台自检抓到**：
  期望停在末帧 29，实际 41 且 `running=True`）。对策：自己记 `_gesture_active`。

### 4.19 音效格式：没有"多格式支持"这回事（task10 补充实测）

- **播放路径本来就不看扩展名**，所以 `.ogg` / `.m4a` / `.flac` 不需要"支持"——
  把 `"rest": "x.ogg"` 写进 `asset.json` 就能响。实测定案：任何后缀都会真的
  尝试播放，扩展名表（`KNOWN_AUDIO_SUFFIXES`）**只影响日志措辞**。
  自检有一条专门盯它："奇怪后缀仍被真的加载"。
- **"支持哪些格式"是运行时事实，不是常量**：`QMediaFormat` 问出来的
  容器/编码随 Qt 与 FFmpeg 的构建而变（本机报出 12 种容器、8 种音频编码，
  含 Ogg / MPEG4）。所以真值取**运行时查询**（`decoder_file_formats()`），
  **不要**在代码或文档里抄一张写死的表当判据。
- **本机没有任何音频编码器**：无 `ffmpeg` / `sox` / `soundfile` / `pydub`，
  `PySide6` 与 `C:\Windows\Media` 里也只有 `.wav`。`QMediaRecorder` 录音
  这条路在本环境下产出 0 字节（没有可用的采集源）。所以自检里
  **"真 ogg / m4a 文件"造不出来**——用"真 WAV 内容 + `.ogg` 后缀"验
  "后缀不挡路"，并在真平台另验"Qt 确实报出支持 Ogg"。
  将来若要验**真编码**，需要一个编码器（要用户同意才装，§3.2）。
- ⚠ **同一个扩展名能不能打开取决于内容**（§4.18 第 3 条），
  所以"支持某格式"只能用真文件实测，扩展名保证不了任何事。

### 4.20 Win32 全局热键的四个实测坑（task12）

- **`RegisterHotKey` 认线程**：它必须在**拥有那个窗口的线程**里调用，否则返回
  **`1408`（`无效窗口；它属于另一线程`）**——**不是** `1409`。这两个错误码只差一位，
  粗心看日志会得出"所有快捷键都被占用了"的错误结论（本项目差点照这个结论去
  写"回退链耗尽"的提示）。判据：同一 `hwnd`，主线程调 `False/1408`，
  子线程内调 `True/0`。对策：所有 Win32 调用都经 `_HotkeyThread.call()` 投回子线程。
- **`GetLastError` 成功时不写**：它只在**失败**时被设置，成功时保留**上一次**的值。
  所以"注册成功 + 紧接着读到 1408"这种自相矛盾的记录真会出现。对策：
  `win32.clear_last_error()` 在每次调用前清零，并且**判据永远是返回值**，
  错误码只在返回假时才有意义。
- **`WNDPROC` 的签名必须是指针宽度**：`LRESULT` / `WPARAM` / `LPARAM` 在 64 位下
  都是 8 字节。把 `lParam` 声明成 `int` 时 `DefWindowProcW` 会抛
  `ctypes.ArgumentError: argument 4: OverflowError: int too long to convert`，
  而**回调里的异常不会冒泡**——只在 stderr 打一行
  `Exception ignored while calling ctypes callback function`，极容易被当成噪音忽略。
  另外回调对象**必须模块级强引用**（`_WINDOW_PROC`）：没人引用它就是野指针。
- **`keybd_event` 在自动化会话里送不到热键**（**测试方法坑，不是产品问题**）：
  实测同一台机器上，`keybd_event(Ctrl,Alt,Shift,P)` 之后消息队列**什么都没有**
  （信号 0 次），而 `PostThreadMessageW(tid, WM_HOTKEY)` **立刻**触发 1 次。
  原因是 `keybd_event` 走**真实键盘输入栈**，要由输入系统在"键盘焦点"上下文中派发，
  无人值守 / 前台是别的进程时不保证成立。**对策**：自检把判据拆成两半各自用最硬的那种
  ——**消息接收链路**用投递验（系统在热键被按时做的就是往线程队列投 `WM_HOTKEY`），
  **注册与释放**用真 Win32 调用验；"操作系统愿不愿意派发"这一条只能**人工按键确认**。

**顺带核实的两条系统语义**（用来支撑"占用"与"强杀"这两条验收）：

- **占用是跨进程的**：父进程占住组合后，**另一个进程**注册同一组合会拿到 `1409`；
  同一个进程内换一个 `id` 注册同一组合也是 `1409`。所以"用别的程序占住"这个
  验收手段是成立的。
- **强制结束进程不会留下占着的热键**：`taskkill /F`（走 `TerminateProcess`，
  不执行我们的 `finally`、也就不会 `UnregisterHotKey`）之后**立刻**注册同一组合
  是**成功**的。也就是说"强杀 → 热键没释放"这条**在本机不成立**：热键的归属是
  **进程**，进程一消亡系统就回收它。真正需要"主动释放"的场景是**正常退出后
  立刻重启**（那时旧进程可能还在收尾），而正常退出本来就走 `finally`。

### 4.21 单实例与重启的四个实测坑（task13）

- **⚠ `QProcess.startDetached()` 返回的是 `(ok, pid)` 元组，不是 bool**
  （PySide6 6.12 实测；Qt6 的 C++ 签名带 `qint64 *pid` 出参，绑定层一并返回）。
  于是 `bool(QProcess.startDetached(...))` **永远为真**：`bool((False, -1)) == True`。
  后果正是 task13 §6 明写不许出现的那一幕——**"重启失败"被当成成功**，
  旧实例照常放锁、照常退出，而新实例压根没起来 → **桌宠彻底消失**。
  判据：`startDetached('Z:/nope/x.exe', ...)` → `(False, -1)`；取 `result[0]`。
  **是 task13 的真平台自检抓到的**（`check_task13_real.py §3`），不是猜的。
- **⚠ 放锁 = `ReleaseMutex` **+** `CloseHandle`，只做前者新实例进不来**。
  `CreateMutexW` 的 `ERROR_ALREADY_EXISTS` 判据是"**这个名字的内核对象还在不在**"，
  不是"有没有人占着它的所有权"。实测三态：旧进程什么都不做 → 新实例 `None`；
  只 `ReleaseMutex` → **仍然 `None`**；两者都做 → 拿到锁。
  对策收在 `SingleInstanceHandle.release()` 一处（幂等）。
- **`GetLastError` 在 `CreateMutexW` 上同样"成功不写"**：抢到锁时它保留上一次的值
  （可能是别的调用留下的 `183`）→ "第一个实例"会被误判成"已有实例"，
  **桌宠直接起不来**。对策：`win32.clear_last_error()` 在调用前清零，
  判据是"返回值非空 **且** 错误码 == 183"，两条缺一不可。
- **`Get-CimInstance Win32_Process` 在 DSH 文件沙箱下的 pwsh 里被拒绝访问**
  （`HRESULT 0x80041003`）。它在自检里是"按命令行找子进程并清掉"的手段，
  被拒绝之后异常又被 `except: pass` 咽掉 → **清理从来没发生**，
  而表现是"§3/§4 抢不到锁（有残留？）"——**看起来像产品 bug**。
  教训与 §4.3 同源：*失败路径自己也会失败，而且更难查*。
  对策：自检**别按进程名强杀**（会误伤用户自己开的 `pythonw`），
  用"记下自己拉的 pid"+"让子进程带 `--exit-after` 自己退"两条。

**顺带核实的两条系统事实**（支撑"作用域"与"完全脱离"这两条验收）：

- **不带 `Global\` 前缀的互斥体作用域就是当前登录会话**（`BaseNamedObjects`），
  所以另一个 Windows 用户可以各跑一个。
- **`startDetached` 起的子进程真的能脱离父进程**：实测旧实例退出之后，
  那个 `pythonw.exe` **仍在进程表里、仍占着锁、日志照常写**
  （`01:12:22` 抢到锁，之后一直活着）。所以"关闭父进程的窗口/控制台
  不会连带关掉新实例"这条**在本机成立**，且已被真进程验过。

### 4.22 开机自启的四个实测坑（task14）

- **⚠ 状态文案有"完整句子"与"后缀"两类，混用会渲染出 `已开启已开启［…］`**。
  task14 §4 的文案表里五条并列，但**拼法不同**：`TEXT_BROKEN` /
  `TEXT_ELSEWHERE` 自带"未开启"开头，而 `TEXT_WORKDIR` / `TEXT_FALLBACK`
  是接在 `TEXT_ENABLED` **后面**的后缀。第一版把 `TEXT_WORKDIR` 写成了
  完整句子又去 `f"{TEXT_ENABLED}{TEXT_WORKDIR}"`，被自检当场抓到。
  对策：名字里带 `_SUFFIX` 的才是后缀，另有一个拼好的完整常量供比对。
- **⚠ "成功之后再查一次、以查询为准"是个陷阱**。界面查的是**真实**启动文件夹，
  而干活的是**处理器**；两者只要不是同一处（自检注入的假文件夹、将来换实现），
  那次查询就会把刚做成的结果**覆盖成相反的值**——用户勾上了、界面却弹回未勾选。
  正确规矩：**`AutostartOutcome` 是这一次操作的唯一结论**
  （`enable()` 内部已经做过"写完再读回核验"，那份核验更贴近真相）；
  只有**失败**那一路才重新查询（回滚要的是"系统现在到底什么样"）。
- **`.ps1` 的 BOM 会被编辑工具悄悄吃掉**。含中文的 `.ps1` 缺 BOM 时
  PowerShell 5.1 按 ANSI 解码 → `ParserError`。自检里有一条
  `[Parser]::ParseFile` 的**只解析不执行**检查盯着它（§6 那条验收的自动化版）；
  改完脚本记得让它过。⚠ `[ref]$errors` 的变量**必须先声明**，否则报的是
  "变量不存在"，与"脚本语法错"分不出来（§4.3 同源）。
- **PowerShell 子进程的返回值形状**：`make_shortcut.ps1` 用
  `Write-Lines @("STATUS=OK", ...)`（逐行 `键=值`）与 `read_shortcut.ps1`
  **格式统一**；第一版前者写的是 `"OK\n详情"`，父进程按 `STATUS=` 解析
  → 永远读不到结论，于是"快捷方式建好了却报失败"。判据：两个脚本的结果
  文件格式必须能被**同一个** `_parse_key_values()` 吃下。

**顺带核实的两条系统事实**（支撑"自我定位"与"回退"这两条验收）：

- **`QProcess.startDetached()` 在不存在的程序上返回 `(False, -1)`**，
  对存在的程序（参数就算指向不存在的文件）返回 `(True, pid)`。
  所以 task14 用它验"快捷方式的目标真的能启动桌宠"是成立的。
- **DSH 文件沙箱会拒绝写工作区外的路径**：本机实测
  `%APPDATA%\...\Startup\` 下直接写文件报 `PermissionError [Errno 13]`。
  于是 `check_task14_real.py` 在默认沙箱下**只能验到"两条路都失败"那一段**
  （而这恰好是"安全软件拦住"那条验收的真实复现——产品表现完全正确：
  两条原因都报出来、界面回滚、不显示假成功）。要在真启动文件夹里建出
  `.lnk`，必须用 `sandbox_permissions=danger-full-access` 跑一次。

---

## 5. 当前进度

| task | 标题 | 状态 |
| --- | --- | --- |
| task01 | 跑起来：透明置顶的宠物窗口（占位素材） | ✅ 已完成 |
| task02 | 路径、配置与日志：能记住一句话，也能说清昨天发生了什么 | ✅ 已完成 |
| task03 | 素材加载与一次列全校验：画出真实的（温蒂）第一帧 | ✅ 已完成 |
| task04 | 尺寸档位与缩放：10 级都能变、贴边不跑出屏幕 | ✅ 已完成 |
| task05 | 动起来：按素材帧率播放，循环 / 往返 / 一次性 | ✅ 已完成 |
| task06 | 右键菜单：换动作、休息/站起来、镜像翻转（按状态记忆） | ✅ 已完成 |
| task07 | 久置行为：鼠标一停就张望，再等一会儿坐下 | ✅ 已完成 |
| task08 | 拖动与命中：只有按在人物身上才起拖，松手记住位置 | ✅ 已完成 |
| task09 | 尺寸控制栏：一条悬浮条，缩放时按钮不动 | ✅ 已完成 |
| task10 | 音效与双击手势：坐下有声音，双击会挥手 | ✅ 已完成 |
| task11 | 设置窗口：五行设置、改完立即生效、绝不显示假成功 | ✅ 已完成 |
| task12 | 全局热键：藏起来还能叫回来 | ✅ 已完成 |
| task13 | 单实例与重启自己：换完形象要能活着回来 | ✅ 已完成 |
| task14 | 开机自启：状态是真的、开关能关掉、启动文件夹里只有一个文件 | ✅ 已完成 |

**task01（已完成）** —— 透明置顶窗口可拖可退，`setMask` 真穿透已实测。
交付 `run.py` / `main.py` / `src/deskpet/{__init__,__main__,app,pet_window,placeholder}.py`。
实测（2560×1440 / 100%）：落点 `(2286, 1166)`、尺寸 `250×250`、距右/下边缘各 24 逻辑单位；
**`WindowFromPoint`：图形中心 → 本窗口，窗口四角 → 别的窗口** → 真穿透；125% 缩放下 dpr=1.25、落点仍 24。

**task02（已完成）** —— 路径 / 配置 / 日志三块地基；`data/` 已进 `.gitignore`。
实测（删掉整个 `data/` 后 `--exit-after 2.5`）：首次运行建出 `data/`，里面**恰好一个** `deskpet.log`
（**没有** `config.json`，因为只在 `set()` 真的改变值时才落盘）；坏 JSON / 顶层非对象 / 类型不符 / 未知键
→ 全部正常启动、逐项回退 + 警告，已知键一个不丢；写入失败的三种注入 → `set()` **不抛、返回 False**、
原因在 `last_error()`、**无残留临时文件**；滚动实测出现 `deskpet.log.1` 且只有 1 份、单条 200 KB 不抛；
**只读路径不写盘**（抛异常的桩一次都没被调用）；启动期致命 → `rc=1` + 系统原文。
验收：task02 §6 的 13 条逐条跑过（临时自检脚本 96 项断言，跑完即删——本阶段不建 `tests/`）。

**task03（已完成）** —— 素材加载层 + 一次列全校验 + 真实第一帧。
交付新增 `nsrules.py` / `pet_spec.py` / `pet_loader.py`；`app.py` 加 `--pet` / `--sprite` / `--action` /
`--list-pets` / `--list-actions`；`pet_window.py` 显示真实第一帧、尺寸取素材 `cell`；**删除** `placeholder.py`。
实测（`python run.py`）：窗口 **500×500**（= `cell`）、落点 `(2036, 916)`、`mask=on`；显示 `idle` 第 0 帧
（截图 `docs/task03_pet_weedy.png`）；**真透明 + 真穿透**（窗口内空白区颜色与窗口外桌面完全相同）；
**没有色键**（全仓库搜不到 `FF00FF` / `hitmask`）。**帧率来源：两个包全部 13 个动作都是 `asset.json`（30.0），
遗留兜底表与 `DEFAULT_FPS` 一次都没被触发。**
验收：task03 §6 的 15 条逐条跑过（三个临时自检脚本约 130 项断言，跑完即删），其中命名空间白名单合法/非法各 6+19 例；
帧率坏值（`0`/`-1`/`"快"`/`true`/`null`/NaN/±inf/`[]`）全部回退且不崩；"一次列全"（`cell` 删掉 + `idle.fps` 改坏 +
首帧名指向不存在的文件 + `rest.frames` 清空 + `sounds` 放数字）→ **一次列出 3 条问题**（坏 fps 与坏 sounds 只进警告）；
缺一张帧文件 → **只报 1 条**；带 BOM 的索引能解析；命令行坏包名 → `rc=2` 且不崩、错误信息列出已找到的包；
配置里写坏包名 → 启动不中断、回退 `weedy`、**`config.json` 哈希前后不变**（回写属 task11）。

**task04（已完成）** —— 尺寸档位与缩放。新增 `size.py` / `rendering.py`；`pet_window.py` 按档位定尺寸、
按右下角锚点切档；`app.py` 加 `--size-level` / `--list-sizes` 并在启动行打印**档位+来源+像素尺寸**；
`config.DEFAULT_SIZE_LEVEL` 改成**引用 `size.DEFAULT_LEVEL`**（§4.1 的真值一处）。

实测（本机 **2560×1440 / DPI 100%（逻辑高度 1440）**）：
- **自适应档位 = 第 4 级（333×333）**，占屏幕高 23.1%——**与 task 卡的"1080p + 125% → 第 6 级"不同**。
  卡里那条与 `SCREEN_TARGET_RATIO = 0.24` **在数学上不可能同时成立**（第 6 级是 500 px，
  要让它最接近 0.24，屏幕逻辑高度得在 2083 左右；1080 物理→第 3 级、1080/1.25=864 逻辑→第 2 级）。
  **已与用户确认：以 0.24 为准**，`CALIBRATION` 改为本机实测基准（2560×1440 @1.0 → 第 4 级）。
  换机器/换分辨率后自适应档位会不同，**属正常**。
- 10 级像素高度：125 / 166 / 250 / 333 / 375 / 500 / 625 / 666 / 750 / 1000（**两两不同、严格递增**）；
  倍数显示 `0.25`…`2.00`。
- **锚点漂移 = 0 的验证方法**：跑 `tools/check_task04.py --real`，它真的建 `PetWindow`、
  把 10 级逐级 `set_size_level()`，每级读回**权威位置 `base_position`**（不是回读窗口坐标），
  算 `左上角 + 档位像素尺寸` 作为右下角。10 级得到**同一个** `(2536, 1416)`，即漂移为 0；
  且每级左上角/右下角都落在 2560×1440 屏幕内（无半截出屏）。命令行 `--size-level 1/6/10` 实测同样吻合。
- `--size-level 0/11/abc` → `argparse` 报错 + **退出码 2**（不夹取、不崩溃）；
  配置里的越界值（`99`）则**夹取** + 警告（两条不同规则，别混）。
- 配置 `"size_level": 10` → 第 10 级、来源"沿用上次"；**写 `6`（= 默认值）→ 仍是"沿用上次"**；
  删掉该项 → 第 4 级、来源"按屏幕自适应"（`has()` 的用处全部实测到了）。
- 窗口尺寸 == 内容尺寸 == 帧图尺寸（无留白、无裁边）；`SmoothTransformation` 放大到 2× 后
  边缘仍干净、无洋红残边（`docs/task04_sizes.png`：第 1/4/10 级对照）。

**task04 定下的两件事**：
1. **D3 缩放算法 = `SmoothTransformation`**（理由见附录 D3；放大**只在换档/首次取帧时**做，绝不每帧做）；
2. **缓存上限策略 = LRU，`rendering.DEFAULT_CACHE_LIMIT = 64` 张**，键是 `(动作, 帧序号, 档位)`。
   选 LRU 而不是"按档位保留"是因为它天然留住"当前档 + 前一档"，又不会在 10 档 × 大帧数下失控。
   **task06 加镜像时必须把这个维度并进 `rendering.FrameRenderer.cache_key()`**（唯一拼键处），
   否则立刻复现"翻转后拿到未翻转的缓存图"。

验收：task04 §6 的 13 条逐条跑过（`tools/check_task04.py`，30 项断言 + 真平台 1 项，**全过**）。
自检脚本**留在仓库里**（`tools/check_task04.py`，`--real` 走真屏幕），因为它同时是 §4.11 的
"别断言随环境变化的量"的示例：offscreen 平台只给 800×800 虚拟屏幕，**装不下第 10 级（1000×1000）**，
所以"每级完整可见"必须在真平台验——这一条差点被当成产品 bug。

**task05（已完成）** —— 动画引擎：按素材帧率播放，循环 / 往返 / 一次性。
新增 `animation.py`（**纯逻辑**：`Animation` + `PLAY_MODES` 播放方式表 + `Segment` 片段序列）；
`pet_window.py` 加 `AnimationDriver`（单次 `QTimer` + 每帧重排）与 `play_action()` / `hold_frame()`；
`app.py` 的 `--action` 从"只显示第一帧"改成"**播放**"，启动行加 `mode/frames/frame_ms/fps_source`。

**调度的最终实现**：**单次 `QTimer`（`Qt.PreciseTimer`）+ 每帧重排**。
一帧的回调是"算 dt → `advance(dt)` → 显示 → `start(next_ms)`"；
`advance()` 返回 `None`（定格 / 一次性动作停在末帧 / 片段播完）就**不 `start()`**，
定时器自己停住——**"还在播吗"就是 `timer.isActive()`**，没有第二个布尔开关。
`dt` 用 `QElapsedTimer` 实测并夹到 `MAX_DT_MS = 100`（休眠唤醒不跳帧）；
间隔夹在 `[4, 250] ms`。

实测（本机 2560×1440）：
- **`idle` 的节奏（纯调度探针，不建窗口）：3 秒 89 帧、平均 33.64 ms/帧、最差 35 ms**
  ——与素材声明的 30 fps（33.33 ms）吻合，`frame_ms` 来源 `asset.json`。
- **⚠ 踩到并修掉一个真 bug：`QTimer` 默认的 `CoarseTimer` 会把 33 ms 上取整到 46.8 ms**
  （Windows 时钟节拍 15.6 ms），表现就是"素材 30 fps、实际只有 21 fps"，看起来像动画逻辑错。
  改用 **`Qt.PreciseTimer`** 后 33.6~33.8 ms。（已记进 §4.9。）
- **真窗口（可见、带遮罩、每帧重绘）实测 41.7 ms/帧（3 秒 74 帧）**——比纯调度慢，
  逐项 profile 证明 `renderer.frame()` 命中缓存 / `toImage()` / `setMask()` / `update()` /
  `region_for_image()` **全在 1 ms 以内**，差值是 Windows 合成器重绘遮罩窗口的开销。
  **验收节奏要看"纯调度"那个数**（已记进 §4.9，免得后人当产品 bug 去改逻辑）。
- 六个动作各跑 2 秒都不崩、都在动；`--action waving` **停在末帧 29 且 `running=False`**；
  `--pet weedy_sightseer --action 温蒂-倾听-基建-Special-x1` 正常起播（600 帧、按 `oneshot`）。
- `--action abc` → **rc=2 + stderr 列出可用动作**（`weedy` 6 个 / `weedy_sightseer` 7 个），
  不弹模态框（那会阻塞事件循环，§4.9）。
- **只改素材的 `fps` 就能改节奏**（走命令行实测，用用户包 `data/pets/` 验的，**没碰 `assets/`**）：
  24 → 41.6667 ms、60 → 16.6667 ms、`"快"` / `-1` / `true` / **删掉** → 全部回退遗留兜底 125 ms（8 fps）且不崩。
- CPU：3 秒播放实测 **4.2%~10.9%**（低个位数~十出头，非空转轮询量级）；
  10 秒长跑前半段 36.31 ms/帧、全程 36.59 ms/帧（**不漂移**）。

验收：task05 §6 的 13 条逐条跑过。两个脚本**留在仓库里**：
`tools/check_task05.py`（offscreen，**55 项断言全过**）+
`tools/check_task05_real.py`（真平台，**11 项全过**，量节奏/CPU/长跑/UI 响应）。
offscreen 那份用**自检根里的临时素材包**复现"改 fps"的验收，所以 `assets/` 一个字节都没动。

> **§A 自查**：本轮**没有**同一个问题试 3 次以上（`pingpong` 那几条 FAIL 第一轮就定位到
> "假动作名不在播放方式表里"这个测试自身的错，不是反复改产品代码）；
> **没有**改到卡范围之外（右键菜单 / 镜像 / 状态机 / 音效一律没碰）；
> 文档改动集中在 §2.13 新增小节 + §4.9 两条 + 本节，与代码改动（1 个新模块 + 2 个模块改）**同量级**；
> 临时探针（profile 脚本、fps 探针包 `data/pets/fpsprobe`）**已全部清掉**。

**task06（已完成）** —— 右键菜单（换动作 / 休息·站起来 / 镜像翻转）+ 镜像渲染与按状态记忆。
新增 `context_menu.py`（`MENU_ORDER` + `MenuState` + `build_menu()` + `action_id()`）与
`mirror.py`（`MirrorState`：`is_on` / `can_mirror` / `toggle` / `mirror_changed`）；
`rendering.py` 的缓存键**补上镜像维度**（4 维）并把翻转做在渲染层（`QImage.flipped(Qt.Horizontal)`）；
`pet_window.py` 加 `contextMenuEvent` / `toggle_mirror()` / `toggle_rest()` / `menu_state()`；
`app.py` 启动时建 `MirrorState` 并在启动行加打印 `mirror=on|off`。
`config.py` 的 `mirror` 字段校验（过滤 / 警告 / 回滚）**沿用 task02 已有的实现**，本 task 只**修了一个真 bug**（见下）。

实测（本机 2560×1440）：
- **`config.json` 里 `mirror` 的真实内容**：点过 `镜像翻转` 之后是
  `"mirror": {"idle": true}`（只记**翻过的那些状态**，没翻过的键不出现）。
  关掉程序再启动 → 启动行打印 **`mirror=on`**，翻转状态确实还在；
  **读操作不写盘**（启动前后 `config.json` 的 SHA 完全一致）。
- **镜像 + 缓存键的最终实现**：键是 `(动作, 帧序号, 档位, 镜像)`，唯一拼键处
  `FrameRenderer.cache_key()`；翻转在**缩放之前**做（`_render()` 里 `image.flipped(Qt.Horizontal)`）。
  像素级验证：与"直接翻转原图再缩放"这条**独立路径**结果逐位相同；
  不透明剪影完全镜像对应（**0 个像素不符**）；颜色差异只到 **±1**（`SmoothTransformation`
  的插值噪声，不是"变成另一张图"）。**连续翻转 10 次、跨帧跨档位交叉翻、LRU 淘汰后再取
  —— 一次都没有"翻转无效"**（这条专门盯缓存键漏镜像）。
- **拖动中右键的最终取舍**：**明确忽略拖动中的右键**（不弹菜单，也不打断拖动）。
  真平台实测：拖动中右键后位置与 `dragging` 状态都没变，松手后右键恢复正常。
- **写失败回滚勾选是怎么做的**：`MirrorState.toggle()` 只在 `ConfigStore.set()` **返回真**时
  才改自己的内存表并发 `mirror_changed`；写失败就**直接返回 `False`**，
  内存表根本没动 → 勾选态（`menu_state().mirror_on`）自然还是旧值，
  **没有信号回路**（不需要"屏蔽信号 / 守卫标志"那套，因为失败的路径上压根不发信号）。
  注入 `_write` 返回 `False` 实测：`toggle` 返回 `False`、勾选未勾上、画面未变、
  内存表与磁盘都是 `{}`、原因留在 `last_error("mirror")`。
- **⚠ 顺手修掉一个 task02 遗留的真 bug**：`ConfigStore.set()` 曾经"先改内存、再写盘"，
  写失败**不回滚内存**。后果是下一次 `set()` 拿已经改过的内存去比"值没变"，
  走**不写盘**的短路并返回 `True` —— 用户看到翻转成功了，磁盘上却一个字节没落。
  已改成写失败时 `self._values[key] = previous` 回滚（§2.14 末段）。
  **这是 task06 的写失败自检抓到的**，不是猜的。
- 不适用范围内的动作（`--action waving`）→ 菜单项**灰色不可点**，`toggle_mirror()` 被拒且
  **配置一个字节都不改**；手工把 `mirror` 写成 `{"running_left": true, "idle": "yes"}`
  或旧名 `{"sit": true}` → 启动不崩、**三项各自一条警告被丢弃**、`running_left` 不会被翻，
  **`sit` 没有做任何别名**。

验收：task06 §6 的 16 条逐条跑过。两个脚本**留在仓库里**：
`tools/check_task06.py`（offscreen，**126 项断言全过**）+ `tools/check_task06_real.py`
（真平台，**20 项全过**：真 `setMask`、翻转后遮罩跟着换面、菜单在鼠标处弹出、
拖动中右键、真事件循环下翻 10 次不卡）。改过 `config.py` 之后**回归跑了 task04/task05 的自检**
（30 项 / 55 项，**全过**），确认没碰坏旧行为。
offscreen 平台**不支持 `setMask`**（§4.9），所以"遮罩真的跟着换面"这条只能在真平台验——
真平台那份量的是"翻转前后遮罩外接矩形的宽高完全一致"（水平翻转不改轮廓尺寸）。

> **§A 自查**：① **没有**同一个问题试 3 次以上——两次 FAIL 都是**第一轮就定位到测试自己写错**
> （一次是把源码里**注释提到** `mirrored()` 当成"在调用它"；一次是把翻转序列的期望值写死了），
> 产品代码只按事实改了一次。② **没有**改到卡范围之外：尺寸栏 / 设置 / 隐藏 / 音效 / 状态机 /
> 拖动持久化一律没碰（拖动只验"右键不打断"，没改拖动本身）。③ 文档改动集中在 §2.14 新增小节 +
> §2.12/§1.2 三处状态更新 + 本节，与代码改动（2 个新模块 + 5 个模块改）**同量级**。
> ④ 临时探针（三个 `probe_mirror*.py`、`data/task06_failcheck*`）**已全部删掉**。
> ⑤ `config.py` 那处改动**超出了卡列出的修改文件清单**（卡里只写"`mirror` 字段的校验与过滤"），
> 但它是**卡里明写的验收标准**（"写失败 → 勾选立刻回滚、宠物状态与配置保持一致"）
> 在当前实现下**不可能满足**的直接原因，属"不做就过不了验收"，已在此如实记录。

**task07（已完成）** —— 久置行为（张望 / 休息）。**约定见 §2.15**；新增 `idle_state.py` + `cursor_watch.py`。
实测：真事件循环 T=3 → **3.26 s 张望、8.5 s 休息**；轮询 **157.4 ms**、CPU **0.00%**；退出无残留。
自检 `check_task07.py`（123 项）+ `check_task07_real.py`（16 项）**全过**，task04/05/06 **回归全过**。

> **§A 自查**：首轮 FAIL 多是**测试自身**错（扫描没剥注释、跳着喂假时间），产品只改一处；
> 没越界；探针已删；CPU 误读（21%）经 profile 确认是**测量方法**，未改逻辑。

**task08（已完成）** —— 拖动与命中。**约定见 §2.16**；新增 `hit_test.py` / `geometry.py` / `drag.py`；
`pet_window.py` 换成正式拖动（`drag_controller` + `position_changed` + `abort_drag()`）；
`app.py` 加 `--position` 与"命令行 > 上次保存的 > 缺省"的位置决定顺序，启动行加**位置来源**。

实测（本机 2560×1440 / DPI 100%）：

- **D2 的最终落地方式 = `setMask(QRegion)`**（没有自判定、没有混合）。
  区域生成的**唯一实现**搬到了 `hit_test.region_for_frame()`，`pet_window.region_for_image()`
  只剩转发（旧名留给 task01–07 的自检）。区域**只在换帧 / 换档 / 换镜像时重算**
  （`show_action_frame()` 里那一处 `apply_mask()`），同动作同档位内复用。
  实测重算**没有抖动**：120 次完整拖动（每次 10 个移动事件）期间心跳定时器照常跑，
  位置始终精确等于"基准 + 累计位移"。
- **透明区真的穿透（怎么验的）**：真平台用 Win32 `WindowFromPoint` 判据
  （§4.9：这是唯一可自动化的判据）。第 4 级（333×333）、人物遮罩外接矩形
  `122,109 104x151`：**人物中心 → 本窗口 8456878**；
  **窗口四角 → 全部是别的窗口 197472**（4/4 穿透）。
  **没有**垫记事本——`WindowFromPoint` 返回"另一个窗口"本身就证明点击不会落到宠物上，
  比"垫一个记事本再肉眼点"更硬且可自动化。
- **`setMask` 的边缘锯齿**：本 task 用 1 位 alpha 的真实素材观察，边缘确有台阶，
  但**与 task01 的占位矩形结论一致**（1 倍观察几乎看不出）。
  它是 §0 已定案的取舍，**不要求本 task 解决**（源 GIF 的 alpha 本来就是 1 位，§4.12）。
- **`position` 的真实格式与一次实测值**：`"x,y"`，实测 `"460,330"`（松手那一刻的落脚点）、
  拖出屏外后实测 `"2227,1107"`（= 屏幕右下角夹取后的值，屏幕 2560×1440、窗口 333×333）。
  启动行形如 `pos=(2227, 1107)(沿用上次)`。
- **拖动与静置逻辑的挂起接口最终叫**：`PetWindow.set_action_suspended(bool)`
  → `IdleStateMachine.set_suspended()`（task07 留的接口，本 task 只接线）。
  挂起期间计时归零但不切动作；**恢复时重新计时**（松手后再等满 `idle_seconds` 才张望）。
- **拖动中的动作控制权**：按下先切 `idle` → 有水平位移才切跑动 → 松手回 `idle`。
  **纯竖直移动沿用上次方向**（`direction_action()` 返回 `None` 表示"没依据"）。
- **用户拖动过之后缩放不弹回缺省落点**：`_user_positioned` 为真时切档只做右下角锚点保护。
  实测切档后右下角锚点 `(2560, 1440)` → `(2560, 1440)`（**漂移 0**），
  而缺省落点是 `(2036, 916)` —— 位置确实以用户为准。
- **⚠ 顺手修掉一个真 bug（双击把宠物卡住）**：Qt 的顺序是 press → release → dblclick，
  双击的第 2 次按下会先触发 `mousePressEvent`，于是"起拖"被顺手开起来且**没有配对的松手**，
  表现是双击后 `dragging` 恒为真（右键菜单永远不弹、静置逻辑永远被挂起）。
  **是 task07 的自检当场抓到的**（双击后 `suspended` 变 `True` 且不再恢复），
  不是猜的。对策见 §4.16。
- **⚠ 另一个真发现：无 `QGuiApplication` 时 `QBitmap.fromImage()` 直接段错误**
  （`0xC0000005`，`try/except` 拦不住）。对策是 `hit_test.can_build_region()`，见 §4.15。

验收：task08 §6 的 20 条逐条跑过。两个脚本**留在仓库里**：
`tools/check_task08.py`（offscreen，**128 项断言全过**）+
`tools/check_task08_real.py`（真平台，**30 项全过**：`WindowFromPoint` 真穿透、
快速来回拖不丢偏移、拖出屏外后写进配置的是夹取值、重启后落在原处、切档不弹回缺省落点）。
改过 `pet_window.py` / `app.py` 之后**回归跑了 task04–07 的全部自检与真平台检查**
（30 / 55 / 128 / 123 项，**全过**），并顺手把 task05 / 06 / 06-real 里
引用已作废私有字段（`_dragging` / `_press_global` / `_drag_base_pos`）的用例改成**真实鼠标事件**走产品路径。

> **§A 自查**：① **没有**同一个问题试 3 次以上——几轮 FAIL 都**第一轮就定位到测试自身写错**
> （offscreen 虚拟屏幕只有 800×800，期望值写死了真机数字；`_code_only()` 把字符串剥掉了
> 却拿它断言字符串字面量；喂假时钟把时间线污染）。产品代码按事实改了**两处**：
> ① `hit_test()` 里"缩放必须在镜像分支之外"（自检抓到 rendered_size 不生效）；
> ② 双击卡死（task07 自检抓到）。②**没有**改到卡范围之外：控制栏 / 设置 / 音效 / 手势 /
> 热键一律没碰，音效与双击挥手明确留给 task10。③ 文档改动集中在 §2.16 新增小节 +
> §1.2 三行状态 + §4.14/4.15/4.16 + 本节，与代码改动（3 个新模块 + 2 个模块改）**同量级**。
> ④ 临时探针（`probe_task08.py` / `probe_task08b.py` 与它们的输出文件）**已全部删掉**。

**task09（已完成）** —— 尺寸控制栏。**约定见 §2.17，踩坑见 §4.17**；
新增 `control_bar.py`（`ControlBar` + `BAR_HEIGHT`/`GAP`/`RESERVED`）；
`size.py` 加 `level_text()`（档位文字的唯一真值）；`pet_window.py` 加
`set_resize_handler()` / `set_reserved()` / `reserve_bottom()` / `_persist_level()`；
`app.py` 加 `ControlBarController`（组装跟随 / 预留 / 回位 / 幂等）。

实测（本机 **2560×1440 / DPI 100%**，自适应第 4 级）：
- **档位文字**：`1.00x（6/10）`（全角括号）。**栏宽恒为 293**（第 1 级与第 10 级
  完全一致，布局不被文字长度撑动）。
- **`−` / `×` 的字形正常**（U+2212 / U+00D7），真实素材下显示正常、**不是方框**，
  所以**没有换字符**，附录 B 与本节即为最终文字。
- **栏位置**：宠物右边 `2536` → 栏右边 `2536`（**右对齐**；水平居中会落在 `2171`）；
  栏顶 `1372` = 宠物底 `1364` + `GAP 8`；栏高 `40`。
- **⚠ 本 task 的关键验收（缩放中按钮不动）实测通过**：把档位从 1 连点到 10，
  `+` 按钮的**屏幕坐标恒为 `2474,1391`**、`−` 恒为 `2438,1391`、栏左上角恒为
  `2306,1372`（**10 级全程一个像素都没动**）；一路点回 1 级同样不动。
- **宠物底边固定**：放得下的 9 个档位里底边恒为 `1364`。
- **拖动跟随**：宠物动 `(24, 76)` → 栏动 `(24, 76)`；来回拖之后栏**精确回到原处**
  （`2973,2039` → `2973,2039`，不累积误差、不抖）。
- **关闭回位**：点 `×` → 栏隐藏、宠物从 `1031` 回到 `1083` = 缺省落点的纵坐标
  （屏幕底 `1440` − 窗口 `333` − 边距 `24`），边距归零。
- **宠物贴屏幕最下方** → 栏出现在**上方**（栏底 `1099` = 宠物顶 `1107` − `GAP 8`），
  仍右对齐、仍完整在屏幕内。
- **配置**：换档后 `size_level` 落盘（第 9 级实测），**重读后仍是该档**、
  启动来源显示"沿用上次"；**到边界再点不写盘**（`mtime` 前后完全相同）。

> **⚠ 顺手修掉一个真 bug（"换档"被当成"拖动"）**：真平台自检当场抓到——
> `set_size_level()` 会改宠物左上角，于是 `position_changed` 照样发出来，
> 控制栏的增量跟随就按那个增量斜着平移，连点 `+` 时栏从 `2306,1372`
> 一路漂到 `1974,891`。对策是 `_stepping` 挂起标志（§4.17）。
> **这条只有真平台自检能抓到**（offscreen 那份的期望值写宽了，第一轮没红）。

验收：task09 §6 的 18 条逐条跑过。两个脚本**留在仓库里**：
`tools/check_task09.py`（offscreen，**115 项断言全过**）+
`tools/check_task09_real.py`（真平台，**37 项全过**：真实屏幕上的右对齐、
"按钮屏幕坐标全程不变"、拖动增量跟随、关闭回位、贴底时放上方、配置落盘）。
改过 `pet_window.py` / `app.py` / `size.py` 之后**回归跑了 task04–08 的全部自检与
真平台检查**（30 / 55 / 128 / 123 / 128 项 + 真平台 1 / 11 / 23 / 16 / 37 项，**全过**）。

> **§A 自查**：① **没有**同一个问题试 3 次以上——首轮 11 项 FAIL 里 9 项是
> **测试自己的期望值写错**（把"底边上移 RESERVED"误当成"底边不动"；
> 用 `QImage ==` 冒充逐像素比较；`_code_only()` 把 `.close()` 拆成三个 token
> 却拿带点的字面量去判），产品只按事实改了**两处**：`_reapply_bottom_margin()`
> 的基准底边不幂等（自检抓到）+ `_stepping` 挂起（真平台抓到）。
> ② **没有**改到卡范围之外：设置窗口 / 音效 / 手势 / 热键 / 自启 / 隐藏一律没碰。
> ③ 文档改动集中在 §2.17 新增小节 + §4.17 新增小节 + §1.2 两行状态 + 本节，
> 与代码改动（1 个新模块 + 4 个模块改）**同量级**。
> ④ 临时探针（`data/t09probe` / `t09persist` / `t09e2e`）**已全部删掉**。
> ⑤ **规格缺口（已按用户决定处理）**：卡里要求先读 `agent.md` §9 / 附录 B / 附录 C，
> 但那三处在你删掉的 `迁移清单.md` 里，现仓库搜不到。卡里已给的真值
> （40 / 8 / 40+8+4 / 文字格式 / 右对齐）一律照做；**卡里没给的**两项按合理默认落地：
> **① 栏宽与内边距**——`QHBoxLayout` 自然尺寸 + 标签 `minWidth 96` + 按钮 `30`（实测栏宽 293）；
> **② "上移一个条带高度"取 `RESERVED`(52)** 而不是 `BAR_HEIGHT`(40)，
> 因为 52 才是"栏高 + 间距 + 余量"，保证栏放得下（用户已确认此处理方式）。

**task10（已完成）** —— 音效与双击手势。**约定见 §2.18，踩坑见 §4.18**；
新增 `sounds.py`；`app.py` 加异步预热 + 启动音 + 音量联动 + 退出关设备；
`pet_window.py` 加 `mouseDoubleClickEvent` 手势 / `wave_once()` / `_play_sound()`；
`idle_state`→`_on_stage_changed()` 接休息音效。

**D4 最终选择 = `QMediaPlayer` + `QAudioOutput`**（要能播 mp3；`QSoundEffect` 只支持 wav）。
**不需要额外依赖**：`PySide6.QtMultimedia` 实测可用（已随 PySide6 装上）。
**预热方式**：`preload()` 立即返回 → 结果走 `sound_ready` 信号 → `QTimer.singleShot(80)` 排启动音；
**另一个定时器（2 s）兜底超时**，超时按"没有音效"照常启动。全程**不 sleep、不忙等、不建音频线程**。

实测（本机 2560×1440 / DPI 100%）：
- **两个自带包都没有 `sounds` 声明** → 启动**静默**，日志 `音效：无音效` + 原因
  `素材索引里没有声明任何音效`。`--pet weedy --exit-after 3` 实测 rc=0、无任何声音。
- **四条汇报文案逐字符实测**（用自检造的临时包，`assets/` 一个字节没动）：
  `音效 2/2` / `无音效` / `无音效（未在索引里声明：bgm.mp3）` /
  `音效 0/2（缺 chime）（无法播放：rest（Could not open file））`。
- **启动音时机真平台实测 81.3 ms**（约定 80 ms）；音量运行时读得回、改完立刻生效
  （`set_volume(1000)→100`、`(-5)→0`、`"大声"→0`）。
- **双击手势**：`waving` 整段 30 帧恰好一圈 → `sequence_done` → 回 `idle`；
  **拖动途中双击 wave 调用 0 次**（正常时 1 次）；连点两次各播一次 `chime` 且回到片段起点。
- **长跑 50 次**休息 + 双击后设备仍可用（`available=True`、还能真播）；
  **两次真实启动都 rc=0**（无残留占用）。
- 三条诊断在真平台上都验过：文件不在 / 格式打不开（Qt 原文 `Could not open file`）/
  存在却没声明（`sounds/` 里两个文件都被列出）。

验收：task10 §6 的 19 条逐条跑过（其中"能听到声音"那组按卡里前置说明，
用**自检自己造的临时素材包**验，**没有改 `assets/`、没有要用户同意**）。
两个脚本**留在仓库里**：`tools/check_task10.py`（offscreen，**92 项全过**）+
`tools/check_task10_real.py`（真平台，**20 项全过**：真音频设备、启动音时机、
50 次长跑、打不开的文件、两次真实进程干净退出）。
改过 `pet_window.py` / `app.py` 之后**回归跑了 task04–09 的全部自检与真平台检查**。
⚠ 顺手更新了 `check_task07.py` 里**两条已被 task10 取代的期望值**
（它们原文自己写着"挥手属 task10"）：双击现在会挥手，所以断言改成
"不再保持休息姿势（进入挥手手势）"与"不改 manual"，手势本身归 task10 自检。

> **§A 自查**：① **没有**同一个问题试 3 次以上——4 个首轮 FAIL 一次定位到
> **测试自身**（`_code_only()` 把 `.close()` 拆成 token 后仍拿带点字面量去判；
> 拿 `_source_of()` 的原文（含 docstring）判"有没有猜文件名"；
> 自检造的"坏文件"内容其实是 FFmpeg 能接受的；preload 幂等期望写反）。
> 产品按事实改了**三处**，每一处都有现场判据：① 异步加载不能同步读状态（日志里
> Qt 自己报着 `Could not open media`）；② `availability` 与 `summary()` 分叉
> （`available=True` 却报 `音效 0/2`）；③ 手势收尾判据 `in_sequence` 恒为假
> （task05 真平台自检抓到"停在 41 而非 29"）。
> ② **没有**改到卡范围之外：设置窗口 / 热键 / 自启 / 单实例 / 混音 / 淡入淡出 /
> 自动发现一律没碰（卡里明写不做）。③ 文档改动集中在 §2.18 新增小节 + §4.18 新增小节
> + §1.2 一行 + 本节，与代码改动（1 个新模块 + 3 个模块改）**同量级**。
> ④ 临时探针（`data/t10probe`、`t10broken`、`t10cands`、`t10c2`、`t10breakchk`、
> `t10dbg`、`t10brokenprobe`、`data/pets/`、`t10real_out/err.txt`、`crash.log`）
> **已全部删掉**。⑤ **卡里"打不开的文件"那条只在真设备上成立**：
> 自检必须选**实测被 Qt 拒绝**的内容，否则"改名的 txt"会被 FFmpeg 当裸流接受
> ——已记进 §4.18，免得后人以为产品判错了。

**task10 补充：多格式支持（同一次会话追加，约定见 §2.18 末行 / 坑见 §4.19）** ——
结论是**播放路径本来就不看扩展名**，所以这项工作的实质是"把事实固定下来"，
而不是"加一条格式分支"：

- `KNOWN_AUDIO_SUFFIXES` 补齐到 **21 个**（含 `.ogg` / `.oga` / `.opus` / `.m4a` /
  `.flac` / `.aac` / `.alac` / `.wma` + `mka/mkv/webm/avi/mov/aiff/ac3` 等可能装音轨的容器），
  并**明确写死它不是判据**（只影响日志措辞）。
- 新增**运行时查询**：`decoder_file_formats()` / `decoder_audio_codecs()`，
  把"支持哪些格式"这条事实**问 Qt 要**而不是抄表；`SoundReport` 带上这两份，
  **启动日志会打出来**（实测：12 种容器、8 种音频编码，含 Ogg / MPEG4 / MP3 / FLAC / Wave）。
- 实测：声明 `.ogg` 的包启动后 `音效 2/2`（真加载、真播放），rc=0。
- 真平台自检补 5 条：Qt 报出支持 Ogg、`.ogg` 与 `.m4a` 声明都能加载并播放。
- **没能验的**：本机**没有任何音频编码器**（无 ffmpeg/sox/soundfile/pydub，
  `QMediaRecorder` 也无可用采集源、产出 0 字节），所以造不出**真 ogg/m4a 文件**；
  自检用"真 WAV 内容 + `.ogg` 后缀"验"后缀不挡路"。**真编码格式未实测**
  ——已如实记进 §4.19，要验需要一个编码器（需用户同意才装）。
- 自检：`tools/check_task10.py` **117 项**（+25）、`check_task10_real.py` **25 项**（+5），
  task04–09 全部回归通过。

> **§A 自查（补充）**：① 没有同一个问题试 3 次以上——两次"造不出真 ogg"
> 的尝试（`QMediaRecorder` 录音、找 PySide6/Windows 自带样本）各试一次就停，
> 直接改走"后缀不挡路 + 问 Qt 要事实"这条路，并把**没能验的部分如实写进 §4.19**
> 而不是含糊过去。② **没有**改到卡范围之外：随机播放 / 多音效 / 事件信号那几项
> **一个字都没动**（用户明确说"不做其余事情"）。③ 文档改动（§2.18 一行 +
> §4.19 新增小节 + 本节）与代码改动（`sounds.py` 一个常量 + 三个查询函数 +
> `app.py` 一行日志）**同量级**。④ 临时探针（`data/pets/fmtprobe`、
> `data/fmtprobe`、`t10r/t10re.txt`）**已全部删掉**。

**task11（已完成）** —— 设置窗口。**约定见 §2.19**；
新增 `settings.py`（`FIELDS` 字段描述表 + `SettingsWindow` + `pet_choices()`）与
`keycapture.py`（录入控件骨架）；`config.py` 加 `SetResult` / `apply()` / `require()`；
`context_menu.py` 的 `NOT_WIRED_YET` 去掉 `settings`；
`pet_window.py` 加 `set_settings_handler()`；`app.py` 加 `SettingsController`。

实测（本机 **2560×1440，屏幕逻辑高度 1392**（1440 − 48 任务栏）/ DPI 100%）：

- **窗口实际尺寸 560 × 516**，落点 `1000,206`（水平居中；窗口中心 y=464 = 1392 的 1/3）；
  **真平台实测通过**（`check_task11_real.py`）。
- **五行量程**：`音量` 滑块 **0–100**、单位**空**（数字裸显示 `70`）；
  `久置时间` 滑块 **5–300**、单位 ` 秒`（显示 `20 秒`）；
  `呼出快捷键` 与 `开机自启` 两行**灰色不可点**且标题带 `（暂未开放）`。
- **改完立即生效**：拖音量 → `config.json` 的 `volume` 同步更新（实测 70→35→45 都落盘）；
  拖久置时间 → 立即写盘（状态机每次判定现读，无需重启）。
  截图见 `docs/task11_settings.png`。
- **写失败回滚的最终做法**：`store.apply()` 返回 `SetResult`；失败 → 控件回滚到
  `store.get(key)`（**配置里的真值**，不是"改之前那个值"）+ 状态栏
  `设置失败：<系统原文>`。**注入 `os.replace` 抛 `PermissionError("拒绝访问")` 实测**：
  状态栏显示 `设置失败：写配置失败：…（PermissionError: 拒绝访问）`，滑块回到 45，
  磁盘仍是 45。
- **不触发写入回路的具体做法 = `SettingsWindow._applying` 守卫标志**：
  初值填充与回滚都把它置真，`_on_slider/_on_check/_on_combo/_on_keyseq` 见真即返回。
  自检用一个**计数 `_write` 桩**盯它：一次改动 `_write` 只被调 **1 次**（回路会无限增长）。
- **打开设置窗口只读不写**：实测构造窗口后 `config.json` **连文件都没被创建**
  （`ConfigStore` 只在 `set()` 真的改变值时才落盘）。
- **单例**：连点 5 次 `设置…` → 始终是**同一个实例**、`show()` 被调 **0 次**
  （走 `reload + center_on_screen + raise_`）；Esc / `关闭` 之后引用被释放，
  再打开是**新实例**且值来自配置。
- **角色候选**：自检造了一个好包 + 一个坏包（只写在 scratch 里，`assets/` 未动），
  两个都出现在候选里；坏包带 `　⚠ 不可用`、当前项带 `● ` 且排第一位。
  **选可用包** → 写 `pet` + 状态栏 `重启后生效`；**选坏包** → **不写配置**（见下）
  + 说出默认形象 + 带出那个包的问题 + 界面退回真实状态。
- **逃生门**：`打开启动文件夹` 在按钮行**最左**（实测次序 `打开启动文件夹、关闭`），
  点击打开的是 `shell:startup`（自检拦下 `QDesktopServices` 验的 URL）。
- `mirror` 不在界面上，但设置窗口写过盘之后它**仍在** `config.json` 里。

**角色切换的当前边界（task13 补齐）**：本 task 只做"写配置 + 提示重启后生效"
与"坏包不动配置 + 提示详情"；**询问是否现在重启**、**坏包时把配置改回默认形象**、
**写失败提示"保存失败，重启后会回到原来的角色"** 三条留给 task13。

**为什么坏包不改配置**：`config.pet` 的默认值是空串（"用户还没表态"），
所以"回到默认形象"本来就不需要写任何东西；而把一个**已知不可用**的命名空间写进去，
等于让下次启动必然走一遍"回退默认形象"的警告路径。task13 §6 要的是
"配置被改回默认形象"——空串正是这个意思。**task13 若想显式写 `weedy`，改一处即可。**

验收：task11 §6 的 **24 条逐条跑过**。两个脚本**留在仓库里**：
`tools/check_task11.py`（offscreen，**115 项断言全过**）+
`tools/check_task11_real.py`（真平台，**18 项全过**：真屏幕居中与 1/3、
模态、走真菜单路径打开、宠物仍置顶、真写失败回滚、事件循环正常返回）。
改过 `config.py` / `pet_window.py` / `context_menu.py` / `app.py` 之后
**回归跑了 task04–10 的全部 offscreen 自检**（30 / 55 / 128 / 123 / 128 / 115 / 117 项，**全过**），
并**实跑了一次 `python run.py --exit-after 3`**（rc=0，启动行与音效日志正常）。

> **§A 自查**：① **没有**同一个问题试 3 次以上——首轮 FAIL 全部**一次定位到测试自己写错**
> （期望里把 `key` 当成 `title`；提示文字抄漏一个「后」字；`layout().children()` 里
> 一个 widget 都没有（要用 `itemAt(i)`）；`button()` 只收 `StandardButton`；
> 拿 `_source_of` 判 "APPDATA" 结果命中了解释"为什么不用它"的**注释**——
> 这一条 §4.11 早就写过，我又踩了一次）。产品按事实改了**三处**，每处都有现场判据：
> ① `set()` 写失败时**按 key 复制一份原因**（原来只记在 `"*"` 上，
> `last_error("volume")` 会**回**到上一次那个键的陈旧原因，是探针实测出来的）；
> ② `set()` 的**未知键**也要能写进去再回滚（否则 `apply("nope")` 会假装成功）；
> ③ `keycapture` 的属性改名 `text` → `display_text`（与 Qt 撞名，
> 实测 `TypeError: 'str' object is not callable`）。
> ② **没有**改到卡范围之外：自启 / 热键注册 / 重启 / 单实例 / 隐藏呼出 / 主题
> 一律没碰（卡里明写不做），`呼出快捷键` 与 `开机自启` 两行**只建骨架并禁用**。
> ③ 文档改动集中在 §2.19 新增小节 + §1.2 两行 + §5 本节，与代码改动
> （2 个新模块 + 4 个模块改）**同量级**。
> ④ 临时探针（`data/t11_probe.json` / `t11w.json` / `t11v*.json` / `t11smoke*` /
> `t11shot.json` / `task11_scratch` / `task11_real_scratch`）**已全部删掉**。
> ⑤ **规格缺口（已按用户决定处理）**：卡里要求先读的「第二部分 §4 / 3.4 / 3.5 /
> 3.13 / 3.15 / 附录 B」都在你删掉的 `迁移清单.md` 里。**附录 B 已由用户当场贴出**，
> 相关文字（含设置窗口那一组）一律照它落地；**卡里没给的**两项按合理默认落地并记在此：
> **① `开机自启` 那一行的状态栏文字取 `未开启`**（task14 §4 的 `AutostartState`
> 是它的真值，本 task 只是占位，task14 接上时换成真实查询结果）；
> **② 状态栏的成功提示文案**（`已切换到「…」；重启后生效`）卡里没给，属自定。

**task12（已完成）** —— 全局热键：藏起来还能叫回来。**约定见 §2.20，踩坑见 §4.20**；
新增 `win32.py`（`ctypes` 唯一出口）、`hotkey_spec.py`（掩码 + 说法解析）、
`hotkey.py`（`HotkeyManager` + 子线程消息循环）；
`pet_window.py` 加 `hide_pet()` / `show_pet()` / `visibility_changed` / `set_hotkey_available()`；
`context_menu.py` 的 `NOT_WIRED_YET` 清空、加 `REASON_NO_HOTKEY`；
`settings.py` 解锁 `呼出快捷键` 行（加 `HotkeyOutcome` / `set_hotkey_changer()`）；
`keycapture.py` 主键名改问 `hotkey_spec.format_qt_key()`（不再用 `QKeySequence`）、加 `reject_capture()`；
`app.py` 加 `HotkeyController`（注册 / 切换 / 换热键 / 释放）+ `logger.show_message()`。

**D5 的最终选择 = 子线程 + `GetMessageW` + `queue` + 100 ms 取件**（`hotkey_filter.py`
**没有创建**）。**120 ms 轮询已去掉**：GUI 线程那个定时器是**从内存队列取件**
（空队列时微秒级），不是轮询系统状态。**"分两拍处理"**的实现方式 =
`_on_hotkey_pressed` 里 `QTimer.singleShot(0, self.toggle)`。

实测（本机 2560×1440 / DPI 100%）：

- **本机 `Ctrl+Alt+Shift+P` 直接可用**（没走回退链，`changed=False`）；启动日志
  `呼出快捷键：Ctrl+Alt+Shift+P`。
- **回退链 + 写回配置（真进程实测）**：先用一个占位进程占住 `Ctrl+Alt+Shift+P`
  再启动桌宠 → 日志 `原快捷键 Ctrl+Alt+Shift+P 不可用（…错误 1409），已改用 Ctrl+Alt+Shift+O`，
  `config.json` 里 `hotkey` 真的被改写成 **`Ctrl+Alt+Shift+O`**。
- **候选链全占住 → `combo=None`**：日志 `所有候选快捷键都注册不上` +
  `没有可用的呼出快捷键（…）：隐藏功能已禁用`；`MenuState.visibility_available` 为假
  →菜单 `隐藏宠物` **灰掉**（不是隐藏），`hide_pet()` 也**拒绝隐藏**（返回 `False`）。
- **隐藏 / 呼出（真窗口）**：投一条真 `WM_HOTKEY` → 宠物隐藏（`hidden=True`、
  `visible=False`、动画定时器**已停**）；再投一条 → 呼出（`visible=True`、
  **仍带 `WindowStaysOnTopHint`**、动画定时器**恢复**）。
  隐藏→呼出 **20 次**：始终是**同一个窗口对象**、帧总数不变、档位与位置全程未变，
  40 次切换共 **1380 ms（平均 34.5 ms/次）**——证明是"保留状态"而不是重建窗口。
- **换热键失败会回滚**：先用另一个 `HotkeyManager` 真占住 `Ctrl+Shift+F9`，
  再 `change()` 过去 → `ok=False` + 占用文案；断言 **`manager.combo` 仍是原来那个**
  `Ctrl+Alt+Shift+P`，并且**再投一条 `WM_HOTKEY` 它真的还能触发**（不只是"字段没变"）。
- **退出时彻底释放**：`stop()` 之后 `combo=None`；**立刻**新建 manager 注册同一组合
  **成功**。
- ⚠ **踩到并修掉一个真 bug：`RegisterHotKey` 认线程**（`1408` 而不是 `1409`）。
  第一版从 GUI 线程调用子线程建出来的窗口 → 注册全失败。三个探针取到了干净判据
  （主线程调 `False/1408`、**子线程内**调 `True/0`），对策是把所有 Win32 调用
  经 `_HotkeyThread.call()` 投回子线程。**已记进 §4.20。**
- ⚠ **另一处必须记下来的：`WNDPROC` 签名 + 回调强引用**。把 `lParam` 当 `int`
  会让 `DefWindowProcW` 抛 `OverflowError`，而**回调里的异常不冒泡**（只在 stderr
  打一行 `Exception ignored while calling ctypes callback function`）。
- **强杀路径实测（系统语义，已记 §4.20）**：`taskkill /F` 之后**立刻**注册同一组合
  **成功**——热键归属**进程**，进程消亡系统就回收。所以"强杀后热键没释放"这条
  **在本机不成立**，不需要为它做额外处理。
- **CPU**：可见播放时实测 **0.00%**（4 秒采样）。隐藏期间的判据是**动画定时器已停**
  + 取件定时器只有 100 ms 一次的空队列检查（微秒级）——**没有 120 ms 轮询在空转**。

验收：task12 §6 的 **15 条逐条跑过**（15 条里有 3 条只能人工确认，见下）。
两个脚本**留在仓库里**：`tools/check_task12.py`（offscreen，**110 项断言全过**）+
`tools/check_task12_real.py`（真平台，**27 项全过**）。改过 `pet_window.py` /
`settings.py` / `context_menu.py` / `keycapture.py` / `app.py` / `config.py` / `logger.py`
之后**回归跑了 task04–11 的全部 offscreen 自检**（30 / 55 / 128 / 123 / 128 / 115 / 117 / 115 项，**全过**）
与全部真平台检查（task05/06/07/08/09/10/11，**全过**）。

**⚠ 没能自动化的 3 条（必须人工按键确认）**：① 真按 `Ctrl+Alt+Shift+P` 呼出
（`keybd_event` 在自动化会话里送不到热键，见 §4.20；消息链路本身已被投递法覆盖）；
② "切到浏览器它还在最上层"（要看观感）；③ 中文键盘布局下 AltGr 不会多报一个 Alt
（卡里也写了"若发现异常，记录到 agent.md"——本机**未发现异常**，录入只读 `event.key()`）。

> **§A 自查**：① **没有**同一个问题试 3 次以上——`1408` 那次用了**三个递进的只读探针**
> （建窗口有效性 → 线程归属 → 干净对照）就定位到根因，`src/` 只按事实改了一处
> （`_HotkeyThread.call()`）；首轮 FAIL 里绝大多数**一次就判定是测试自己写错**
> （手写 `0x4|0x1` 当 Ctrl+Alt、`_code_only()` 用空格拼 token 却拿带点字面量去判、
> 拿未来才录到的值断言现在、把 `FIELDS.title` 当成渲染后的标题、
> 搜索 `"modifiers"` 命中了我们自己的 `modifiers_text()`、`_FakeWin32` 漏替常量与方法）。
> ② **没有**改到卡范围之外：单实例 / 重启 / 自启 / 托盘 / 多热键 / 宏一律没碰；
> `hidden→呼出` 只做了"保留状态"，没有做销毁重建。
> ③ 文档改动集中在 §2.20 新增小节 + §4.20 新增小节 + §1.2 三行 + D5 + 本节，
> 与代码改动（3 个新模块 + 7 个模块改）**同量级**。
> ④ 临时探针（`tools/probe_task12*.py`、`tools/probe_t12_*.py`、
> `data/probe_t12*.txt`、`data/t12acc*`、`data/t12*_out.txt`、`data/t12cpu*`、`data/t12x*`）
> **已全部删掉**（`Get-ChildItem data` 现在只剩日志与各 task 的 `*_check.txt` / `*_real.txt`）。
> ⑤ **规格缺口（按顺序规则处理，需你确认）**：卡里把候选链写成 `Win+Alt+P`，
> 又规定展示顺序固定 `Ctrl → Alt → Shift → Win` —— 两者对同一个组合给出两种写法。
> 本单位**以顺序规则为准**（`Win+Alt+P` 的规范写法 = `Alt+Win+P`），因为它才是
> 用户可见、也是写进配置与录入控件的那一个；两者解析到**同一掩码 `0x9`**，
> 所以注册行为完全一致。自检里有一条专门盯它。
> ⑥ 顺带按事实更新了 `check_task11.py` 的 **3 条期望值**（它们原文写着"呼出快捷键未实现"，
> 而那正是 task11 自己划的、被 task12 推掉的边界；与 task10 更新 task07 那两条同理）。

**下一步**：`tasks/task14.md`（开机自启）——**已完成，见本节末**。

**task13（已完成）** —— 单实例与重启自己。**约定见 §2.21，踩坑见 §4.21**；
新增 `single_instance.py`（命名互斥体）、`restart.py`（拉起 + 交接 + 失败不退出）；
`win32.py` 加 `CreateMutexW` / `ReleaseMutex` / `CloseHandle` 薄绑定；
`app.py` 加 `--restart-delay` 解析、`_wait_before_lock()`、单实例编排、
`RestartController`、`_persist_pet_fallback()`；`settings.py` 补换角色流程
（询问重启 / 坏包回写默认形象 / 写失败提示）；`logger.py` 加 `log_restart_stage()`。

**D6 的最终选择 = 命名互斥体**（`CreateMutexW`）。`QLocalServer` 没采用：
它要在临时目录放套接字文件、且必须在 `QApplication` 之后才能用，
会把单实例检查挤到启动编排后面。**作用域"仅当前登录会话"怎么保证的**：
互斥体名字**不以 `Global\` 开头** → 内核把它放进当前会话的 `BaseNamedObjects`；
名字是 `deskpet-single-instance.<用户名>`（`MUTEX_PREFIX` 一处）。

**重启交接的完整时序**（`check_task13_real.py` 实测，日志可逐行对上）：

```
旧实例 01:12:21  抢到锁 → restart.build_args() = [pythonw.exe, run.py, --restart-delay, 1.2]
旧实例 01:12:21  startDetached 返回 (True, pid) → 取 result[0] 判成功
旧实例 01:12:21  release()：释放所有权 + 关闭句柄 → "重启时序[放锁]"
旧实例 01:12:21  旧实例进程退出（rc=0）
新实例 01:12:21  起、"先等 1.200 秒再抢单实例锁" → "重启时序[等待]"
新实例 01:12:22  等待结束（实际 1.201 秒）→ 抢锁**成功** → "重启时序[抢锁]"
```

**"重启失败不退出"是怎么验证的**：注入一个不存在的程序路径
（`Z:/definitely/not/here/deskpet.exe`）→ `_start_detached()` 返回 `False`
（从元组里取第一个元素）→ `restart_self()` **不放锁**、返回假 →
`RestartController` 弹 `重启失败，请手动关掉再启动一次`、**不调 `app.quit()`**。
真平台实测：`OLD_STARTED False` + `OLD_HELD_AFTER True`（旧实例仍持锁 = 它没有退出）。

实测（本机 2560×1440 / DPI 100%）：

- **单实例**：第一个跑着时第二次启动 → **rc=0 安静退出**，日志
  `检测到已有实例在运行（deskpet-single-instance.hanso）：本次启动安静退出`，
  **没有弹窗**；关掉之后再启动正常（锁真的被释放了）。
- **三个只读查询都不被拒**：已有实例时 `--list-pets`（169 字符）/
  `--list-actions`（685 字符）/ `--list-sizes`（585 字符）**都 rc=0 正常输出**。
  ⚠ `--list-sizes` 第一版是"抢了再放"，实测第二个实例**先被 `acquire()` 判成
  "已有实例"直接返回**，那个 `release()` 永远走不到 → 改成**压根不抢锁**。
- **完全脱离**：旧实例（Harness）退出之后，被拉起的 `pythonw.exe`
  **仍在进程表里、仍占着锁、日志照常写** → "关闭父进程不会连带关掉新实例"成立。
- **工作目录**：新实例日志里 `重启：新实例先等 … （工作目录 C:\projects\deskPet）`
  = 程序目录（**不是**继承父进程的 cwd）。
- **连续 3 轮 抢锁/放锁**：全程 3/3 持锁期间报"有实例"、3/3 放锁后报"空闲"，
  **没有任何一次两个实例并存，也没有空窗**。
- **⚠ 抓到一个会让桌宠彻底消失的真 bug**：`QProcess.startDetached()` 在
  PySide6 6.x 返回 **`(ok, pid)` 元组**，`bool(整个元组)` **永远为真** →
  "重启失败"被当成成功 → 旧实例放锁退出、新实例没起来。已改成取 `result[0]`
  （`restart._start_detached()`），并记进 §4.21。
- **⚠ 另一条：放锁必须连句柄一起关**。实测"只 `ReleaseMutex`"时
  `ERROR_ALREADY_EXISTS` 照样为真、新实例进不来（判据是"内核对象还在不在"）。
  已收在 `SingleInstanceHandle.release()` 一处并写进 §4.21。

验收：task13 §6 的 **17 条逐条跑过**。两个脚本**留在仓库里**：
`tools/check_task13.py`（offscreen，**83 项断言全过**）+
`tools/check_task13_real.py`（真平台，**31 项全过**：真起两个进程、真重启一次、
真看锁交接、真注入失败、真验脱离与工作目录）。
改过 `app.py` / `settings.py` / `config.py` / `logger.py` / `win32.py` 之后
**回归跑了 task04–12 的全部 offscreen 自检**（30 / 55 / 128 / 123 / 128 / 115 / 117 / 115 / 110，**全过**）
与全部真平台检查（task05–12，**全过**）。
另按事实更新了 `check_task11.py` 的 **2 条期望值**（它们原文写着"选坏包不写配置"，
而那正是 task11 自己划的、被 task13 推掉的边界；与 task10 更新 task07、
task12 更新 task11 同理）。

> **§A 自查**：① **没有**同一个问题试 3 次以上——`startDetached` 元组那个 bug
> 是**真平台自检一次抓到**的（`OLD_STARTED True` 配着不存在的路径，一眼可疑），
> 随后用一个 6 行的只读探针确认了 `bool((False, -1)) == True` 就定案；
> 首轮 FAIL 里绝大多数**一次就判定是测试自己写错**（`_code_only()` 把带点字面量
> 拆成 `args . list_pets`；`_squashed()` 会**丢掉字符串字面量**所以判不了
> `apply("pet", "")`；`paths.override_dirs` 是上下文管理器、忘了 `with`；
> `_is_running()` 用了自检专用后缀去问生产锁；`Get-CimInstance` 被沙箱拒绝
> 而异常被 `except: pass` 咽掉）。
> ② **没有**改到卡范围之外：自启 / 托盘 / 多实例协调 / 进程间通信 / 热替换角色
> / 自动更新一律没碰（卡里明写不做）。
> ③ 文档改动集中在 §2.21 新增小节 + §4.21 新增小节 + §1.2 两行 + 本节 + D6，
> 与代码改动（2 个新模块 + 5 个模块改）**同量级**。
> ④ 临时探针（`data/t13_gc/`、`data/task13_scratch/`、`data/task13_real_scratch/`、
> `data/t13*.txt`）**已全部删掉**。
> ⑤ **规格缺口**：卡里要求先读的「第三部分 3.16 / 附录 A / 附录 D 的 D6」
> 都在你删掉的 `迁移清单.md` 里，**本机已无该文件**。处理方式：
> **D6 当场问了你**（选命名互斥体）；`--restart-delay` 的默认 1.2 秒、
> "新进程先等再抢锁""先拉起新实例再关自己""子进程完全脱离""工作目录 = 程序目录"
> 这些**卡里 §2/§4/§6 本身写全了**，一律照它落地；
> **§3.16 那两个"必须处理的细节"**卡里没给原文，按卡里 §4 的"换角色流程"表格
> 落地为 **①询问重启 ②坏包把配置改回默认形象**（这也是 §6 验收明写的两条）。
> ⑥ 已知未做（**不阻塞**，卡里也没要求）：卡里 §6 最后一条"连续重启 5 次"，
> 本实现验的是 **3 轮锁的交接**（真起进程的那个 §2 只重启 1 次）——
> 因为每多一轮就多一个"不会自己退出"的真桌宠要收拾。

**task14（已完成）** —— 开机自启：状态是真的、开关能关掉、启动文件夹里只有一个文件。
**约定见 §2.22，踩坑见 §4.22**；
新增 `autostart.py`（`query_state` / `enable` / `disable` / `startup_dir` /
`cleanup_legacy_markers`）+ 两个外部脚本
`tools/make_shortcut.ps1` / `tools/read_shortcut.ps1`（**UTF-8 with BOM**）；
`paths.autostart_target()`（按当前进程反推目标，**绝不写死**）；
`logger.log_autostart_outcome()`（前缀 `自启[动作]`，两条尝试的原因都落日志）；
`settings.py` 解锁 `开机自启` 行（`implemented=True`、`AUTOSTART_KEY` 标记键、
`set_autostart_handler()`、`AutostartOutcome`、失败回滚到真实状态）；
`app.py` 加 `AutostartController`（`apply()` + `open_hook()`）并在
`SettingsController.open()` 里**每个新窗口都重装处理器**。

实测（本机 Windows 11 / 2560×1440）：

- **启动文件夹的真值**（向系统问来的，与 `%APPDATA%` 推导完全一致）：
  `C:\Users\hanso\AppData\Roaming\Microsoft\Windows\Start Menu\Programs\Startup`。
- **`.lnk` 的实际目标**（源码运行）：`C:\Python314\pythonw.exe` +
  参数 `C:\projects\deskPet\run.py`、工作目录 `C:\projects\deskPet`
  （= `paths.autostart_target()`，**字面量与实测完全吻合**）。
- **真平台实测通过 35 项**：真建出 `deskpet.lnk` → 读回目标/参数/工作目录全对 →
  **用那个目标真的拉了一次桌宠子进程、它活过了启动阶段**
  （这是"快捷方式真能启动程序"最硬的判据）→ 真关掉 → 文件消失 →
  重复关掉幂等成功 → 再开启仍然成功 → 收尾把启动文件夹恢复原样。
- **"恰好只有一个我们的文件"**：真启动文件夹实测 `['Ollama.lnk', 'deskpet.lnk', 'desktop.ini']`
  ——只有**一个**是我们的（`deskpet.cmd` 不并存）；从 `.cmd` 回退切回 `.lnk` 时
  回退项被清掉；残留 `.cmd` + 开启 → 只剩 `.lnk`。
- **"读状态是纯只读"是怎么被验证的**（§4.2 指定的方式）：把 `enable` / `disable` /
  `cleanup_legacy_markers` / `_write_shortcut` / `_write_fallback` / `_remove` /
  `_write_text` **全部换成会抛 `PermissionError` 的桩**，然后
  ① `query_state()` 连查 3 次；② 真建 `SettingsWindow` 并 `reload_from_config()` +
  `refresh_status()`。两次都是 **桩调用 0 次、快捷方式 SHA 不变、
  连 `config.json` 都没被创建**。另有一条源码级检查：用 `ast` 切出
  `query_state()` 的函数体，里面**没有任何写调用**。
- **"回退方式"真的被触发过吗**：**触发过，且文案准确**。两种触发途径都实测到：
  ① 把 `make_shortcut_script()` 指向不存在的路径 → 状态栏/返回状态是
  `已开启［回退方式：登录时会闪一下命令行窗口］`（`error` 仍是 `None`，
  因为**这是成功**），启动文件夹里恰好只有 `deskpet.cmd`，内容里目标与参数都对；
  ② **真实环境**：本会话的 DSH 沙箱拒绝写工作区外的路径，于是在真启动文件夹上
  两条路**都**失败，`error` 里同时能看到
  `生成快捷方式：Unable to save shortcut "…deskpet.lnk".` 与
  `写回退脚本失败（…deskpet.cmd）：[Errno 13] Permission denied`——
  **正是 task14 §6"注入两条都失败"那条验收的自然复现**，产品表现正确
  （两条原因都带出来 + 勾选回滚 + 状态栏 `设置失败：…` + 不显示假成功）。
  随后用 `danger-full-access` 跑一次真平台自检，`.lnk` 那条路**全部通过**。
- **状态文案逐字符核对**：`未开启` / `已开启` /
  `未开启［启动文件夹里那个快捷方式是坏的（读不出目标），取消勾选再勾选一次即可重建］` /
  `未开启［它指向的是别的地方：…］` / `已开启［工作目录对不上，重新开启即可修正］` /
  `［回退方式：登录时会闪一下命令行窗口］` / 前缀 `开机自启：`——**七条各有一条断言**。
  其中"指向别处"与"坏壳"两条是**真造出来的**：用产品自己的脚本把 `.lnk` 改成
  指向 `notepad.exe`、以及把 `.lnk` 清成 0 字节。
- **遗留标记清理**：手工放 `deskpet.marker` → 程序**主动删掉**、自启状态不变；
  清理**不碰**用户自己的启动项（`用户的启动项.lnk` 全程原封不动）——
  清单式删除，**不做通配**。
- **幂等**：重复 `disable()` 返回 `error=None` + `未开启`；清理在没有遗留文件时
  是空操作。
- **退出/启动干净**：`python run.py --exit-after 3` → rc=0，启动行与音效日志正常。

验收：task14 §6 的 **18 条逐条跑过**（其中 2 条只能人工确认，见下）。
两个脚本**留在仓库里**：`tools/check_task14.py`（offscreen，**111 项断言全过**，
全程在自检自己造的假启动文件夹里跑，**不碰用户的真实启动文件夹**）+
`tools/check_task14_real.py`（真平台，**35 项全过**，真建真读真启动、
跑完把启动文件夹恢复原样）。改过 `app.py` / `settings.py` / `paths.py` /
`logger.py` 之后**回归跑了 task04–13 的全部 offscreen 自检**
（30 / 55 / 128 / 123 / 128 / 115 / 117 / 116 / 110 / 83 项，**全过**）
与全部真平台检查（task05 / 11 / 12 / 13 real，**全过**）。
另按事实更新了 `check_task11.py` 的 **4 条**与 `check_task12.py` 的 **2 条**期望值
（它们原文写着"`开机自启` 未实现 / 带 `（暂未开放）`"，而那正是被 task14 推掉的边界；
与 task10 更新 task07、task12 更新 task11、task13 更新 task11 同理）。

**⚠ 没能自动化的 2 条（必须人工确认）**：① **注销并重新登录（或重启）后桌宠自动起来**
——这一次登录没法在脚本里重来一遍；可自动化的证据是"`.lnk` 在真启动文件夹里、
目标正确、**且那个目标真的能启动桌宠**"三条合起来（都已实测通过），
剩下的是 Windows 的登录时机问题。② **双击 `打开启动文件夹` 真的弹出资源管理器**
——自检拦下 `QDesktopServices.openUrl` 验到了 URL 是 `shell:startup`
（task11 起就这么验的），"资源管理器窗口真的弹出来"要看观感。

> **§A 自查**：① **没有**同一个问题试 3 次以上——首轮 10 项 FAIL 里 **8 项一次就
> 判定是测试自己写错**（`_code_only()` 把 `read_shortcut(` 拆成
> `read_shortcut (` 却拿带括号的字面量去判、同一份代码里出现了**第 6 次**；
> 拿 `APPDATA` 去搜已剥掉字符串的代码；`[ref]$errors` 的变量没声明；
> `_function_body` 靠"下一个 `def`"切边界把后面的函数圈了进来；
> 拿"全程结束后只剩两个文件"去断言一个**已经被自己删掉一个**的目录）。
> 产品按事实改了**两处**，每一处都有现场判据：
> **①** `TEXT_WORKDIR` 混用"完整句子"与"后缀" → 渲染出 `已开启已开启［…］`（自检抓到）；
> **②** "成功后再查一次、以查询为准"会把处理器刚做成的结果覆盖成相反的值、
> 且 `enabled` 与 `status_text` 来源不一致（自检抓到）。
> 第一轮我把真平台那 16 项 FAIL 直接当成产品 bug，**停下来查了一次**才确认是
> DSH 沙箱拒绝写工作区外路径——**没有**去改产品逻辑迁就它（§4.11"先怀疑测试写错了"）。
> ② **没有**改到卡范围之外：打包 / `.spec` / 计划任务 / 管理员权限 / 全机器级自启 /
> 自动重试 / 注册表 `Run` 键 / 托盘一律没碰（卡里明写不做），
> 自启项的去留**只由用户勾选决定**。
> ③ 文档改动集中在 §2.22 新增小节 + §4.22 新增小节 + §1.2 一行 + 本节，
> 与代码改动（1 个新模块 + 2 个外部脚本 + 4 个模块改）**同量级**。
> ④ 临时探针（`data/probe_t14_*.py`、`data/probe_t14_scratch/`、`data/probe_ps_out.*`、
> `data/task14_scratch/`、`data/task14_real_scratch/`，以及回归跑出来的
> `data/task13_scratch*`）**已全部删掉**（`data/` 下现在只剩日志与各 task 的
> `*_check.txt` / `*_real.txt`）。
> ⑤ **规格缺口**：卡里要求先读的 `agent.md` 那一段与「附录 B」都在你删掉的
> `迁移清单.md` 里，**本机已无该文件**。处理方式：卡里 §4 那张**状态文案表**
> 本身给全了七条真值，逐字符照做；卡里没给的部分按合理默认落地并记在此：
> **① `.cmd` 回退项的文件名**取 `deskpet.cmd`（卡里 §1/§2 明写）；
> **② `LEGACY_MARKERS` 清单**卡里只说"主动删除早期版本误放的标记文件"，
> 没给文件名，取了四个最可能的名字（`deskpet.marker` / `deskpet.state` /
> `deskpet.autostart` / `.deskpet`），**只删这些名字、不做通配**；
> **③ 状态栏在只读查询失败时的兜底**取 `autostart.TEXT_DISABLED`。
> ⑥ **下一阶段需你确认**：task15（截屏）/ task16（录屏）**当前不存在这两个文件**，
> 且 D11 已定案"本阶段不做截屏/录屏"。**主功能（task01–14）到此全部完成**，
> 要不要新开截屏/录屏任务请你定。

**已知待清理（不阻塞主线）**：
- 遗留兜底帧率表 `pet_spec.LEGACY_ACTION_FPS` 条件已满足（两个包都补完 `fps`），可作为一次独立清理任务删掉（**D9**）。
- task01 的五个模块里带的 `from __future__ import annotations` 由用户自行删除（§2.1），后续 task 不要再加回来。

| # | 待决策 | 最晚定案 | 当前状态 |
| --- | --- | --- | --- |
| D1 | 透明方案 | task01 之前 | ✅ **已定案**：素材重做为透明背景 PNG（不做色键转换、不重烘 RGB 帧） |
| D2 | 点击穿透 | task01 之前 | ✅ **已定案**：`setMask(QRegion)`（接受边缘锯齿） |
| D3 | 缩放算法：`SmoothTransformation` / 三倍最近邻 | task04 之前 | ✅ **已定案（task04）**：`SmoothTransformation`。理由：源 GIF 是 1 位 alpha，放大时最近邻会把台阶等比放大；`SmoothTransformation` 柔化边缘，代价只是边缘多出半透明像素，而"`setMask` 有锯齿"本来就是已接受的取舍 |
| D4 | 音频后端：`QMediaPlayer`（可播 mp3）/ `QSoundEffect`（只支持 wav） | task10 之前 | ✅ **已定案（task10）**：`QMediaPlayer` + `QAudioOutput`（要能播 mp3；**不需要额外依赖**，`PySide6.QtMultimedia` 实测可用） |
| D5 | 全局热键路线：原生消息过滤器 / 子线程+队列+定时器 | task12 之前 | ✅ **已定案（task12）**：**子线程 + `GetMessageW` 消息循环 + `queue` + GUI 线程 100 ms 取件定时器**。理由：不碰主线程消息钩子（隐藏期间没有"钩子被摘"的风险）、唤醒是事件驱动（隐藏期间 CPU 0）、退出安全。**`hotkey_filter.py` 没有创建**；**原定的 120 ms 轮询已去掉** |
| D6 | 单实例机制：`QLocalServer` / 命名互斥体 | task13 之前 | ✅ **已定案（task13，2025 用户确认）**：**命名互斥体**（`CreateMutexW`，`win32.py` 统一出口）。理由：作用域**天然是当前登录会话**（名字不带 `Global\`）、不落任何文件、`QApplication` 之前就能跑（`QLocalServer` 两条都不满足） |
| D7 | 自带素材包补 `fps` | 任意 | ✅ **已落实**：两个包、13 个动作全是 `30.0` |
| D8 | `hitmask.json` 是否继续支持 | task03 之前 | ✅ **已定案：不再需要**（`setMask` 按 alpha 生成区域），读取与降级一律不做 |
| D9 | 遗留兜底表何时删除 | 用户补完 `fps` 之后 | ⬜ 未定——**条件已满足**，可随时作为一次独立清理任务删掉 |
| D10 | 项目命名统一 | task01 之前 | ✅ **已定案：统一为 `deskPet`**（包名 `deskpet`） |
| D11 | 是否需要"截屏 / 录屏"功能 | task14 之后 | ✅ **已定案：本阶段不做**（task14 已完成，14 个 task 全部收尾；将来要做再新开任务） |
| D12 | **动作名** | task03 之前 | ✅ **已定案（2025 用户）**：`idle` / `running_left` / `running_right` / `waiting` / `rest` / `waving`；`review` / `sit` / `jumping` **全部作废、不许做别名映射**；编程侧与素材侧统一 |
| D14 | **`--fps`** | task03 之前 | ✅ **已定案：已删除，不得复活**（帧率由素材决定） |

> （上面没有 D13，因为该编号不被使用。）

**其他待办（不阻塞主线）**：本阶段 14 个 task **已全部完成**（task01–14）。
截屏 / 录屏若要新开任务，用 `tasks/task15.md` / `tasks/task16.md` 的编号
（**当前不存在这两个文件**，且 D11 已定案本阶段不做）。
打包阶段注意：`main.py` 顶层静态 import；
**⚠ `tools/make_shortcut.ps1` 与 `tools/read_shortcut.ps1` 必须显式声明进打包数据**
（漏掉的表现是"打包后开机自启永远显示不可用"，而**源码运行看不出来**，见 §2.22 末段）；
出包脚本要自检关键文件，构建机留在 `data/` 的日志必须删掉再发。
版本控制：仓库已 `git init`（分支 `main`），`.gitignore` 已忽略
`data/`、`__pycache__/`、`dist/`、`build/`、`*.spec`、虚拟环境目录；**每个 task 完成提交一次**
（`feat(阶段): 描述`，提交信息写到仓库外的临时文件再引用）。
