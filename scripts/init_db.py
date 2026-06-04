#!/usr/bin/env python3
"""WebAppFlaskauto-iOS 数据库初始化 / 维护工具。

驱动 ``services.app_db`` 这一层（原生 sqlite3，非 SQLAlchemy），只操作账号 /
会话 / 设备占用三张表，刻意不导入 ``app.py``，以免触发 adb bootstrap、
后台线程等副作用——本工具应当能在不启动整套服务的情况下独立运行。

用法
----
    # 初始化数据库（建表 + 写入预设账号；幂等）
    python scripts/init_db.py init

    # 清空运行态数据：会话 + 设备占用 + 已注册的普通用户（保留预设账号与表结构）
    python scripts/init_db.py clear

    # 重置数据库（删除所有表后重建并重新写入预设账号；危险操作）
    python scripts/init_db.py reset

    # 查看数据库状态（账号、占用、文件大小）
    python scripts/init_db.py status

    # 备份数据库文件
    python scripts/init_db.py backup
"""

from __future__ import annotations

import shutil
import sys
from datetime import datetime
from pathlib import Path

# Windows 控制台默认非 UTF-8，中文输出会乱码——强制 UTF-8 编码。
try:
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, ValueError):
    pass

# 把项目根目录加入 import 路径，使脚本可从任意 cwd 运行。
project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))

from services import app_db as db_module  # noqa: E402

# 预设账号用户名集合——clear 时据此保留 super_admin / admin / user。
SEED_USERNAMES = {row[0] for row in db_module.SEED_ACCOUNTS}

# 项目登录与设备占用链路依赖的三类账号，初始状态必须齐全：
#   super_admin —— 兜底超管，权限最高、不可删除
#   admin       —— 管理员（管理普通用户、强制释放设备）
#   user        —— 普通用户（必须先占用设备才能控制）
REQUIRED_ROLES = ("super_admin", "admin", "user")


def _db_exists() -> bool:
    return db_module.DB_PATH.exists()


def _tables_exist(conn) -> bool:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='users'"
    ).fetchone()
    return row is not None


def _existing_usernames() -> set[str]:
    """Usernames currently in the DB (empty if no DB / no users table)."""
    if not _db_exists():
        return set()
    conn = db_module.get_conn()
    try:
        if not _tables_exist(conn):
            return set()
        return {r["username"] for r in conn.execute("SELECT username FROM users")}
    finally:
        conn.close()


def _verify_required_roles() -> bool:
    """Confirm super_admin / admin / user all exist — the trio the login &
    reservation flow assumes. Returns True when complete."""
    conn = db_module.get_conn()
    try:
        roles = {r["role"] for r in conn.execute("SELECT DISTINCT role FROM users")}
    finally:
        conn.close()
    missing = [r for r in REQUIRED_ROLES if r not in roles]
    if missing:
        print(f"⚠ 缺少必需角色: {', '.join(missing)} —— 请检查 services/app_db.py 的 SEED_ACCOUNTS")
        return False
    print(f"✓ 三类账号齐全: {' / '.join(REQUIRED_ROLES)}")
    return True


def _print_seed_credentials() -> None:
    """Surface the preset accounts + default passwords so the operator can log
    in immediately. Read straight from SEED_ACCOUNTS (single source of truth)."""
    print("预设账号（默认密码，首次登录请尽快修改）:")
    print(f"  {'用户名':<12}{'密码':<16}角色")
    for username, _email, password, role in db_module.SEED_ACCOUNTS:
        print(f"  {username:<12}{password:<16}{role}")


# ── 命令实现 ──────────────────────────────────────────────────────────────

def init_database() -> None:
    """建表并写入预设账号（super_admin / admin / user）。委托给
    services.app_db.init_db（幂等：仅补齐缺失的预设账号，不会覆盖已有账号）。"""
    print("开始初始化数据库...")
    before = _existing_usernames()  # 用于报告本次新建的预设账号
    db_module.init_db()
    print(f"数据库初始化完成: {db_module.DB_PATH}")

    created = sorted(SEED_USERNAMES - before)
    if created:
        # 例如旧库缺少 user：再次 init 即会补齐，这里明确告知。
        print(f"本次新建预设账号: {', '.join(created)}")
    else:
        print("预设账号已存在，未新增（幂等）。")

    print()
    _verify_required_roles()
    print()
    _print_seed_credentials()
    print()
    show_database_status()


def clear_database() -> None:
    """清空运行态数据：会话、设备占用、已注册普通用户（保留预设账号）。"""
    if not _db_exists():
        print("数据库尚未初始化，无需清空。先运行 `init`。")
        return
    conn = db_module.get_conn()
    try:
        if not _tables_exist(conn):
            print("表结构不存在，无需清空。先运行 `init`。")
            return
        placeholders = ",".join("?" * len(SEED_USERNAMES))
        sessions = conn.execute("DELETE FROM user_sessions").rowcount
        reservations = conn.execute("DELETE FROM device_reservations").rowcount
        users = conn.execute(
            f"DELETE FROM users WHERE username NOT IN ({placeholders})",
            tuple(SEED_USERNAMES),
        ).rowcount
        conn.commit()
    finally:
        conn.close()
    print("=" * 56)
    print("数据库运行态数据已清空（预设账号与表结构保留）")
    print("=" * 56)
    print(f"  user_sessions       : {sessions} 条")
    print(f"  device_reservations : {reservations} 条")
    print(f"  users (非预设)       : {users} 条")
    print("=" * 56)


def reset_database() -> None:
    """删除所有表后重建并重新写入预设账号。"""
    print("开始重置数据库...")
    if _db_exists():
        conn = db_module.get_conn()
        try:
            conn.executescript(
                """
                DROP TABLE IF EXISTS user_sessions;
                DROP TABLE IF EXISTS device_reservations;
                DROP TABLE IF EXISTS users;
                """
            )
            conn.commit()
        finally:
            conn.close()
        print("已删除所有数据表")
    # 复位模块级幂等开关，确保 init_db 真正重建。
    db_module._initialised = False
    db_module.init_db()
    print("已重新创建数据表并写入预设账号")
    print()
    _verify_required_roles()
    print()
    _print_seed_credentials()
    print()
    show_database_status()


def backup_database() -> str | None:
    """复制数据库文件，返回备份路径。"""
    if not _db_exists():
        print("数据库文件不存在，无法备份。")
        return None
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = db_module.DB_PATH.with_name(f"{db_module.DB_PATH.name}.backup_{timestamp}")
    shutil.copy2(db_module.DB_PATH, backup_path)
    print(f"数据库备份完成: {backup_path}")
    return str(backup_path)


def show_database_status() -> None:
    """打印账号、设备占用与数据库文件信息。"""
    print("=" * 56)
    print("数据库状态报告")
    print("=" * 56)
    if not _db_exists():
        print(f"数据库未初始化（文件不存在）: {db_module.DB_PATH}")
        print("=" * 56)
        return

    conn = db_module.get_conn()
    try:
        if not _tables_exist(conn):
            print("数据库文件存在但表结构缺失，请运行 `init`。")
            print("=" * 56)
            return

        users = conn.execute(
            "SELECT username, email, role, is_active FROM users ORDER BY id"
        ).fetchall()
        sessions_count = conn.execute(
            "SELECT COUNT(*) AS c FROM user_sessions"
        ).fetchone()["c"]
        reservations = conn.execute(
            "SELECT device_id, username, expires_at FROM device_reservations "
            "ORDER BY created_at"
        ).fetchall()
    finally:
        conn.close()

    roles_present = {u["role"] for u in users}
    print(f"账号总数: {len(users)}")
    for u in users:
        state = "启用" if u["is_active"] else "停用"
        tag = "预设" if u["username"] in SEED_USERNAMES else "注册"
        print(f"  - [{tag}] {u['username']:<14} {u['role']:<12} {state:<4} {u['email']}")
    missing = [r for r in REQUIRED_ROLES if r not in roles_present]
    if missing:
        print(f"  ⚠ 缺少必需角色: {', '.join(missing)} —— 运行 `python scripts/init_db.py init` 补齐")
    else:
        print(f"  ✓ 三类账号齐全: {' / '.join(REQUIRED_ROLES)}")
    print()
    print(f"活动会话数: {sessions_count}")
    print(f"设备占用数: {len(reservations)}")
    for r in reservations:
        print(f"  - {r['device_id']:<24} 占用者={r['username']:<14} 到期={r['expires_at']}")
    print()

    size = db_module.DB_PATH.stat().st_size
    print(f"数据库文件: {db_module.DB_PATH}")
    print(f"文件大小: {size / 1024:.2f} KB")
    print("=" * 56)


# ── CLI ─────────────────────────────────────────────────────────────────

def _print_help() -> None:
    print("可用命令:")
    print("  init    - 初始化数据库（建表 + 预设账号，幂等）")
    print("  clear   - 清空运行态数据（会话/占用/已注册用户，保留预设账号）")
    print("  reset   - 重置数据库（删除所有表并重建，危险操作）")
    print("  status  - 显示数据库状态")
    print("  backup  - 备份数据库文件")


def main() -> None:
    command = sys.argv[1] if len(sys.argv) > 1 else "init"

    if command == "init":
        init_database()
    elif command == "clear":
        confirm = input("确定清空运行态数据吗？（会话/占用/已注册用户将被删除）(y/N): ")
        if confirm.lower() == "y":
            clear_database()
        else:
            print("操作已取消")
    elif command == "reset":
        confirm = input("确定重置数据库吗？这将删除包括账号在内的所有数据！(y/N): ")
        if confirm.lower() == "y":
            reset_database()
        else:
            print("操作已取消")
    elif command == "status":
        show_database_status()
    elif command == "backup":
        backup_database()
    else:
        print(f"未知命令: {command}\n")
        _print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
