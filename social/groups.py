"""
群聊管理模块

功能：
  - 创建群聊（群主）
  - 添加 / 移除群成员（群主权限）
  - 设置成员文件修改权限（群主权限）
  - 解散群聊（群主权限）
  - 保存聊天记录（具体内容格式由其他模块负责）
  - 查询聊天记录 & 群信息

表结构（自动创建）：
  chat_groups         群聊基本信息
  chat_group_members  群成员及权限
  chat_messages       聊天记录
"""
import sqlite3
from datetime import datetime, timezone
from social.db import get_conn, transactional

# ---------------------------------------------------------------------------
# 建表
# ---------------------------------------------------------------------------

_tables_created = False


def _ensure_tables():
    global _tables_created
    if _tables_created:
        return
    conn = get_conn()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS chat_groups (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            owner_id INTEGER NOT NULL,
            created_at TEXT NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS chat_group_members (
            group_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            can_modify_files INTEGER DEFAULT 0,
            joined_at TEXT NOT NULL,
            PRIMARY KEY(group_id, user_id)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS chat_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            group_id INTEGER NOT NULL,
            sender_id INTEGER NOT NULL,
            message_type TEXT DEFAULT 'text',
            content TEXT DEFAULT '',
            created_at TEXT NOT NULL
        )
    """)
    _tables_created = True


# ---------------------------------------------------------------------------
# 群聊 CRUD
# ---------------------------------------------------------------------------

@transactional
def create_group(owner_id: int, name: str) -> dict:
    """
    创建群聊。
    群主自动加入群聊并拥有最高权限。
    """
    _ensure_tables()
    conn = get_conn()
    now = _now()

    if not name.strip():
        raise ValueError("群名称不能为空")

    cursor = conn.execute(
        "INSERT INTO chat_groups (name, owner_id, created_at) VALUES (?, ?, ?)",
        (name.strip(), owner_id, now)
    )
    group_id = cursor.lastrowid

    # 群主自动加入
    conn.execute(
        "INSERT INTO chat_group_members (group_id, user_id, can_modify_files, joined_at) "
        "VALUES (?, ?, 1, ?)",
        (group_id, owner_id, now)
    )

    return {"status": "created", "group_id": group_id}


@transactional
def dissolve_group(group_id: int, owner_id: int) -> dict:
    """
    解散群聊。
    仅群主可以操作，会删除群聊、成员关系和所有聊天记录。
    """
    _ensure_tables()
    conn = get_conn()

    grp = conn.execute(
        "SELECT owner_id FROM chat_groups WHERE id = ?", (group_id,)
    ).fetchone()
    if not grp:
        raise ValueError("群聊不存在")

    # 管理员可以解散任意群聊
    from social.models import check_permission
    is_admin = check_permission(owner_id, "moderate_content")
    if grp["owner_id"] != owner_id and not is_admin:
        raise ValueError("仅群主可以解散群聊")

    # 记录管理员解散
    if is_admin and grp["owner_id"] != owner_id:
        note = f"管理员 {owner_id} 解散了群主 {grp['owner_id']} 的群聊"

    conn.execute("DELETE FROM chat_messages WHERE group_id = ?", (group_id,))
    conn.execute("DELETE FROM chat_group_members WHERE group_id = ?", (group_id,))
    conn.execute("DELETE FROM chat_groups WHERE id = ?", (group_id,))

    return {"status": "dissolved"}


# ---------------------------------------------------------------------------
# 成员管理（群主权限）
# ---------------------------------------------------------------------------

@transactional
def add_member(group_id: int, owner_id: int, user_id: int) -> dict:
    """
    添加群成员。
    仅群主可以操作。
    """
    _ensure_tables()
    conn = get_conn()

    grp = conn.execute(
        "SELECT owner_id FROM chat_groups WHERE id = ?", (group_id,)
    ).fetchone()
    if not grp:
        raise ValueError("群聊不存在")
    if grp["owner_id"] != owner_id:
        raise ValueError("仅群主可以添加成员")

    try:
        conn.execute(
            "INSERT INTO chat_group_members (group_id, user_id, can_modify_files, joined_at) "
            "VALUES (?, ?, 0, ?)",
            (group_id, user_id, _now())
        )
    except sqlite3.IntegrityError:
        return {"status": "already_member"}
    return {"status": "added"}


@transactional
def remove_member(group_id: int, owner_id: int, user_id: int) -> dict:
    """
    移除群成员。
    仅群主可以操作，但不能移除群主自己。
    """
    _ensure_tables()
    conn = get_conn()

    grp = conn.execute(
        "SELECT owner_id FROM chat_groups WHERE id = ?", (group_id,)
    ).fetchone()
    if not grp:
        raise ValueError("群聊不存在")
    if grp["owner_id"] != owner_id:
        raise ValueError("仅群主可以移除成员")
    if user_id == owner_id:
        raise ValueError("群主不能被移除，请使用解散群聊")

    deleted = conn.execute(
        "DELETE FROM chat_group_members WHERE group_id = ? AND user_id = ?",
        (group_id, user_id)
    ).rowcount
    return {"status": "removed" if deleted else "not_member"}


# ---------------------------------------------------------------------------
# 文件修改权限
# ---------------------------------------------------------------------------

@transactional
def set_file_permission(group_id: int, owner_id: int,
                        user_id: int, can_modify: bool) -> dict:
    """
    设置群成员的文件修改权限。
    仅群主可以操作。
    can_modify: True 允许修改群文件, False 禁止
    """
    _ensure_tables()
    conn = get_conn()

    grp = conn.execute(
        "SELECT owner_id FROM chat_groups WHERE id = ?", (group_id,)
    ).fetchone()
    if not grp:
        raise ValueError("群聊不存在")
    if grp["owner_id"] != owner_id:
        raise ValueError("仅群主可以设置权限")

    updated = conn.execute(
        "UPDATE chat_group_members SET can_modify_files = ? WHERE group_id = ? AND user_id = ?",
        (int(can_modify), group_id, user_id)
    ).rowcount
    return {"status": "updated" if updated else "not_member"}


def get_member_permissions(group_id: int, user_id: int) -> dict | None:
    """获取某成员在群中的权限信息"""
    _ensure_tables()
    conn = get_conn()
    row = conn.execute("""
        SELECT cgm.group_id, cgm.user_id, cgm.can_modify_files, cgm.joined_at,
               u.username, u.display_name
        FROM chat_group_members cgm
        JOIN users u ON u.id = cgm.user_id
        WHERE cgm.group_id = ? AND cgm.user_id = ?
    """, (group_id, user_id)).fetchone()
    return dict(row) if row else None


def get_group_members(group_id: int) -> list[dict]:
    """获取群成员列表（含权限）"""
    _ensure_tables()
    conn = get_conn()
    rows = conn.execute("""
        SELECT cgm.group_id, cgm.user_id, cgm.can_modify_files, cgm.joined_at,
               u.username, u.display_name, u.acct, u.avatar,
               CASE WHEN cg.owner_id = cgm.user_id THEN 1 ELSE 0 END AS is_owner
        FROM chat_group_members cgm
        JOIN users u ON u.id = cgm.user_id
        JOIN chat_groups cg ON cg.id = cgm.group_id
        WHERE cgm.group_id = ?
        ORDER BY is_owner DESC, cgm.joined_at
    """, (group_id,)).fetchall()
    return [dict(r) for r in rows]


def get_user_groups(user_id: int) -> list[dict]:
    """获取用户参与的所有群聊"""
    _ensure_tables()
    conn = get_conn()
    rows = conn.execute("""
        SELECT cg.id AS group_id, cg.name, cg.owner_id, cg.created_at,
               cgm.can_modify_files,
               ou.username AS owner_username, ou.display_name AS owner_display,
               (SELECT COUNT(*) FROM chat_group_members WHERE group_id = cg.id) AS member_count
        FROM chat_group_members cgm
        JOIN chat_groups cg ON cg.id = cgm.group_id
        JOIN users ou ON ou.id = cg.owner_id
        WHERE cgm.user_id = ?
        ORDER BY cg.created_at DESC
    """, (user_id,)).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# 聊天记录
# ---------------------------------------------------------------------------

@transactional
def save_chat_message(group_id: int, sender_id: int,
                      message_type: str = "text",
                      content: str = "") -> dict:
    """
    保存一条聊天记录。
    具体的 content 格式（文字/文件/语音/表情）由调用方决定。
    """
    _ensure_tables()
    conn = get_conn()

    # 验证发送者是群成员
    member = conn.execute(
        "SELECT 1 FROM chat_group_members WHERE group_id = ? AND user_id = ?",
        (group_id, sender_id)
    ).fetchone()
    if not member:
        raise ValueError("你不是该群成员")

    now = _now()
    cursor = conn.execute(
        "INSERT INTO chat_messages (group_id, sender_id, message_type, content, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (group_id, sender_id, message_type, content, now)
    )
    return {"status": "saved", "message_id": cursor.lastrowid}


def get_chat_history(group_id: int, user_id: int,
                     limit: int = 50, before_id: int | None = None) -> list[dict]:
    """
    获取聊天记录。
    user_id 用于验证是否为群成员。
    before_id 用于分页（加载更早的消息）。
    """
    _ensure_tables()
    conn = get_conn()

    member = conn.execute(
        "SELECT 1 FROM chat_group_members WHERE group_id = ? AND user_id = ?",
        (group_id, user_id)
    ).fetchone()
    if not member:
        raise ValueError("你不是该群成员")

    if before_id:
        rows = conn.execute("""
            SELECT cm.id, cm.group_id, cm.sender_id, cm.message_type,
                   cm.content, cm.created_at,
                   u.username, u.display_name, u.avatar
            FROM chat_messages cm
            JOIN users u ON u.id = cm.sender_id
            WHERE cm.group_id = ? AND cm.id < ?
            ORDER BY cm.id DESC
            LIMIT ?
        """, (group_id, before_id, limit)).fetchall()
    else:
        rows = conn.execute("""
            SELECT cm.id, cm.group_id, cm.sender_id, cm.message_type,
                   cm.content, cm.created_at,
                   u.username, u.display_name, u.avatar
            FROM chat_messages cm
            JOIN users u ON u.id = cm.sender_id
            WHERE cm.group_id = ?
            ORDER BY cm.id DESC
            LIMIT ?
        """, (group_id, limit)).fetchall()

    # 时间正序（聊天风格）
    msgs = [dict(r) for r in rows]
    msgs.reverse()
    return msgs


# ---------------------------------------------------------------------------
# 辅助
# ---------------------------------------------------------------------------

def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
