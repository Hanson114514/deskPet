"""构建期工具：把 raw/ 下的 GIF 素材抽帧成 assets/<命名空间>/。

用法：
    python tools/extract_gif_frames.py [--raw raw] [--out assets] [--namespace NS ...]

约定（见 agent.md §2.3 / §2.5）：
- 每个动作一张 GIF，**动作名 = GIF 文件名去掉扩展名**（1:1 对应，不做语义改名）；
- 帧文件名 `<动作>_<序号>.png`，序号位数 = 帧数所需的最少位数（<100 用 2 位，<1000 用 3 位）；
- 帧画布 = GIF 画布（透明背景原样保留，不裁切、不缩放）；
- `cell` = 帧的实际像素尺寸；`fps` = 30.0（素材是 30 fps 序列，GIF 用 30/40 ms 混合量化）；
- `asset.json` 用**无 BOM 的 utf-8**写；人写键（`name` / `description` / `sounds` /
  各动作已有的 `fps`）若已存在则原样搬过去。

红线：不修改 `raw/` 下的任何文件。
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from PIL import Image

# 素材实测：GIF 每帧 30/40 ms 交替（2:1），平均 33.333 ms → 30 fps。
DEFAULT_FPS = 30.0

# 人写键：重写 asset.json 时必须原样搬过去（agent.md §2.5）。
HUMAN_KEYS = ("name", "description", "sounds")


def read_existing_index(path: Path) -> dict:
    """读已有 asset.json（容 BOM）；读不出来就当空的。"""
    if not path.is_file():
        return {}
    try:
        with path.open("r", encoding="utf-8-sig") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def frame_name(action: str, index: int, total: int) -> str:
    width = 2 if total < 100 else (3 if total < 1000 else 4)
    return f"{action}_{index:0{width}d}.png"


def extract_action(gif: Path, frames_dir: Path, previous: dict) -> dict:
    """抽一个 GIF，返回它在 asset.json 里的 actions 条目。"""
    action = gif.stem
    # 先清掉该动作的旧帧，避免帧数变少时留下孤儿文件。
    for stale in frames_dir.glob(f"{action}_*.png"):
        stale.unlink()

    names: list[str] = []
    cell: tuple[int, int] | None = None
    with Image.open(gif) as im:
        total = im.n_frames
        for i in range(total):
            im.seek(i)
            rgba = im.convert("RGBA")
            if cell is None:
                cell = rgba.size
            elif rgba.size != cell:
                raise SystemExit(f"{gif}: 第 {i} 帧尺寸 {rgba.size} 与首帧 {cell} 不一致")
            name = frame_name(action, i, total)
            rgba.save(frames_dir / name, "PNG")
            names.append(name)

    if cell is None:
        raise SystemExit(f"{gif}: 没有任何帧")

    entry: dict = {"frames": names, "fps": DEFAULT_FPS}
    # 尊重人写过的 fps（视为数、非 bool、> 0）；坏值丢弃。
    old = previous.get(action) if isinstance(previous, dict) else None
    if isinstance(old, dict):
        old_fps = old.get("fps")
        if isinstance(old_fps, (int, float)) and not isinstance(old_fps, bool) and old_fps > 0:
            entry["fps"] = float(old_fps)
    return action, entry, cell


def process_package(ns_dir: Path, out_root: Path, keep_human: bool) -> dict:
    gifs = sorted(ns_dir.glob("*.gif"))
    if not gifs:
        raise SystemExit(f"{ns_dir}: 没有 .gif 素材")

    ns_root = out_root / ns_dir.name
    frames_dir = ns_root / "frames"
    frames_dir.mkdir(parents=True, exist_ok=True)

    index_path = ns_root / "asset.json"
    previous = read_existing_index(index_path) if keep_human else {}
    prev_actions = previous.get("actions") if isinstance(previous.get("actions"), dict) else {}

    actions: dict = {}
    cells: set = set()
    for gif in gifs:
        action, entry, cell = extract_action(gif, frames_dir, prev_actions)
        if action in actions:
            raise SystemExit(f"{ns_dir}: 动作名 {action!r} 重复")
        actions[action] = entry
        cells.add(cell)

    if len(cells) != 1:
        raise SystemExit(f"{ns_dir}: 帧尺寸不统一：{sorted(cells)}")
    cell = cells.pop()

    index: dict = {}
    for key in HUMAN_KEYS:
        if key in previous:
            index[key] = previous[key]
    index["cell"] = [cell[0], cell[1]]
    index["actions"] = actions

    # 人写的键排在前面、cell/actions 在后；写无 BOM utf-8。
    with index_path.open("w", encoding="utf-8", newline="\n") as fh:
        json.dump(index, fh, ensure_ascii=False, indent=2)
        fh.write("\n")

    return {
        "namespace": ns_dir.name,
        "cell": list(cell),
        "actions": {name: len(entry["frames"]) for name, entry in actions.items()},
        "total_frames": sum(len(e["frames"]) for e in actions.values()),
        "index": str(index_path),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="把 raw/ 下的 GIF 素材抽帧到 assets/")
    parser.add_argument("--raw", default="raw", help="原始素材根目录（默认 raw）")
    parser.add_argument("--out", default="assets", help="输出素材根目录（默认 assets）")
    parser.add_argument("--namespace", action="append", default=None,
                        help="只处理指定命名空间，可重复；缺省处理 raw/ 下全部子目录")
    parser.add_argument("--fresh", action="store_true",
                        help="忽略已有 asset.json 里人写的键（默认保留）")
    args = parser.parse_args()

    raw_root = Path(args.raw)
    out_root = Path(args.out)
    if not raw_root.is_dir():
        raise SystemExit(f"原始素材目录不存在：{raw_root}")

    if args.namespace:
        ns_dirs = []
        for name in args.namespace:
            candidate = raw_root / name
            if not candidate.is_dir():
                raise SystemExit(f"命名空间目录不存在：{candidate}")
            ns_dirs.append(candidate)
    else:
        ns_dirs = sorted(p for p in raw_root.iterdir() if p.is_dir())

    report = []
    for ns_dir in ns_dirs:
        report.append(process_package(ns_dir, out_root, keep_human=not args.fresh))

    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"共 {len(report)} 个素材包，"
          f"{sum(r['total_frames'] for r in report)} 张帧")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
