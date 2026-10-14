# task03 · 素材加载与一次列全校验：画出真实的菲比

## 1. 背景

- **task01 已完成**：透明置顶窗口 + 拖 + 退出。
- **task02 已完成**：统一路径模块（`bundled_pets_dir()` / `user_pets_dir()`）、配置读写、日志。
- 本 task 让窗口显示**真实素材的第一帧**（不再是占位图形），并把
  "**素材包 → 结构化描述对象**"这条链路建好。
- 素材已在仓库外（旧项目 `C:\projects\dotPet\assets\`）：
  `phoebe-2` / `weedy` / `weedy_sightseer`。
  **用户正在把它们重做为透明背景 PNG**（动作名 `review` → `waiting`，并自己在 `asset.json` 里补 `fps`）。

> **开始前必须先读**：`agent.md`（尤其 §0 已定案决策、"接口统一约定"里的 `asset.json` 字段表与帧率优先级）、
> `迁移清单.md` 第一部分 §1/§2、第二部分 §4/§5/§6/§7、第三部分 3.11 / 3.17 / 3.20 / 3.21、
> 以及**附录 D**：**D1（透明=素材自带 alpha）/ D2（`setMask`）/ D8（不做 `hitmask.json`）均已定案**，
> 只需留意 **D3（缩放算法）**——本 task 只做静态第一帧，**不需要**决定 D3，但**不要**把缩放写死成不可替换。

## 2. 本任务目标

运行后能看到：

1. `python run.py` 启动后，窗口里显示的是**真实的菲比第一帧**（站在右下角，不再有占位方块）；
2. 命令 `python run.py --list-pets` 打印三个可用素材包及各自校验结果与音效摘要，然后退出；
3. 命令 `python run.py --list-actions` 打印动作名、帧数**以及每个动作解析后的帧率与来源**
   （`asset.json` / 遗留兜底 / 默认 12），然后退出；
4. 命令 `python run.py --pet weedy` 显示**温蒂的第一帧**；`--sprite assets/weedy` 效果相同
   （两种说法走**同一条**解析规则：含路径分隔符或本身是已存在目录 → 按目录解释）；
5. `python run.py --pet 不存在的包` **当场报错并退出**（列出已找到的包），
   而**配置里**写一个坏包名时**只是回退到 `phoebe` + 一条警告**，程序照常启动。

> **注意**：本 task 显示的是**静态第一帧**。帧率解析结果要能通过 `--list-actions` **看见**，
> 但**动画播放本身属于 task05**。

## 3. 涉及文件

预计新增：

```
src/deskpet/pet_spec.py      PetSpec / ActionSpec 数据结构 + 帧率解析（含遗留兜底表与 DEFAULT_FPS）
src/deskpet/pet_loader.py    命名空间白名单校验、两根扫描、asset.json 解析、一次性列全校验
src/deskpet/nsrules.py       命名空间白名单规则（1–64、禁用字符、首尾空白、结尾点等）
```

预计修改：

```
src/deskpet/app.py           命令行解析扩到 --pet/--sprite/--action/--list-pets/--list-actions
src/deskpet/pet_window.py    从 PetSpec 取第一帧显示，去掉对 placeholder 的依赖
src/deskpet/config.py        接入"配置里 pet 指向坏包 → 回退默认 + 警告"的解析入口
src/deskpet/logger.py        校验问题必须一次性全部写进日志
```

预计新增（**运行产物**）：`data/pets/`（本 task 只在扫描时读取，**不创建、不导入**）

> 素材文件**从哪来**：本 task **不要**把素材复制进 `assets/`。
> 说明见下面"不要做"——这是本 task 唯一需要你确认的前置条件。

## 4. 接口约定

### 数据结构（`deskpet.pet_spec`）

| 符号 | 语义 |
| --- | --- |
| `ActionSpec` | 一个动作：`name`、`frames: tuple[Path, ...]`、`frame_ms: float`、`fps_source: str` |
| `PetSpec` | 一个包：`namespace`、`display_name`（`name` 缺失/空白 → 退回命名空间）、`description`、`cell: tuple[int, int]`、`actions: dict[str, ActionSpec]`、`action_order: tuple[str, ...]`、`sounds: dict[str, str]`（已过滤非法项）、`root: Path`、`source: str`（`"user"` / `"bundled"`） |
| `FrameRateSource` | 枚举/常量：`"asset"` / `"legacy"` / `"default"`（`--list-actions` 要打印它） |

**帧率解析（本 task 必须实现，且是唯一实现）**——优先级**从高到低**：

1. `asset.json` 的 `actions.<动作>.fps`（数字，可为小数；`bool` 不算）；
2. **遗留兜底表**：`idle 8.0`、`running-left/right 11.0`、`waiting 8.0`、`sit 8.25`、`jumping 8.0`；
3. **`DEFAULT_FPS = 12.0`**。

| 规则 | 约定 |
| --- | --- |
| 对外产物 | **每帧显示时长 `frame_ms = 1000.0 / fps`（浮点，不取整）**；`fps` 本身只在 `--list-actions` 显示 |
| 遗留表位置 | **只允许存在一处**（`pet_spec.py` 里的常量），其它模块必须引用它，不许另抄一份 |
| `DEFAULT_FPS` | **只允许存在一处**，且**不许在配置默认值里重复** |
| 坏 `fps` | 非数字 / bool / `<= 0` / NaN → 丢弃该字段 + 一条警告，**按"没有 fps"继续走下一层**，绝不抛异常 |

### 加载与校验（`deskpet.pet_loader`）

| 函数 | 语义 |
| --- | --- |
| `scan_pets() -> list[PetCandidate]` | **纯目录层面**扫描两个根（用户优先、名字排序去重），**不打开帧、不解析索引**（`--list-pets` 用它） |
| `resolve_pet(spec: str \| None, config_pet: str \| None) -> ResolutionResult` | 统一解析"一个说法"。**永不抛异常**；返回 `(PetSpec \| None, issues, fell_back: bool)` |
| `validate_pet(root: Path) -> list[PetIssue]` | **一次性列全**所有问题：缺 `asset.json` / 索引解析不了 / `cell` 非法 / 无动作 / 缺帧文件 / `fps` 非法。返回**问题列表**，不抛第一个异常就结束 |
| `load_pet(root: Path) -> PetSpec` | 校验通过后构建 `PetSpec`；有问题时抛带**完整问题列表**的异常 |
| `PetIssue` | `kind`（枚举）+ `namespace` + `action`（可空）+ `detail`（可读文字） |

**解析与回退规则（必须逐条实现）**：

| 来源 | 坏包时的行为 |
| --- | --- |
| 配置 `pet` | **回退默认形象 `phoebe` + 一条警告，不阻断启动**；解析动作**永不抛异常** |
| 命令行 `--pet` / `--sprite` | **如实报错并退出**（列出已找到的包），**绝不偷偷换形象** |
| 默认形象也坏 | 抛错并带出原因（"假装能跑"比报错更糟） |
| 默认形象真值 | **只允许存在一处**（`pet_spec.py` 的常量）；配置默认值是空串，不写死形象名 |

**命名空间白名单（`nsrules.py`）**：长度 1–64；目录名就是命名空间；
禁止 `/` `\` `:` `*` `?` `"` `<` `>` `|`、控制字符（0–31）、`..`、`.`、以点开头、
首尾空白、结尾的点；**允许**中文等非 ASCII；**允许中间空格**；
非字符串 / 空 / 超长一律拒绝；从目录反推命名空间 = 取路径末段。

**索引读取**：`asset.json` **一律用 `utf-8-sig` 读**（容 BOM）；**写**用无 BOM 的 `utf-8`。
`cell` 必须是**两个正数的数组**（**不是对象**）；布尔不算数。
（`hitmask.json` 已定案不做，见下）

### 帧数据（本 task 最小实现，task04 会接替）

| 符号 | 语义 |
| --- | --- |
| `load_frame_image(spec: PetSpec, action: str, index: int) -> QImage` | 读 PNG → 得到**带 alpha 的 `QImage`**。素材本身是透明背景 PNG，**所以这里没有任何颜色判定**：直接加载即可（必要时统一转成便于绘制的格式） |
| `frame_alpha_mask(image) -> QImage` | （可选，task08 用）从 alpha 通道得到二值蒙版，供 `setMask` 生成 `QRegion` |

- **绝不要**在渲染 / `setMask` / 任何下游环节引入色键判断——**透明度只有一个来源：素材的 alpha 通道**。
- 本 task **不做** `setMask`（窗口遮罩属 task01/task08）；这里只保证"帧能带 alpha 正确加载"。
- 顺带确认：**加载出来的帧周围确实是透明的**（用取色/肉眼确认一次，这是 D1 生效的第一个证据）。

## 5. 不要做

- ❌ **不做动画**（不建播放定时器、不换帧；帧率只解析不消费）
- ❌ **不做尺寸档位**（不缩放、不选档、不 `--list-sizes`；用原始 `cell` 尺寸显示即可）
- ❌ 不做命中判定/穿透（`setMask` 属 task01/task08；本 task 沿用 task01 的遮罩机制）
- ❌ 不做右键菜单 / 控制栏 / 设置窗口 / 热键 / 音效 / 自启 / 单实例
- ❌ **不做素材包导入功能**（不写 `data/pets/`）
- ❌ **不要改 `assets/` 下的任何素材**（重做素材是用户自己的事）
- ❌ 不要为了"能看到菲比"就自己造素材或转换素材——
  **如果实现时发现 `assets/` 是空的，停下来问我，不要自己复制、生成或转格式**

> **⚠ 前置条件（已与用户确认）**：用户会**自己**把素材重做为**透明背景 PNG**，
> 按原目录结构导入到 `C:\projects\deskPet\assets\<命名空间>/`，
> 动作名改为 `waiting`，并自己在 `asset.json` 里补 `fps`。
> **本 task 开始前先确认 `assets/` 里三个包已就位**；没就位**先问**，不要自己造。

## 6. 验收标准

- [ ] `python run.py` 显示的**不是占位方块**，而是菲比的第一帧
- [ ] 人物**周围是透明的**：能透过它看到桌面；**没有任何洋红/底色方块**
      （素材是带 alpha 的 PNG，D1 生效的直接证据）
- [ ] 人物边缘**没有一圈彩色残边或毛刺**（若有，说明素材 alpha 不干净，见 §3.3 教训——
      这是**要反馈给用户去修素材**的问题，不要在运行时写容差补救）
- [ ] `--list-pets` 打印三个包：命名空间、展示名、来源（user/bundled）、校验结论、音效摘要
      （`phoebe-2` 应有 `sit` + `chime` 两个音效；另外两个包应报"无音效"）
- [ ] `--list-actions` 打印 **6 个动作**及其帧数与**帧率来源**；
      如果用户已经补过 `fps`，来源应显示 `asset.json` 并显示用户写的值；
      没补的话显示**遗留兜底**（idle 8.0 / running-* 11.0 / waiting 8.0 / sit 8.25 / jumping 8.0）
- [ ] 手工把某个动作的 `fps` 改成 `3` → `--list-actions` 立刻显示 **3.0（来源：asset.json）**；
      改成 `0` / `"快"` / `true` → 回退到下一层（遗留兜底）+ 警告，**不崩**
- [ ] 手工**删掉**某个动作的 `fps` → 回到遗留兜底值
- [ ] `--pet weedy` 与 `--sprite assets/weedy` 显示**同一个**温蒂第一帧
- [ ] `--pet ../weedy` / `--pet "a/b"` / `--pet ""` → 报"素材包命名非法"，**不崩**
- [ ] `--pet 不存在的包` → 报错退出，错误信息里**列出已找到的包**
- [ ] 在 `config.json` 里把 `pet` 写成不存在的包 → 启动**不中断**，
      显示回退后的默认形象，日志里有警告（**并且本次不写配置**——回写属于 task11）
- [ ] 把 `asset.json` 改坏（例如删掉 `cell`、把 `cell` 写成对象、把 `fps` 写成 `"快"`、
      删掉一张帧文件）→ 校验**一次列出全部问题**，不是只报第一个
- [ ] 把 `asset.json` 存成 **UTF-8 with BOM** → 仍能正常解析（`utf-8-sig`）
- [ ] 素材索引里若还留着旧的 `review` 键 → 按普通动作名处理即可（**不要**写别名映射、
      不要偷偷改成 `waiting`）；只是 `mirror` 的适用范围认得 `idle`/`waiting`/`sit`
- [ ] 全套坏掉（把三个包都改坏 + 默认形象也不可用）→ 抛错并带出原因，**不是静默空窗口**
- [ ] 搜索确认：`nsrules` 的白名单字符集与本文件一致；遗留帧率表、`DEFAULT_FPS` 各只有一处定义；
      **全仓库搜不到色键（`FF00FF`）与 `hitmask` 相关实现**

## 7. 完成后请更新 agent.md 中“当前进度”一节

- 记录 task03 已完成 + 实际结果（三个包的校验结论、`--list-actions` 的真实输出样例）；
- 记录素材的**实际状态**：三个包是否已就位、是否已带 alpha、动作名是否已是 `waiting`、
  `asset.json` 里是否已补 `fps`（这些直接影响后面几个 task 的验收基线）；
- 记录实际观测到的帧率**来源分布**（哪些动作走 `asset.json`、哪些还在用遗留兜底）；
- 如果发现**素材 alpha 不干净**（边缘毛刺/残边），写进已知坑并**反馈给用户**（不要自己补救）；
- 下一个 task 指向 `tasks/task04.md`。
