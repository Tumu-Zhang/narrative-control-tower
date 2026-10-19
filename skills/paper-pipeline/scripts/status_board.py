# -*- coding: utf-8 -*-
"""状态看板生成/校验脚本（paper-pipeline 配套，通用版）。

磁盘实际落位是唯一真相源；本脚本只读磁盘与看板，不读任何叙述性文档。
章节与小节的识别正则可用参数适配（默认适配"第N章 目录 + 'N.M '小节目录"）。

用法：
    python status_board.py --root "<项目根>" --check    # 只比对（默认）
    python status_board.py --root "<项目根>" --write    # 生成/刷新看板
    python status_board.py --root <项目根> --write \
        --chapter-pattern "第[一二三四五六七八九十]+章" --section-pattern "^\\d+\\.\\d+\\s"

退出码：0 = 看板与磁盘一致；2 = 存在差异或看板缺失。
"""
from __future__ import annotations

import argparse
import csv
import re
import sys
from datetime import datetime
from pathlib import Path

DEFAULT_BOARD_REL = "00_项目管理/状态看板.csv"

BOARD_COLUMNS = [
    "章", "节", "目录路径", "约束", "草稿", "校验", "正文", "图表",
    "引用数", "状态", "阻塞项", "最后更新",
]
STATUS_VALUES = [
    "未启动", "已初始化待重启", "撰写中", "已校核待入库", "已入库", "已出库",
]
RESTART_HINTS = ("已初始化", "待重启", "待重跑", "回退至初始态")
REF_RE = re.compile(r"REF-\d{4}")  # 参考文献库编号体例，可按需调整

COMPARE_COLUMNS = ["目录路径", "约束", "草稿", "校验", "正文", "图表", "状态"]


def files_under(d: Path):
    if not d.is_dir():
        return []
    return [p for p in d.rglob("*") if p.is_file()]


def _flag(d: Path) -> str:
    return "1" if files_under(d) else "0"


def _ref_count(section: Path) -> int:
    refs = set()
    for sub in ("正文", "草稿"):
        for p in files_under(section / sub):
            if p.suffix.lower() not in (".md", ".txt"):
                continue
            try:
                refs.update(REF_RE.findall(p.read_text(encoding="utf-8", errors="ignore")))
            except OSError:
                continue
    return len(refs)


def _newest_mtime(section: Path) -> str:
    stamps = []
    for p in files_under(section):
        try:
            stamps.append(p.stat().st_mtime)
        except OSError:
            continue
    return datetime.fromtimestamp(max(stamps)).strftime("%Y-%m-%d") if stamps else ""


def derive_status(section: Path) -> str:
    """按磁盘实际落位推导小节状态。"""
    draft = files_under(section / "草稿")
    check = files_under(section / "校验")
    body = files_under(section / "正文")
    if body:
        return "已入库"
    if any("出库稿" in p.name for p in draft):
        return "已出库"
    if draft and check:
        return "已校核待入库"
    if draft:
        return "撰写中"
    blob = ""
    for p in (section / "任务约束.md", section / "数据与来源" / "source_notes.md"):
        if p.is_file():
            try:
                blob += p.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                pass
    if any(h in blob for h in RESTART_HINTS):
        return "已初始化待重启"
    return "未启动"


def scan_section(chapter: Path, section: Path) -> dict:
    return {
        "章": chapter.name,
        "节": section.name,
        "目录路径": "{}/{}".format(chapter.name, section.name),
        "约束": "1" if (section / "任务约束.md").is_file() else "0",
        "草稿": _flag(section / "草稿"),
        "校验": _flag(section / "校验"),
        "正文": _flag(section / "正文"),
        "图表": _flag(section / "图表"),
        "引用数": str(_ref_count(section)),
        "状态": derive_status(section),
        "阻塞项": "",
        "最后更新": _newest_mtime(section),
    }


def scan_all(root: Path, chapter_re: re.Pattern, section_re: re.Pattern):
    rows = []
    for chapter in sorted(d for d in root.iterdir() if d.is_dir() and chapter_re.match(d.name)):
        for section in sorted(d for d in chapter.iterdir() if d.is_dir() and section_re.match(d.name)):
            rows.append(scan_section(chapter, section))
    return rows


def read_board(path: Path):
    if not path.is_file():
        return None
    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


def write_board(path: Path, rows) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=BOARD_COLUMNS)
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k, "") for k in BOARD_COLUMNS})


def diff_rows(old, new) -> list:
    old_map = {r.get("目录路径", ""): r for r in old}
    new_map = {r.get("目录路径", ""): r for r in new}
    diffs = []
    for key in sorted(set(old_map) | set(new_map)):
        o, n = old_map.get(key), new_map.get(key)
        if o is None:
            diffs.append("[+] 新增小节 {}".format(key))
        elif n is None:
            diffs.append("[-] 小节消失 {}".format(key))
        else:
            for col in COMPARE_COLUMNS:
                if (o.get(col) or "") != (n.get(col) or ""):
                    diffs.append("[~] {} {}: {} -> {}".format(key, col, o.get(col), n.get(col)))
    return diffs


def main() -> int:
    parser = argparse.ArgumentParser(description="状态看板生成/校验（磁盘落位为唯一真相源）")
    parser.add_argument("--root", required=True, help="项目根目录")
    parser.add_argument("--board", default=DEFAULT_BOARD_REL, help="看板 CSV 相对路径")
    parser.add_argument("--chapter-pattern", default=r"第[一二三四五六七八九十]+章",
                        help="章节目录名匹配正则")
    parser.add_argument("--section-pattern", default=r"^\d+\.\d+\s",
                        help="小节目录名匹配正则")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--write", action="store_true", help="写入状态看板")
    group.add_argument("--check", action="store_true", help="只比对（默认）")
    args = parser.parse_args()

    root = Path(args.root)
    if not root.is_dir():
        print("[error] 项目根不存在：{}".format(root))
        return 2

    chapter_re = re.compile(args.chapter_pattern)
    section_re = re.compile(args.section_pattern)
    board_path = root / args.board
    rows = scan_all(root, chapter_re, section_re)

    if args.write:
        old = read_board(board_path) or []
        kept_blockers = {r.get("目录路径", ""): (r.get("阻塞项") or "") for r in old}
        for row in rows:
            row["阻塞项"] = kept_blockers.get(row["目录路径"], "")
        board_path.parent.mkdir(parents=True, exist_ok=True)
        write_board(board_path, rows)
        print("[write] {}: {} 行".format(args.board, len(rows)))
        return 0

    old = read_board(board_path)
    if old is None:
        print("[check] 看板不存在：{}（首次使用请 --write 生成）".format(args.board))
        return 2
    diffs = diff_rows(old, rows)
    if diffs:
        print("[check] 看板与磁盘存在 {} 处差异：".format(len(diffs)))
        for d in diffs:
            print("  " + d)
        return 2
    print("[check] 一致：{} 个小节，无差异。".format(len(rows)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
