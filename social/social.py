"""
关注 / 取关 / 屏蔽 / 静音 / 域名屏蔽

所有接口使用 user_id 表示当前操作者身份。
"""

import sqlite3
from datetime import datetime, timezone
from social.db import get_conn, transactional


# ---------------------------------------------------------------------------
# 关注 & 取关
# ---------------------------------------------------------------------------

@transactional
def follow(follower_id: int, following_id: int, show_reblogs: bool = True,
           notify: bool = False, languages: list | None = None) -> dict:
    """
    关注一个用户。
    如果目标用户设置了 locked=1，则创建关注请求而非直接关注。
    返回 {"status": "followed" | "requested" | "blocked"}
    """
    conn = get_conn()
    now = _now()

    if follower_id == following_id:
        raise ValueError("不能关注自己")

    # 检查是否被封禁
    if is_banned(follower_id):
        return {"status": "banned", "message": "你已被封禁，无法关注"}

    # 检查是否被对方屏蔽，被屏蔽则无法关注
    blocked = conn.execute(
        "SELECT 1 FROM blocks WHERE user_id = ? AND blocked_id = ?",
        (following_id, follower_id)
    ).fetchone()
    if blocked:
        return {"status": "blocked", "message": "你已被该用户屏蔽，无法关注"}

    # 检查是否已关注
    existing = conn.execute(
        "SELECT id FROM follows WHERE follower_id = ? AND following_id = ?",
        (follower_id, following_id)
    ).fetchone()
    if existing:
        return {"status": "already_following"}

    # 检查目标是否锁定
    target = conn.execute(
        "SELECT id, locked FROM users WHERE id = ?", (following_id,)
    ).fetchone()
    if not target:
        raise ValueError("目标用户不存在")
    target = dict(target)

    if target["locked"]:
        # 创建关注请求
        try:
            conn.execute(
                "INSERT INTO follow_requests (account_id, target_id, created_at) VALUES (?, ?, ?)",
                (follower_id, following_id, now)
            )
        except sqlite3.IntegrityError:
            return {"status": "already_requested"}

        _create_notification(conn, following_id, "follow_request",
                             from_user_id=follower_id, created_at=now)
        return {"status": "requested"}

    # 直接关注
    conn.execute(
        "INSERT INTO follows (follower_id, following_id, show_reblogs, notify, languages, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (follower_id, following_id, int(show_reblogs), int(notify),
         _json(languages or []), now)
    )

    # 更新计数器
    _incr(conn, "users", "following_count", follower_id)
    _incr(conn, "users", "followers_count", following_id)

    _create_notification(conn, following_id, "follow",
                         from_user_id=follower_id, created_at=now)
    return {"status": "followed"}


@transactional
def unfollow(follower_id: int, following_id: int) -> dict:
    """取关一个用户"""
    conn = get_conn()

    deleted = conn.execute(
        "DELETE FROM follows WHERE follower_id = ? AND following_id = ?",
        (follower_id, following_id)
    ).rowcount

    if deleted:
        _decr(conn, "users", "following_count", follower_id)
        _decr(conn, "users", "followers_count", following_id)

        # 移除关注请求（如果有）
        conn.execute(
            "DELETE FROM follow_requests WHERE account_id = ? AND target_id = ?",
            (follower_id, following_id)
        )
        return {"status": "unfollowed"}
    return {"status": "not_following"}


# ---------------------------------------------------------------------------
# 关注请求处理（被关注方操作）
# ---------------------------------------------------------------------------

@transactional
def accept_follow_request(request_id: int, user_id: int) -> dict:
    """批准关注请求"""
    conn = get_conn()
    now = _now()

    req = conn.execute(
        "SELECT * FROM follow_requests WHERE id = ? AND target_id = ?",
        (request_id, user_id)
    ).fetchone()
    if not req:
        raise ValueError("关注请求不存在或你不是被请求者")
    req = dict(req)

    # 删除请求，创建关注关系
    conn.execute("DELETE FROM follow_requests WHERE id = ?", (request_id,))

    try:
        conn.execute(
            "INSERT INTO follows (follower_id, following_id, created_at) VALUES (?, ?, ?)",
            (req["account_id"], req["target_id"], now)
        )
    except sqlite3.IntegrityError:
        return {"status": "already_following"}

    _incr(conn, "users", "following_count", req["account_id"])
    _incr(conn, "users", "followers_count", req["target_id"])

    # 通知请求者被接受
    _create_notification(conn, req["account_id"], "follow",
                         from_user_id=user_id, created_at=now)
    return {"status": "accepted"}


@transactional
def reject_follow_request(request_id: int, user_id: int) -> dict:
    """拒绝关注请求"""
    conn = get_conn()
    deleted = conn.execute(
        "DELETE FROM follow_requests WHERE id = ? AND target_id = ?",
        (request_id, user_id)
    ).rowcount
    return {"status": "rejected" if deleted else "not_found"}


# ---------------------------------------------------------------------------
# 查询接口
# ---------------------------------------------------------------------------

def get_followers(user_id: int, limit: int = 40, offset: int = 0) -> list[dict]:
    """获取粉丝列表"""
    conn = get_conn()
    rows = conn.execute("""
        SELECT u.id, u.username, u.display_name, u.acct, u.avatar, u.note,
               f.created_at AS followed_at
        FROM follows f
        JOIN users u ON u.id = f.follower_id
        WHERE f.following_id = ?
        ORDER BY f.created_at DESC
        LIMIT ? OFFSET ?
    """, (user_id, limit, offset)).fetchall()
    return [dict(r) for r in rows]


def get_following(user_id: int, limit: int = 40, offset: int = 0) -> list[dict]:
    """获取关注列表"""
    conn = get_conn()
    rows = conn.execute("""
        SELECT u.id, u.username, u.display_name, u.acct, u.avatar, u.note,
               f.created_at AS followed_at, f.show_reblogs, f.notify
        FROM follows f
        JOIN users u ON u.id = f.following_id
        WHERE f.follower_id = ?
        ORDER BY f.created_at DESC
        LIMIT ? OFFSET ?
    """, (user_id, limit, offset)).fetchall()
    return [dict(r) for r in rows]


def is_following(follower_id: int, following_id: int) -> bool:
    """检查是否已关注"""
    conn = get_conn()
    return conn.execute(
        "SELECT 1 FROM follows WHERE follower_id = ? AND following_id = ?",
        (follower_id, following_id)
    ).fetchone() is not None


def get_follow_requests(user_id: int, limit: int = 40, offset: int = 0) -> list[dict]:
    """获取待处理的关注请求"""
    conn = get_conn()
    rows = conn.execute("""
        SELECT fr.id, fr.account_id, fr.created_at,
               u.username, u.display_name, u.acct, u.avatar, u.note
        FROM follow_requests fr
        JOIN users u ON u.id = fr.account_id
        WHERE fr.target_id = ?
        ORDER BY fr.created_at DESC
        LIMIT ? OFFSET ?
    """, (user_id, limit, offset)).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# 屏蔽
# ---------------------------------------------------------------------------

@transactional
def block(user_id: int, blocked_id: int) -> dict:
    """
    屏蔽一个用户。
    副作用：自动取关（双向），移除粉丝关系，删除关注请求。
    """
    conn = get_conn()

    if user_id == blocked_id:
        raise ValueError("不能屏蔽自己")

    # 检查是否被封禁
    if is_banned(user_id):
        return {"status": "banned", "message": "你已被封禁，无法屏蔽"}

    try:
        conn.execute(
            "INSERT INTO blocks (user_id, blocked_id, created_at) VALUES (?, ?, ?)",
            (user_id, blocked_id, _now())
        )
    except sqlite3.IntegrityError:
        return {"status": "already_blocked"}

    # 自动双向取关
    for (a, b) in [(user_id, blocked_id), (blocked_id, user_id)]:
        deleted = conn.execute(
            "DELETE FROM follows WHERE follower_id = ? AND following_id = ?", (a, b)
        ).rowcount
        if deleted:
            _decr(conn, "users", "following_count", a)
            _decr(conn, "users", "followers_count", b)

    # 删除关注请求
    conn.execute(
        "DELETE FROM follow_requests WHERE "
        "(account_id = ? AND target_id = ?) OR (account_id = ? AND target_id = ?)",
        (user_id, blocked_id, blocked_id, user_id)
    )
    return {"status": "blocked"}


@transactional
def unblock(user_id: int, blocked_id: int) -> dict:
    """取消屏蔽"""
    conn = get_conn()
    deleted = conn.execute(
        "DELETE FROM blocks WHERE user_id = ? AND blocked_id = ?",
        (user_id, blocked_id)
    ).rowcount
    return {"status": "unblocked" if deleted else "not_blocked"}


def get_blocked(user_id: int, limit: int = 40, offset: int = 0) -> list[dict]:
    """获取屏蔽列表"""
    conn = get_conn()
    rows = conn.execute("""
        SELECT u.id, u.username, u.display_name, u.acct, u.avatar,
               b.created_at AS blocked_at
        FROM blocks b
        JOIN users u ON u.id = b.blocked_id
        WHERE b.user_id = ?
        ORDER BY b.created_at DESC
        LIMIT ? OFFSET ?
    """, (user_id, limit, offset)).fetchall()
    return [dict(r) for r in rows]


def is_blocked(user_id: int, target_id: int) -> bool:
    """检查是否已屏蔽"""
    conn = get_conn()
    return conn.execute(
        "SELECT 1 FROM blocks WHERE user_id = ? AND blocked_id = ?",
        (user_id, target_id)
    ).fetchone() is not None


# ---------------------------------------------------------------------------
# 静音
# ---------------------------------------------------------------------------

@transactional
def mute(user_id: int, muted_id: int, mute_notifications: bool = True,
         expire_at: str | None = None) -> dict:
    """
    静音一个用户。
    - mute_notifications: 是否同时静音通知
    - expire_at: ISO 格式过期时间，None 表示永久
    """
    conn = get_conn()

    if user_id == muted_id:
        raise ValueError("不能静音自己")

    try:
        conn.execute(
            "INSERT INTO mutes (user_id, muted_id, mute_notifications, expire_at, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (user_id, muted_id, int(mute_notifications), expire_at, _now())
        )
    except sqlite3.IntegrityError:
        return {"status": "already_muted"}
    return {"status": "muted"}


@transactional
def unmute(user_id: int, muted_id: int) -> dict:
    """取消静音"""
    conn = get_conn()
    deleted = conn.execute(
        "DELETE FROM mutes WHERE user_id = ? AND muted_id = ?",
        (user_id, muted_id)
    ).rowcount
    return {"status": "unmuted" if deleted else "not_muted"}


def get_muted(user_id: int, limit: int = 40, offset: int = 0) -> list[dict]:
    """获取静音列表"""
    conn = get_conn()
    rows = conn.execute("""
        SELECT u.id, u.username, u.display_name, u.acct, u.avatar,
               m.mute_notifications, m.expire_at, m.created_at AS muted_at
        FROM mutes m
        JOIN users u ON u.id = m.muted_id
        WHERE m.user_id = ?
        ORDER BY m.created_at DESC
        LIMIT ? OFFSET ?
    """, (user_id, limit, offset)).fetchall()
    return [dict(r) for r in rows]


def is_muted(user_id: int, target_id: int) -> bool:
    """检查是否已静音"""
    conn = get_conn()
    return conn.execute(
        "SELECT 1 FROM mutes WHERE user_id = ? AND muted_id = ?",
        (user_id, target_id)
    ).fetchone() is not None


# ---------------------------------------------------------------------------
# 域名屏蔽
# ---------------------------------------------------------------------------

@transactional
def domain_block(user_id: int, domain: str) -> dict:
    """屏蔽整个域名"""
    conn = get_conn()
    try:
        conn.execute(
            "INSERT INTO domain_blocks (user_id, domain, created_at) VALUES (?, ?, ?)",
            (user_id, domain, _now())
        )
    except sqlite3.IntegrityError:
        return {"status": "already_blocked"}
    return {"status": "domain_blocked"}


@transactional
def domain_unblock(user_id: int, domain: str) -> dict:
    """取消域名屏蔽"""
    conn = get_conn()
    deleted = conn.execute(
        "DELETE FROM domain_blocks WHERE user_id = ? AND domain = ?",
        (user_id, domain)
    ).rowcount
    return {"status": "unblocked" if deleted else "not_blocked"}


def get_domain_blocks(user_id: int) -> list[dict]:
    """获取域名屏蔽列表"""
    conn = get_conn()
    rows = conn.execute(
        "SELECT domain, created_at FROM domain_blocks WHERE user_id = ? ORDER BY created_at DESC",
        (user_id,)
    ).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# 好友判断（互相关注即为好友）
# ---------------------------------------------------------------------------

def are_friends(user_a_id: int, user_b_id: int) -> bool:
    """判断两个用户是否为好友（互相关注）。粉丝和关注都属于好友。"""
    if user_a_id == user_b_id:
        return True
    conn = get_conn()
    a_follows_b = conn.execute(
        "SELECT 1 FROM follows WHERE follower_id = ? AND following_id = ?",
        (user_a_id, user_b_id)
    ).fetchone() is not None
    b_follows_a = conn.execute(
        "SELECT 1 FROM follows WHERE follower_id = ? AND following_id = ?",
        (user_b_id, user_a_id)
    ).fetchone() is not None
    return a_follows_b and b_follows_a


def get_friends(user_id: int, limit: int = 100, offset: int = 0) -> list[dict]:
    """获取好友列表（互相关注的用户，粉丝和关注都属于好友）"""
    conn = get_conn()
    rows = conn.execute("""
        SELECT u.id, u.username, u.display_name, u.acct, u.avatar, u.note
        FROM follows f
        JOIN users u ON u.id = f.following_id
        WHERE f.follower_id = ?
          AND EXISTS (
              SELECT 1 FROM follows f2
              WHERE f2.follower_id = f.following_id AND f2.following_id = ?
          )
        ORDER BY u.username
        LIMIT ? OFFSET ?
    """, (user_id, user_id, limit, offset)).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# 帖子可见性规则（谁可以看 / 谁不可以看）
# ---------------------------------------------------------------------------

_visibility_table_created = False


def _ensure_visibility_table():
    """创建 post_visibility_rules 表（如不存在）"""
    global _visibility_table_created
    if _visibility_table_created:
        return
    conn = get_conn()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS post_visibility_rules (
            post_id INTEGER PRIMARY KEY,
            visible_to TEXT,
            invisible_to TEXT
        )
    """)
    _visibility_table_created = True


@transactional
def set_post_visibility(post_id: int, user_id: int,
                        visible_to: list[int] | None = None,
                        invisible_to: list[int] | None = None) -> dict:
    """
    设置帖子的"谁可以看"（白名单）和"谁不可以看"（黑名单）。
    仅内容创作者和管理员可操作。
    - visible_to: 仅这些用户可以看到（白名单）
    - invisible_to: 这些用户不能看到（黑名单，优先级最高）
    """
    from social.models import check_permission
    if not check_permission(user_id, "set_post_visibility"):
        return {"status": "forbidden", "message": "仅内容创作者和管理员可设置内容可见范围"}
    import json
    _ensure_visibility_table()
    conn = get_conn()
    conn.execute(
        "INSERT OR REPLACE INTO post_visibility_rules (post_id, visible_to, invisible_to) "
        "VALUES (?, ?, ?)",
        (post_id,
         json.dumps(visible_to or [], ensure_ascii=False),
         json.dumps(invisible_to or [], ensure_ascii=False))
    )
    return {"status": "updated"}


def check_post_visibility(post_id: int, viewer_id: int | None) -> tuple[bool, str]:
    """
    检查帖子对指定用户是否可见，返回 (is_visible, reason)。

    规则优先级（从高到低）：
      1. 作者始终可见
      2. invisible_to 黑名单（谁不可以看）
      3. direct:  仅作者和 @提及者可见
      4. private: 仅作者、@提及者、visible_to 白名单可见
      5. friends_only: 仅好友（互相关注）和 visible_to 白名单可见
      6. visible_to 白名单（谁可以看）
      7. public / unlisted: 公开可见
    """
    conn = get_conn()
    import json

    # 确保 post_visibility_rules 表存在
    _ensure_visibility_table()

    post = conn.execute(
        "SELECT author_id, visibility FROM posts WHERE id = ?", (post_id,)
    ).fetchone()
    if not post:
        return (False, "not_found")

    author_id = post["author_id"]
    visibility = post["visibility"]

    # 作者始终可见
    if viewer_id == author_id:
        return (True, "author")

    # 获取可见性规则
    rules = conn.execute(
        "SELECT visible_to, invisible_to FROM post_visibility_rules WHERE post_id = ?",
        (post_id,)
    ).fetchone()

    invisible_to = []
    visible_to = []
    if rules:
        try:
            invisible_to = json.loads(rules["invisible_to"] or "[]")
            visible_to = json.loads(rules["visible_to"] or "[]")
        except (json.JSONDecodeError, TypeError):
            pass

    # 黑名单（谁不可以看）最高优先级
    if viewer_id is not None and viewer_id in invisible_to:
        return (False, "invisible_to")

    # 未登录用户只能看 public / unlisted
    if viewer_id is None:
        if visibility in ("public", "unlisted"):
            return (True, "public")
        return (False, "login_required")

    # direct: 仅作者和 @提及者
    if visibility == "direct":
        mentioned = conn.execute(
            "SELECT 1 FROM post_mentions WHERE post_id = ? AND mentioned_user_id = ?",
            (post_id, viewer_id)
        ).fetchone()
        return (True, "direct_mentioned") if mentioned else (False, "direct")

    # private: 仅作者、@提及者、visible_to 白名单
    if visibility == "private":
        mentioned = conn.execute(
            "SELECT 1 FROM post_mentions WHERE post_id = ? AND mentioned_user_id = ?",
            (post_id, viewer_id)
        ).fetchone()
        if mentioned:
            return (True, "private_mentioned")
        if viewer_id in visible_to:
            return (True, "visible_to")
        return (False, "private")

    # friends_only: 好友（互相关注）+ visible_to 白名单
    if visibility == "friends_only":
        if viewer_id in visible_to:
            return (True, "visible_to")
        if are_friends(viewer_id, author_id):
            return (True, "friend")
        return (False, "not_friend")

    # public / unlisted: visible_to 白名单（仅对登录用户生效）
    if visible_to and viewer_id not in visible_to:
        return (False, "not_in_visible_to")

    return (True, "public")


# ---------------------------------------------------------------------------
# 好友分组
# ---------------------------------------------------------------------------

_friend_group_tables_created = False


def _ensure_friend_group_tables():
    global _friend_group_tables_created
    if _friend_group_tables_created:
        return
    conn = get_conn()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS friend_groups (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            created_at TEXT NOT NULL,
            UNIQUE(user_id, name)
        );
        CREATE TABLE IF NOT EXISTS friend_group_members (
            group_id INTEGER NOT NULL,
            friend_id INTEGER NOT NULL,
            added_at TEXT NOT NULL,
            PRIMARY KEY(group_id, friend_id)
        );
    """)
    _friend_group_tables_created = True


@transactional
def create_friend_group(user_id: int, name: str) -> dict:
    """创建好友分组"""
    _ensure_friend_group_tables()
    conn = get_conn()
    try:
        cursor = conn.execute(
            "INSERT INTO friend_groups (user_id, name, created_at) VALUES (?, ?, ?)",
            (user_id, name, _now())
        )
        return {"status": "created", "id": cursor.lastrowid}
    except sqlite3.IntegrityError:
        return {"status": "duplicate", "message": "分组名已存在"}


@transactional
def delete_friend_group(group_id: int, user_id: int) -> dict:
    """删除好友分组"""
    _ensure_friend_group_tables()
    conn = get_conn()
    deleted = conn.execute(
        "DELETE FROM friend_groups WHERE id = ? AND user_id = ?",
        (group_id, user_id)
    ).rowcount
    if deleted:
        conn.execute("DELETE FROM friend_group_members WHERE group_id = ?", (group_id,))
        return {"status": "deleted"}
    return {"status": "not_found"}


@transactional
def add_to_friend_group(group_id: int, user_id: int, friend_id: int) -> dict:
    """将好友添加到分组"""
    _ensure_friend_group_tables()
    conn = get_conn()
    # 验证分组属于该用户
    grp = conn.execute(
        "SELECT id FROM friend_groups WHERE id = ? AND user_id = ?",
        (group_id, user_id)
    ).fetchone()
    if not grp:
        raise ValueError("分组不存在或不属于你")
    try:
        conn.execute(
            "INSERT INTO friend_group_members (group_id, friend_id, added_at) VALUES (?, ?, ?)",
            (group_id, friend_id, _now())
        )
    except sqlite3.IntegrityError:
        return {"status": "already_in_group"}
    return {"status": "added"}


@transactional
def remove_from_friend_group(group_id: int, user_id: int, friend_id: int) -> dict:
    """从分组中移除好友"""
    _ensure_friend_group_tables()
    conn = get_conn()
    grp = conn.execute(
        "SELECT id FROM friend_groups WHERE id = ? AND user_id = ?",
        (group_id, user_id)
    ).fetchone()
    if not grp:
        raise ValueError("分组不存在或不属于你")
    deleted = conn.execute(
        "DELETE FROM friend_group_members WHERE group_id = ? AND friend_id = ?",
        (group_id, friend_id)
    ).rowcount
    return {"status": "removed" if deleted else "not_in_group"}


def get_friend_groups(user_id: int) -> list[dict]:
    """获取用户的所有好友分组及成员"""
    _ensure_friend_group_tables()
    conn = get_conn()
    groups = conn.execute(
        "SELECT id, name, created_at FROM friend_groups WHERE user_id = ? ORDER BY created_at",
        (user_id,)
    ).fetchall()
    result = []
    for g in groups:
        g = dict(g)
        members = conn.execute("""
            SELECT u.id, u.username, u.display_name, u.acct, u.avatar, fgm.added_at
            FROM friend_group_members fgm
            JOIN users u ON u.id = fgm.friend_id
            WHERE fgm.group_id = ?
        """, (g["id"],)).fetchall()
        g["members"] = [dict(m) for m in members]
        g["member_count"] = len(members)
        result.append(g)
    return result


# ---------------------------------------------------------------------------
# 推荐系统（共同好友 + 兴趣标签）
# ---------------------------------------------------------------------------

_reaction_tables_created = False


def _ensure_reaction_tables():
    global _reaction_tables_created
    if _reaction_tables_created:
        return
    conn = get_conn()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS user_tag_interests (
            user_id INTEGER NOT NULL,
            tag_name TEXT NOT NULL,
            weight REAL DEFAULT 1.0,
            updated_at TEXT NOT NULL,
            PRIMARY KEY(user_id, tag_name)
        );
        CREATE TABLE IF NOT EXISTS post_reactions (
            user_id INTEGER NOT NULL,
            post_id INTEGER NOT NULL,
            reaction_type TEXT NOT NULL DEFAULT 'not_interested',
            created_at TEXT NOT NULL,
            PRIMARY KEY(user_id, post_id, reaction_type)
        );
    """)
    _reaction_tables_created = True


def recommend_users(user_id: int, limit: int = 20) -> list[dict]:
    """
    基于共同好友 + 兴趣标签推荐用户。

    算法：
      1. 找到"好友的好友"（二阶邻居）
      2. 按共同好友数量排序
      3. 叠加兴趣标签重合度
      4. 排除：自己、已是好友、已屏蔽/被屏蔽
    """
    conn = get_conn()
    _ensure_reaction_tables()

    # 排除列表：自己 + 已关注 + 已屏蔽 + 被屏蔽
    exclude_sql = """
        SELECT u2.id FROM users u2 WHERE u2.id = ?
        UNION
        SELECT f.following_id FROM follows f WHERE f.follower_id = ?
        UNION
        SELECT b.blocked_id FROM blocks b WHERE b.user_id = ?
        UNION
        SELECT b2.user_id FROM blocks b2 WHERE b2.blocked_id = ?
    """
    rows = conn.execute(f"""
        SELECT candidate.id, candidate.username, candidate.display_name,
               candidate.acct, candidate.avatar, candidate.note,
               candidate.followers_count, candidate.following_count,
               COUNT(DISTINCT mf.follower_id) AS mutual_friend_count
        FROM follows f
        JOIN follows mf ON mf.following_id = f.following_id
        JOIN users candidate ON candidate.id = mf.follower_id
        WHERE f.follower_id = ?
          AND candidate.id NOT IN ({exclude_sql})
        GROUP BY candidate.id
        ORDER BY mutual_friend_count DESC, candidate.followers_count DESC
        LIMIT ?
    """, [user_id, user_id, user_id, user_id] + [user_id] + [limit]).fetchall()

    result = []
    for r in rows:
        r = dict(r)
        # 计算兴趣标签重合度
        my_tags = set(t["tag_name"] for t in conn.execute(
            "SELECT tag_name FROM user_tag_interests WHERE user_id = ? AND weight > 0.5",
            (user_id,)
        ).fetchall())
        their_tags = set(t["tag_name"] for t in conn.execute(
            "SELECT tag_name FROM user_tag_interests WHERE user_id = ? AND weight > 0.5",
            (r["id"],)
        ).fetchall())
        r["tag_overlap"] = len(my_tags & their_tags)
        r["recommend_score"] = r["mutual_friend_count"] * 10 + r["tag_overlap"] * 5
        result.append(r)

    result.sort(key=lambda x: x["recommend_score"], reverse=True)
    return result


def recommend_posts(user_id: int, limit: int = 20, offset: int = 0) -> list[dict]:
    """
    基于兴趣标签推荐动态。

    算法：
      1. 获取用户兴趣标签及权重
      2. 找到含这些标签的帖子
      3. 排除：作者已被屏蔽、已标记不感兴趣
      4. 不感兴趣的标签权重降低
      5. 按匹配权重排序，考虑可见性
    """
    conn = get_conn()
    _ensure_reaction_tables()

    # 获取用户兴趣标签
    tag_rows = conn.execute(
        "SELECT tag_name, weight FROM user_tag_interests WHERE user_id = ? AND weight > 0.3",
        (user_id,)
    ).fetchall()
    if not tag_rows:
        # 无兴趣标签时，从帖子标签中推导
        tag_rows = conn.execute("""
            SELECT t.name AS tag_name, 1.0 AS weight
            FROM posts p
            JOIN post_tags pt ON pt.post_id = p.id
            JOIN tags t ON t.id = pt.tag_id
            WHERE p.author_id = ?
            GROUP BY t.name
            LIMIT 20
        """, (user_id,)).fetchall()

    if not tag_rows:
        return []

    # 构建标签权重映射
    tag_weights = {row["tag_name"]: row["weight"] for row in tag_rows}

    # 排除列表
    blocked_ids = [
        r[0] for r in conn.execute(
            "SELECT blocked_id FROM blocks WHERE user_id = ?", (user_id,)
        )
    ]
    not_interested_ids = [
        r[0] for r in conn.execute(
            "SELECT post_id FROM post_reactions WHERE user_id = ? AND reaction_type = 'not_interested'",
            (user_id,)
        )
    ]

    # 不感兴趣标签（权重减半）
    disliked_tags = set()
    for pid in not_interested_ids:
        disliked_tags.update(
            r["name"] for r in conn.execute(
                "SELECT t.name FROM post_tags pt JOIN tags t ON t.id = pt.tag_id WHERE pt.post_id = ?",
                (pid,)
            ).fetchall()
        )
    for tag in disliked_tags:
        if tag in tag_weights:
            tag_weights[tag] *= 0.3

    # 搜索匹配标签的帖子
    tag_names = list(tag_weights.keys())
    placeholders = ",".join(["?"] * len(tag_names))
    rows = conn.execute(f"""
        SELECT p.id, p.content, p.visibility, p.author_id,
               p.favourites_count, p.reblogs_count, p.created_at,
               u.username, u.display_name, u.acct, u.avatar,
               GROUP_CONCAT(t.name) AS matched_tags
        FROM post_tags pt
        JOIN posts p ON p.id = pt.post_id
        JOIN tags t ON t.id = pt.tag_id
        JOIN users u ON u.id = p.author_id
        WHERE t.name IN ({placeholders})
          AND p.visibility IN ('public', 'unlisted', 'friends_only')
        GROUP BY p.id
        ORDER BY p.created_at DESC
        LIMIT ? OFFSET ?
    """, tag_names + [limit * 3, offset]).fetchall()

    # 过滤 + 计算得分
    scored = []
    from social.social import check_post_visibility
    for r in rows:
        r = dict(r)

        # 排除屏蔽作者
        if r["author_id"] in blocked_ids:
            continue
        # 排除已标记不感兴趣
        if r["id"] in not_interested_ids:
            continue
        # 可见性检查
        visible, _ = check_post_visibility(r["id"], user_id)
        if not visible:
            continue

        tags = (r.get("matched_tags") or "").split(",")
        r["score"] = sum(tag_weights.get(t, 0.3) for t in tags)
        r["matched_tags"] = tags
        scored.append(r)

    scored.sort(key=lambda x: x["score"], reverse=True)
    return [dict(r) for r in scored[:limit]]


# ---------------------------------------------------------------------------
# 不感兴趣
# ---------------------------------------------------------------------------

@transactional
def mark_not_interested(user_id: int, post_id: int) -> dict:
    """
    标记动态为"不感兴趣"。
    后续推荐会降低含有相同标签动态的推送概率。
    """
    conn = get_conn()
    _ensure_reaction_tables()
    now = _now()

    try:
        conn.execute(
            "INSERT INTO post_reactions (user_id, post_id, reaction_type, created_at) "
            "VALUES (?, ?, 'not_interested', ?)",
            (user_id, post_id, now)
        )
    except sqlite3.IntegrityError:
        return {"status": "already_marked"}

    # 获取帖子标签并降低权重
    tags = conn.execute("""
        SELECT t.name FROM post_tags pt
        JOIN tags t ON t.id = pt.tag_id
        WHERE pt.post_id = ?
    """, (post_id,)).fetchall()

    for tag in tags:
        tag_name = tag["name"]
        existing = conn.execute(
            "SELECT weight FROM user_tag_interests WHERE user_id = ? AND tag_name = ?",
            (user_id, tag_name)
        ).fetchone()
        if existing:
            new_weight = max(0.0, existing["weight"] - 0.3)
            conn.execute(
                "UPDATE user_tag_interests SET weight = ?, updated_at = ? "
                "WHERE user_id = ? AND tag_name = ?",
                (new_weight, now, user_id, tag_name)
            )
        else:
            conn.execute(
                "INSERT INTO user_tag_interests (user_id, tag_name, weight, updated_at) "
                "VALUES (?, ?, ?, ?)",
                (user_id, tag_name, 0.3, now)
            )

    return {"status": "marked", "affected_tags": len(tags)}


def get_not_interested_posts(user_id: int, limit: int = 20, offset: int = 0) -> list[dict]:
    """获取已标记为不感兴趣的帖子列表"""
    conn = get_conn()
    _ensure_reaction_tables()
    rows = conn.execute("""
        SELECT pr.post_id, pr.created_at AS marked_at,
               p.content, p.author_id,
               u.username, u.display_name
        FROM post_reactions pr
        JOIN posts p ON p.id = pr.post_id
        JOIN users u ON u.id = p.author_id
        WHERE pr.user_id = ? AND pr.reaction_type = 'not_interested'
        ORDER BY pr.created_at DESC
        LIMIT ? OFFSET ?
    """, (user_id, limit, offset)).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# 内容审核（管理员）
# ---------------------------------------------------------------------------

_moderation_tables_created = False


def _ensure_moderation_tables():
    global _moderation_tables_created
    conn = get_conn()

    # 先建 reports 表（不含 post_id，后续迁移补上）
    conn.execute("""CREATE TABLE IF NOT EXISTS reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            reporter_id INTEGER NOT NULL,
            reason TEXT DEFAULT '',
            status TEXT DEFAULT 'pending',
            admin_id INTEGER,
            resolution TEXT DEFAULT '',
            created_at TEXT NOT NULL,
            resolved_at TEXT
        )""")

    # 迁移：为旧 reports 表补上缺失的列
    cols = [r[1] for r in conn.execute("PRAGMA table_info(reports)").fetchall()]
    _expected_report_cols = {
        "post_id": "INTEGER",
        "reason": "TEXT DEFAULT ''",
        "status": "TEXT DEFAULT 'pending'",
        "admin_id": "INTEGER",
        "resolution": "TEXT DEFAULT ''",
        "resolved_at": "TEXT",
    }
    for col_name, col_def in _expected_report_cols.items():
        if col_name not in cols:
            try:
                conn.execute(f"ALTER TABLE reports ADD COLUMN {col_name} {col_def}")
            except sqlite3.OperationalError:
                pass

    # 标志位已设置则不再建其他表（避免 executescript 在事务内提交）
    if _moderation_tables_created:
        return

    conn.execute("""CREATE TABLE IF NOT EXISTS moderation_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            admin_id INTEGER NOT NULL,
            target_id INTEGER,
            action TEXT NOT NULL,
            detail TEXT DEFAULT '',
            created_at TEXT NOT NULL
        )""")

    conn.execute("""CREATE TABLE IF NOT EXISTS bans (
            user_id INTEGER NOT NULL PRIMARY KEY,
            admin_id INTEGER NOT NULL,
            reason TEXT DEFAULT '',
            expire_at TEXT,
            created_at TEXT NOT NULL
        )""")

    _moderation_tables_created = True


@transactional
def report_content(reporter_id: int, post_id: int, reason: str = "") -> dict:
    """
    举报内容（所有用户均可）。
    一条帖子可以被多次举报，但同一用户对同一帖子不能重复举报。
    """
    _ensure_moderation_tables()
    conn = get_conn()
    now = _now()

    post = conn.execute("SELECT id, author_id FROM posts WHERE id = ?", (post_id,)).fetchone()
    if not post:
        raise ValueError("帖子不存在")

    existing = conn.execute(
        "SELECT id FROM reports WHERE reporter_id = ? AND post_id = ? AND status = 'pending'",
        (reporter_id, post_id)
    ).fetchone()
    if existing:
        return {"status": "already_reported", "message": "你已举报过该内容"}

    cursor = conn.execute(
        "INSERT INTO reports (reporter_id, post_id, reason, status, created_at) "
        "VALUES (?, ?, ?, 'pending', ?)",
        (reporter_id, post_id, reason.strip(), now)
    )

    # 通知管理员
    admin_ids = conn.execute(
        "SELECT id FROM users WHERE role = 'admin'"
    ).fetchall()
    for a in admin_ids:
        _create_notification(conn, a["id"], "admin.report",
                             from_user_id=reporter_id, post_id=post_id, created_at=now)

    return {"status": "reported", "report_id": cursor.lastrowid}


def get_reports(admin_id: int,
                status: str | None = None,
                limit: int = 40,
                offset: int = 0) -> list[dict]:
    """
    获取举报列表（仅管理员）。
    status: 'pending' | 'resolved' | 'ignored'，None 表示全部。
    """
    from social.models import check_permission
    if not check_permission(admin_id, "moderate_content"):
        raise ValueError("仅管理员可查看举报列表")

    _ensure_moderation_tables()
    conn = get_conn()

    conditions = ["1=1"]
    params: list = []

    if status:
        conditions.append("r.status = ?")
        params.append(status)

    where = " AND ".join(conditions)

    rows = conn.execute(f"""
        SELECT r.id, r.reporter_id, r.post_id, r.reason, r.status,
               r.admin_id, r.resolution, r.created_at, r.resolved_at,
               ru.username AS reporter_username, ru.display_name AS reporter_display,
               p.content AS post_content, p.author_id AS post_author_id,
               pu.username AS post_author_username, pu.display_name AS post_author_display,
               au.username AS admin_username
        FROM reports r
        JOIN users ru ON ru.id = r.reporter_id
        LEFT JOIN posts p ON p.id = r.post_id
        LEFT JOIN users pu ON pu.id = p.author_id
        LEFT JOIN users au ON au.id = r.admin_id
        WHERE {where}
        ORDER BY CASE WHEN r.status = 'pending' THEN 0 ELSE 1 END, r.created_at DESC
        LIMIT ? OFFSET ?
    """, params + [limit, offset]).fetchall()

    return [dict(r) for r in rows]


@transactional
def review_report(report_id: int, admin_id: int,
                  action: str,  # 'resolve' | 'ignore' | 'delete_post' | 'ban_user'
                  note: str = "") -> dict:
    """
    审核举报（仅管理员）。
    action:
      - resolve:  标记为已处理，不做惩罚
      - ignore:   忽略举报
      - delete_post: 删除被举报帖子
      - ban_user: 封禁被举报帖子的作者
    """
    from social.models import check_permission
    if not check_permission(admin_id, "moderate_content"):
        return {"status": "forbidden", "message": "仅管理员可审核举报"}

    _ensure_moderation_tables()
    conn = get_conn()
    now = _now()

    report = conn.execute(
        "SELECT * FROM reports WHERE id = ?", (report_id,)
    ).fetchone()
    if not report:
        raise ValueError("举报不存在")
    report = dict(report)

    if report["status"] != "pending":
        return {"status": "already_reviewed", "message": "该举报已被处理"}

    # 更新举报状态
    conn.execute(
        "UPDATE reports SET status = ?, admin_id = ?, resolution = ?, resolved_at = ? WHERE id = ?",
        (action, admin_id, note, now, report_id)
    )

    # 记录审核日志
    conn.execute(
        "INSERT INTO moderation_logs (admin_id, target_id, action, detail, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (admin_id, report.get("post_id"), action, note, now)
    )

    # 执行具体动作
    if action == "delete_post" and report.get("post_id"):
        conn.execute("DELETE FROM posts WHERE id = ?", (report["post_id"],))
        conn.execute("INSERT INTO posts_fts(posts_fts, rowid, content, spoiler_text) VALUES ('delete', ?, '', '')",
                     (report["post_id"],))

    if action == "ban_user" and report.get("post_id"):
        post = conn.execute(
            "SELECT author_id FROM posts WHERE id = ?", (report["post_id"],)
        ).fetchone()
        if post:
            _apply_ban(conn, post["author_id"], admin_id,
                       f"内容违规举报 #{report_id}", None, now)

    return {"status": "reviewed", "action": action}


@transactional
def ban_user(admin_id: int, target_id: int,
             reason: str = "", expire_at: str | None = None) -> dict:
    """
    封禁用户（仅管理员）。
    封禁后用户无法登录、发帖、关注、发私信。
    """
    from social.models import check_permission
    if not check_permission(admin_id, "moderate_content"):
        return {"status": "forbidden", "message": "仅管理员可封禁用户"}

    _ensure_moderation_tables()
    conn = get_conn()
    now = _now()

    if admin_id == target_id:
        raise ValueError("不能封禁自己")

    target = conn.execute("SELECT id FROM users WHERE id = ?", (target_id,)).fetchone()
    if not target:
        raise ValueError("目标用户不存在")

    _apply_ban(conn, target_id, admin_id, reason, expire_at, now)

    conn.execute(
        "INSERT INTO moderation_logs (admin_id, target_id, action, detail, created_at) "
        "VALUES (?, ?, 'ban_user', ?, ?)",
        (admin_id, target_id, reason, now)
    )

    return {"status": "banned"}


def _apply_ban(conn, target_id: int, admin_id: int,
               reason: str, expire_at: str | None, now: str):
    """内部：执行封禁。设置 limited=1，插入 bans 表。"""
    conn.execute(
        "INSERT OR REPLACE INTO bans (user_id, admin_id, reason, expire_at, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (target_id, admin_id, reason, expire_at, now)
    )
    conn.execute("UPDATE users SET limited = 1 WHERE id = ?", (target_id,))


@transactional
def unban_user(admin_id: int, target_id: int) -> dict:
    """
    解封用户（仅管理员）。
    """
    from social.models import check_permission
    if not check_permission(admin_id, "moderate_content"):
        return {"status": "forbidden", "message": "仅管理员可解封用户"}

    _ensure_moderation_tables()
    conn = get_conn()
    now = _now()

    deleted = conn.execute(
        "DELETE FROM bans WHERE user_id = ?", (target_id,)
    ).rowcount
    conn.execute("UPDATE users SET limited = 0 WHERE id = ?", (target_id,))
    conn.execute(
        "INSERT INTO moderation_logs (admin_id, target_id, action, detail, created_at) "
        "VALUES (?, ?, 'unban_user', '', ?)",
        (admin_id, target_id, now)
    )
    return {"status": "unbanned" if deleted else "not_banned"}


def is_banned(user_id: int) -> bool:
    """检查用户是否被封禁（含过期检查）"""
    _ensure_moderation_tables()
    conn = get_conn()
    row = conn.execute(
        "SELECT expire_at FROM bans WHERE user_id = ?", (user_id,)
    ).fetchone()
    if not row:
        return False
    if row["expire_at"] and row["expire_at"] < _now():
        # 过期自动解封
        conn.execute("DELETE FROM bans WHERE user_id = ?", (user_id,))
        conn.execute("UPDATE users SET limited = 0 WHERE id = ?", (user_id,))
        conn.commit()
        return False
    return True


def get_banned_users(admin_id: int, limit: int = 40, offset: int = 0) -> list[dict]:
    """获取封禁用户列表（仅管理员）"""
    from social.models import check_permission
    if not check_permission(admin_id, "moderate_content"):
        raise ValueError("仅管理员可查看封禁列表")

    _ensure_moderation_tables()
    conn = get_conn()
    rows = conn.execute("""
        SELECT b.user_id, b.reason, b.expire_at, b.created_at AS banned_at,
               u.username, u.display_name, u.acct, u.avatar,
               au.username AS admin_username
        FROM bans b
        JOIN users u ON u.id = b.user_id
        JOIN users au ON au.id = b.admin_id
        ORDER BY b.created_at DESC
        LIMIT ? OFFSET ?
    """, (limit, offset)).fetchall()
    return [dict(r) for r in rows]


def get_moderation_log(admin_id: int, limit: int = 40, offset: int = 0) -> list[dict]:
    """获取审核日志（仅管理员）"""
    from social.models import check_permission
    if not check_permission(admin_id, "moderate_content"):
        raise ValueError("仅管理员可查看审核日志")

    _ensure_moderation_tables()
    conn = get_conn()
    rows = conn.execute("""
        SELECT ml.id, ml.admin_id, ml.target_id, ml.action, ml.detail, ml.created_at,
               au.username AS admin_username, au.display_name AS admin_display,
               tu.username AS target_username
        FROM moderation_logs ml
        JOIN users au ON au.id = ml.admin_id
        LEFT JOIN users tu ON tu.id = ml.target_id
        ORDER BY ml.created_at DESC
        LIMIT ? OFFSET ?
    """, (limit, offset)).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# 内部辅助
# ---------------------------------------------------------------------------

def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _json(obj) -> str:
    import json
    return json.dumps(obj, ensure_ascii=False)


def _incr(conn, table: str, column: str, pk: int):
    conn.execute(
        f"UPDATE {table} SET {column} = MAX(0, {column} + 1) WHERE id = ?", (pk,)
    )


def _decr(conn, table: str, column: str, pk: int):
    conn.execute(
        f"UPDATE {table} SET {column} = MAX(0, {column} - 1) WHERE id = ?", (pk,)
    )


def _create_notification(conn, user_id: int, ntype: str,
                         from_user_id: int | None = None,
                         post_id: int | None = None,
                         created_at: str | None = None):
    """
    创建通知。
    自动跳过：被静音、被屏蔽、自己对自己的通知。
    """
    if from_user_id == user_id:
        return  # 不给自己发通知

    # 被接收者静音且设置了静音通知
    muted = conn.execute(
        "SELECT 1 FROM mutes WHERE user_id = ? AND muted_id = ? AND mute_notifications = 1",
        (user_id, from_user_id)
    ).fetchone()
    if muted:
        return

    # 被接收者屏蔽
    blocked = conn.execute(
        "SELECT 1 FROM blocks WHERE user_id = ? AND blocked_id = ?",
        (user_id, from_user_id)
    ).fetchone()
    if blocked:
        return

    conn.execute(
        "INSERT INTO notifications (user_id, notification_type, from_user_id, post_id, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (user_id, ntype, from_user_id, post_id, created_at or _now())
    )
