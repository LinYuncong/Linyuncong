"""
用户 & 帖子 CRUD 辅助函数

为其他模块提供基础的数据读写能力。
"""

import sqlite3
from datetime import datetime, timezone
from social.db import get_conn, transactional


# ---------------------------------------------------------------------------
# 用户
# ---------------------------------------------------------------------------

def get_user(user_id: int) -> dict | None:
    """获取单个用户信息"""
    conn = get_conn()
    row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    return dict(row) if row else None#查到数据则转成字典并返回


def get_user_by_username(username: str) -> dict | None:
    """通过用户名查找用户"""
    conn = get_conn()
    row = conn.execute(
        "SELECT * FROM users WHERE username = ?", (username,)
    ).fetchone()
    return dict(row) if row else None


def get_user_by_email(email: str) -> dict | None:
    """通过邮箱查找用户"""
    conn = get_conn()
    row = conn.execute(
        "SELECT * FROM users WHERE email = ?", (email,)
    ).fetchone()
    return dict(row) if row else None


@transactional
def create_user(username: str,
                email: str,
                password_hash: str,
                display_name: str = "",
                acct: str | None = None,
                note: str = "",
                locked: bool = False,
                bot: bool = False,
                avatar: str = "",
                header: str = "",
                default_privacy: str = "public",
                language: str = "zh-CN") -> dict:
    """创建新用户"""
    conn = get_conn()
    now = _now()

    try:
        cursor = conn.execute("""
            INSERT INTO users
            (username, email, password_hash, display_name, acct, note,
             locked, bot, avatar, header, default_privacy, language,
             created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            username, email, password_hash, display_name,
            acct or username, note,
            int(locked), int(bot), avatar, header,
            default_privacy, language, now, now
        ))#若acct为空，则acct=username
        return {"status": "created", "id": cursor.lastrowid}
    except Exception as e:
        if "UNIQUE" in str(e):
            raise ValueError("用户名或邮箱已存在") from e
        raise


@transactional
def update_user(user_id: int, **kwargs) -> dict:
    """更新用户字段"""
    conn = get_conn()
    allowed = [
        "display_name", "note", "locked", "bot", "limited",
        "avatar", "header", "url", "role", "fields",
        "default_privacy", "language", "last_status_at"
    ]#只允许修改白名单内的内容

    sets = []
    values = []
    for k, v in kwargs.items():
        if k in allowed:
            sets.append(f"{k} = ?")
            values.append(v)#字段在白名单内则加入sets和value，不在则忽略

    if not sets:
        return {"status": "no_changes"}

    values.append(_now())
    values.append(user_id)

    conn.execute(
        f"UPDATE users SET {', '.join(sets)}, updated_at = ? WHERE id = ?",
        values
    )
    return {"status": "updated"}


# ---------------------------------------------------------------------------
# 帖子
# ---------------------------------------------------------------------------

def get_post(post_id: int, viewer_id: int | None = None) -> dict | None:
    """
    获取单条帖子。
    viewer_id 用于判断 public / friends_only / private / direct 帖子的可见性，
    同时会检查 visible_to 白名单和 invisible_to 黑名单。
    """
    conn = get_conn()
    row = conn.execute("""
        SELECT p.*, u.username, u.display_name, u.acct, u.avatar
        FROM posts p
        JOIN users u ON u.id = p.author_id
        WHERE p.id = ?
    """, (post_id,)).fetchone()

    if not row:
        return None

    post = dict(row)

    # 统一的可见性检查（支持 public / friends_only / private / direct + 黑白名单）
    from social.social import check_post_visibility
    visible, _ = check_post_visibility(post_id, viewer_id)#调用social.py中的函数，判断帖子是否可见
    if not visible:
        return None

    # 获取媒体附件
    media = conn.execute(
        "SELECT * FROM media_attachments WHERE post_id = ?",
        (post_id,)
    ).fetchall()
    post["media_attachments"] = [dict(m) for m in media]

    # 获取提及用户
    mentions = conn.execute("""
        SELECT u.id, u.username, u.display_name, u.acct
        FROM post_mentions pm
        JOIN users u ON u.id = pm.mentioned_user_id
        WHERE pm.post_id = ?
    """, (post_id,)).fetchall()
    post["mentions"] = [dict(m) for m in mentions]

    # 获取标签
    tags = conn.execute("""
        SELECT t.id, t.name, t.url
        FROM post_tags pt
        JOIN tags t ON t.id = pt.tag_id
        WHERE pt.post_id = ?
    """, (post_id,)).fetchall()
    post["tags"] = [dict(t) for t in tags]

    return post


# ---------------------------------------------------------------------------
# 辅助
# ---------------------------------------------------------------------------

def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
