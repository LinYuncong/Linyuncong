"""
私信（Direct Message）模块

基于 Mastodon 风格设计：
  - 私信本质上是一条 visibility='direct' 的帖子
  - conversation 用于组织对话上下文
  - 一条私信只能 @ 一个用户
"""
import sqlite3
from datetime import datetime, timezone
from social.db import get_conn, transactional


# ---------------------------------------------------------------------------
# 会话管理
# ---------------------------------------------------------------------------

@transactional#包裹在事务内，保证原子性
def create_conversation(user_ids: list[int]) -> dict:
    """
    创建一个新会话（或返回已有的）。
    用户必须 ≥ 2 人。
    """
    if len(user_ids) < 2:
        raise ValueError("会话至少需要 2 个参与者")
    user_ids = sorted(set(user_ids))#去重排序
    conn = get_conn()

    # 查找是否已有仅包含这些人的会话
    placeholders = ",".join(["?"] * len(user_ids))
    existing = conn.execute(f"""
        SELECT ca.conversation_id
        FROM conversation_accounts ca
        WHERE ca.conversation_id IN (
            SELECT conversation_id FROM conversation_accounts
            GROUP BY conversation_id
            HAVING COUNT(*) = ?
        )
        AND ca.user_id IN ({placeholders})
        GROUP BY ca.conversation_id
        HAVING COUNT(*) = ?
    """, [len(user_ids)] + user_ids + [len(user_ids)]).fetchone()#内层看会话人数是否相等，外层看人员是否相同

    if existing:#已存在则直接返回会话id
        return {"status": "exists", "conversation_id": existing[0]}

    # 创建新会话
    now = _now()
    cursor = conn.execute(
        "INSERT INTO conversations (created_at) VALUES (?)", (now,)
    )
    conv_id = cursor.lastrowid#获取新会话的id

    for uid in user_ids:
        conn.execute(
            "INSERT INTO conversation_accounts (conversation_id, user_id) VALUES (?, ?)",
            (conv_id, uid)
        )#为每个参与者插入一条关联记录到 conversation_accounts 表

    return {"status": "created", "conversation_id": conv_id}#返回会话id


@transactional
def send_direct_message(sender_id: int,
                        conversation_id: int,
                        content: str,
                        spoiler_text: str = "",
                        sensitive: bool = False,
                        media_ids: list[int] | None = None,
                        language: str = "") -> dict:
    """
    在指定会话中发送私信。

    流程：
      1. 验证 sender 属于该会话
      2. 找到会话中的另一个用户作为 @提及目标
      3. 创建 visibility='direct' 的帖子
      4. 更新会话的 last_post_id
      5. 发送通知
    """
    conn = get_conn()
    now = _now()

    # 验证会话
    conv = conn.execute(
        "SELECT id FROM conversations WHERE id = ?", (conversation_id,)
    ).fetchone()
    if not conv:
        raise ValueError("会话不存在")

    # 验证发送者属于该会话
    member = conn.execute(
        "SELECT user_id FROM conversation_accounts "
        "WHERE conversation_id = ? AND user_id = ?",
        (conversation_id, sender_id)
    ).fetchone()
    if not member:
        raise ValueError("你不在此会话中")

    # 检查是否被封禁
    from social.social import is_banned
    if is_banned(sender_id):
        return {"status": "banned", "message": "你已被封禁，无法发送私信"}

    # 找到会话中的其他参与者（接收者）
    recipients = conn.execute(
        "SELECT user_id FROM conversation_accounts "
        "WHERE conversation_id = ? AND user_id != ?",
        (conversation_id, sender_id)
    ).fetchall()
    if not recipients:
        raise ValueError("会话中没有其他参与者")

    # 检查是否被对方屏蔽
    for r in recipients:
        blocked = conn.execute(
            "SELECT 1 FROM blocks WHERE user_id = ? AND blocked_id = ?",
            (r["user_id"], sender_id)
        ).fetchone()
        if blocked:
            return {"status": "blocked", "message": "消息无法发送：你已被对方屏蔽"}

    # 检查发送者是否屏蔽了对方
    for r in recipients:
        blocked_by_sender = conn.execute(
            "SELECT 1 FROM blocks WHERE user_id = ? AND blocked_id = ?",
            (sender_id, r["user_id"])
        ).fetchone()
        if blocked_by_sender:
            return {"status": "blocked", "message": "消息无法发送：你已屏蔽该用户"}

    # 创建私信帖子
    cursor = conn.execute("""
        INSERT INTO posts
        (content, spoiler_text, sensitive, visibility, language,
         author_id, created_at, application_name)
        VALUES (?, ?, ?, 'direct', ?, ?, ?, 'DirectMessage')
    """, (content, spoiler_text, int(sensitive), language, sender_id, now))

    post_id = cursor.lastrowid

    # 添加 @提及（目标接收者）
    for r in recipients:
        conn.execute(
            "INSERT INTO post_mentions (post_id, mentioned_user_id) VALUES (?, ?)",
            (post_id, r["user_id"])
        )

    # 关联媒体附件
    if media_ids:
        placeholders = ",".join(["?"] * len(media_ids))
        conn.execute(
            f"UPDATE media_attachments SET post_id = ? WHERE id IN ({placeholders})",
            [post_id] + media_ids
        )

    # 更新会话
    conn.execute(
        "UPDATE conversations SET last_post_id = ?, is_unread = 1 WHERE id = ?",
        (post_id, conversation_id)
    )

    # 发送通知给所有接收者
    import social.notification as nf
    for r in recipients:
        nf.create_notification(
            r["user_id"], "mention",
            from_user_id=sender_id, post_id=post_id
        )

    return {
        "status": "sent",
        "post_id": post_id,
        "conversation_id": conversation_id,
        "recipient_ids": [r["user_id"] for r in recipients],
    }


# ---------------------------------------------------------------------------
# 会话查询
# ---------------------------------------------------------------------------

def get_conversations(user_id: int,
                      limit: int = 20,
                      offset: int = 0) -> list[dict]:#只读，不必保证原子性
    """
    获取用户的会话列表。
    按最后消息时间倒序排列。
    """
    conn = get_conn()

    rows = conn.execute("""
        SELECT c.id AS conversation_id,
               c.last_post_id,
               c.is_unread,
               c.created_at AS conversation_created_at,
               lp.content AS last_content,
               lp.created_at AS last_post_at,
               lp.author_id AS last_author_id,
               au.username AS last_author_username,
               au.display_name AS last_author_display,
               au.avatar AS last_author_avatar
        FROM conversation_accounts ca
        JOIN conversations c ON c.id = ca.conversation_id
        LEFT JOIN posts lp ON lp.id = c.last_post_id
        LEFT JOIN users au ON au.id = lp.author_id
        WHERE ca.user_id = ?
        ORDER BY COALESCE(lp.created_at, c.created_at) DESC
        LIMIT ? OFFSET ?
    """, (user_id, limit, offset)).fetchall()#conversation_accounts 关联到 conversations，筛选出用户参与的所有会话
    #左连接最后一条消息，并获取最后一条消息的作者信息，按时间倒序
    result = []
    for r in rows:
        r = dict(r)

        # 获取会话中的其他参与者
        participants = conn.execute("""
            SELECT u.id, u.username, u.display_name, u.acct, u.avatar
            FROM conversation_accounts ca
            JOIN users u ON u.id = ca.user_id
            WHERE ca.conversation_id = ? AND ca.user_id != ?
        """, (r["conversation_id"], user_id)).fetchall()
        r["participants"] = [dict(p) for p in participants]

        result.append(r)

    return result


def get_messages(conversation_id: int,
                 user_id: int,
                 limit: int = 40,
                 before_id: int | None = None) -> list[dict]:
    """
    获取指定会话的消息列表
    user_id 用于验证访问权限；before_id用于分页，获取比当前id更小（即时间更早）的消息
    """
    conn = get_conn()

    # 验证用户属于该会话
    member = conn.execute(
        "SELECT 1 FROM conversation_accounts "
        "WHERE conversation_id = ? AND user_id = ?",
        (conversation_id, user_id)
    ).fetchone()
    if not member:
        raise ValueError("你无权访问此会话")

    if before_id:
        rows = conn.execute("""
            SELECT p.*, u.username, u.display_name, u.acct, u.avatar
            FROM posts p
            JOIN users u ON u.id = p.author_id
            JOIN post_mentions pm ON pm.post_id = p.id
            JOIN conversation_accounts ca ON ca.user_id = pm.mentioned_user_id
            WHERE ca.conversation_id = ?
              AND p.visibility = 'direct'
              AND p.author_id IN (
                  SELECT user_id FROM conversation_accounts
                  WHERE conversation_id = ?
              )
              AND p.id < ?
            GROUP BY p.id
            ORDER BY p.id DESC
            LIMIT ?
        """, (conversation_id, conversation_id, before_id, limit)).fetchall()#找到作者，筛选被@的帖子，被提及者是会话成员，会话是私信，作者也是会话成员
    else:
        # 找出该会话相关的所有 direct 帖子
        rows = conn.execute("""
            SELECT p.*, u.username, u.display_name, u.acct, u.avatar
            FROM posts p
            JOIN users u ON u.id = p.author_id
            JOIN post_mentions pm ON pm.post_id = p.id
            JOIN conversation_accounts ca ON ca.user_id = pm.mentioned_user_id
            WHERE ca.conversation_id = ?
              AND p.visibility = 'direct'
              AND p.author_id IN (
                  SELECT user_id FROM conversation_accounts
                  WHERE conversation_id = ?
              )
            GROUP BY p.id
            ORDER BY p.id DESC
            LIMIT ?
        """, (conversation_id, conversation_id, limit)).fetchall()

    # 反转顺序，最早的在前面（聊天风格）
    rows_list = [dict(r) for r in rows]
    rows_list.reverse()
    return rows_list


@transactional
def mark_conversation_read(conversation_id: int, user_id: int) -> dict:
    """标记会话为已读"""
    conn = get_conn()

    member = conn.execute(
        "SELECT 1 FROM conversation_accounts "
        "WHERE conversation_id = ? AND user_id = ?",
        (conversation_id, user_id)
    ).fetchone()
    if not member:
        raise ValueError("你无权访问此会话")

    conn.execute(
        "UPDATE conversations SET is_unread = 0 WHERE id = ?",
        (conversation_id,)
    )
    return {"status": "read"}


def get_unread_conversation_count(user_id: int) -> int:
    """获取未读会话数"""
    conn = get_conn()
    row = conn.execute("""
        SELECT COUNT(*) AS cnt
        FROM conversation_accounts ca
        JOIN conversations c ON c.id = ca.conversation_id
        WHERE ca.user_id = ? AND c.is_unread = 1
    """, (user_id,)).fetchone()
    return row["cnt"]#fetchone返回单行


# ---------------------------------------------------------------------------
# 辅助
# ---------------------------------------------------------------------------

def _now() -> str:#记录创建时间戳
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
