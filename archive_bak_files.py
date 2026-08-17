# -*- coding: utf-8 -*-
"""E-136 P2-6 .bak 历史碎片归档工具
功能：
  对 catalog/ 与 web/ 下的 .bak* 文件进行整洁化归档迁移。
  规则：
    1. 仅扫描 .bak / .bak-* 文件；
    2. 产生时间超过 3 天（72小时）；
    3. 同一基准文件仅保留最新 1 个 .bak 备份，超出部分移入 _archive/bak-<YYYYMMDD>/；
    4. 支持 --dry-run 查看待迁移清单而不实际移动；
    5. 移动操作使用 shutil.move（非删除，完全可回滚）；
    6. 执行具有完全幂等性。
用法：
  python archive_bak_files.py [--dry-run]
"""
import os
import sys
import glob
import time
import shutil
import datetime

ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
CATALOG_DIR = os.path.join(ROOT_DIR, "catalog")
WEB_DIR = os.path.join(ROOT_DIR, "web")
ARCHIVE_BASE = os.path.join(ROOT_DIR, "_archive")

RETENTION_DAYS = 3
RETENTION_SECONDS = RETENTION_DAYS * 86400


def collect_bak_files():
    patterns = [
        os.path.join(CATALOG_DIR, "**/*.bak*"),
        os.path.join(WEB_DIR, "**/*.bak*"),
    ]
    files = []
    for pat in patterns:
        for f in glob.glob(pat, recursive=True):
            if os.path.isfile(f) and not f.startswith(ARCHIVE_BASE):
                files.append(os.path.abspath(f))
    return sorted(list(set(files)))


def run_archive(dry_run=False):
    now = time.time()
    today_str = datetime.datetime.now().strftime("%Y%m%d")
    target_archive_dir = os.path.join(ARCHIVE_BASE, f"bak-{today_str}")

    bak_files = collect_bak_files()
    if not bak_files:
        print("[archive_bak] scan done: no .bak files to archive (0)")
        return 0

    # 按原始基准文件名归类: 例如 a.json.bak-123 -> a.json
    groups = {}
    for f in bak_files:
        base = os.path.basename(f)
        # 截取 .bak 之前的文件名作为 group_key
        prefix = base.split(".bak")[0]
        ext = ""
        if "." in prefix:
            ext = prefix[prefix.rfind("."):]
        group_key = (os.path.dirname(f), prefix)
        groups.setdefault(group_key, []).append(f)

    to_move = []
    for group_key, file_list in groups.items():
        # 按修改时间从新到旧排序
        file_list.sort(key=lambda x: os.path.getmtime(x), reverse=True)
        # 最新 1 个保留，其余的如果超过 RETENTION_DAYS 则归档
        for idx, fpath in enumerate(file_list):
            mtime = os.path.getmtime(fpath)
            age_days = (now - mtime) / 86400.0
            if idx >= 1 and age_days >= RETENTION_DAYS:
                to_move.append((fpath, age_days))

    print(f"[archive_bak] scanned {len(bak_files)} .bak files, eligible: {len(to_move)}")

    if not to_move:
        print("[archive_bak] nothing to archive (all within 3-day protection or newest baseline)")
        return 0

    if dry_run:
        print(f"[archive_bak] DRY-RUN preview (target: {target_archive_dir}):")
        for fpath, age in to_move[:15]:
            rel = os.path.relpath(fpath, ROOT_DIR)
            print(f"  [DRY-RUN MOVE] {rel} (age {age:.1f} days)")
        if len(to_move) > 15:
            print(f"  ... {len(to_move) - 15} more files pending")
        return 0

    os.makedirs(target_archive_dir, exist_ok=True)
    moved_count = 0
    for fpath, age in to_move:
        fname = os.path.basename(fpath)
        dest_path = os.path.join(target_archive_dir, fname)
        # 避免重名冲突
        if os.path.exists(dest_path):
            stem, ext = os.path.splitext(fname)
            dest_path = os.path.join(target_archive_dir, f"{stem}_{int(time.time())}{ext}")
        try:
            shutil.move(fpath, dest_path)
            moved_count += 1
        except Exception as e:
            print(f"[archive_bak WARN] move failed {fpath}: {e}")

    print(f"[archive_bak OK] archived {moved_count}/{len(to_move)} files to {target_archive_dir}")
    return 0


def main():
    dry_run = "--dry-run" in sys.argv
    try:
        return run_archive(dry_run=dry_run)
    except Exception as e:
        print(f"[archive_bak ERROR] exception: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
