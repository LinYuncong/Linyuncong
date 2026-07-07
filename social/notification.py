"""
通知创建 & 查询 & 推送

通知类型: mention / follow / follow_request / favourite / reblog /
          poll / status / update / admin.sign_up / admin.report /
          severed_relationships / moderation_warning / annual_report / quote
"""

import sqlite3
from datetime import datetime, timezone
from social.db import get_conn, transactional


# ---------------------------------------------------------------------------
# 通知查询
# ---------------------------------------------------------------------------

def get_notifications(user_id: int, limit: int = 40, offset: int = 0,#分页参数
                      types: list[str] | None = None,#获取通知的类型
                      unread_only: bool = False) -> list[dict]:
    """
    获取通知列表。
    自动过滤掉：被屏蔽/静音用户触发的通知。
    """
    conn = get_conn()

    # 获取当前用户屏蔽和静音的用户ID
    blocked_ids = [
        r[0] for r in
        conn.execute("SELECT blocked_id FROM blocks WHERE user_id = ?", (user_id,))
    ]
    muted_ids = [
        r[0] for r in
        conn.execute("SELECT muted_id FROM mutes WHERE user_id = ?", (user_id,))
    ]

    conditions = ["n.user_id = ?"]
    params: list = [user_id]

    if types:
        placeholders = ",".join(["?"] * len(types))
        conditions.append(f"n.notification_type IN ({placeholders})")
        params.extend(types)

    if unread_only:
        conditions.append("n.is_read = 0")

    where = " AND ".join(conditions)

    rows = conn.execute(f"""
        SELECT n.id, n.notification_type, n.from_user_id, n.post_id,
               n.is_read, n.created_at,
               n.report_data, n.relationship_severance,
               n.moderation_warning, n.annual_report,
               u.username, u.display_name, u.acct, u.avatar,
               p.content AS post_content,
               p.visibility AS post_visibility
        FROM notifications n
        LEFT JOIN users u ON u.id = n.from_user_id
        LEFT JOIN posts p ON p.id = n.post_id
        WHERE {where}
        ORDER BY n.created_at DESC
        LIMIT ? OFFSET ?
    """, params + [limit, offset]).fetchall()#触发者和帖子可能已被删除，故需添加条件

    result = []
    for r in rows:
        r = dict(r)
        # 过滤：如果触发者在屏蔽/静音列表中，跳过
        if r["from_user_id"] in blocked_ids:
            continue
        if r["from_user_id"] in muted_ids:
            continue
        result.append(r)

    return result


# ---------------------------------------------------------------------------
# 创建通知（由其他模块调用）
# ---------------------------------------------------------------------------

@transactional
def create_notification(user_id: int,
                        notification_type: str,
                        from_user_id: int | None = None,
                        post_id: int | None = None,
                        report_data: str | None = None,
                        relationship_severance: str | None = None,
                        moderation_warning: str | None = None,
                        annual_report: str | None = None) -> dict:
    """
    创建一条通知。

    自动过滤规则：
      - 不给自己发通知
      - 被接收者屏蔽 → 不发送
      - 被接收者静音（且 mute_notifications=1） → 不发送
    """
    conn = get_conn()

    if from_user_id == user_id:
        return {"status": "skipped", "reason": "self"}

    if from_user_id:
        blocked = conn.execute(
            "SELECT 1 FROM blocks WHERE user_id = ? AND blocked_id = ?",
            (user_id, from_user_id)
        ).fetchone()
        if blocked:
            return {"status": "skipped", "reason": "blocked"}

        muted = conn.execute(
            "SELECT 1 FROM mutes WHERE user_id = ? AND muted_id = ? AND mute_notifications = 1",
            (user_id, from_user_id)
        ).fetchone()
        if muted:
            return {"status": "skipped", "reason": "muted"}

    now = _now()
    cursor = conn.execute(
        """INSERT INTO notifications
           (user_id, notification_type, from_user_id, post_id,
            report_data, relationship_severance, moderation_warning,
            annual_report, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (user_id, notification_type, from_user_id, post_id,
         report_data, relationship_severance, moderation_warning,
         annual_report, now)#插入通知
    )
    return {"status": "created", "id": cursor.lastrowid}


# ---------------------------------------------------------------------------
# 关注时的通知（便捷封装）
# ---------------------------------------------------------------------------

def notify_follow(follower_id: int, following_id: int):
    """关注通知"""
    return create_notification(following_id, "follow", from_user_id=follower_id)


def notify_follow_request(requester_id: int, target_id: int):
    """关注请求通知"""
    return create_notification(target_id, "follow_request", from_user_id=requester_id)


def notify_mention(mentioned_user_id: int, from_user_id: int, post_id: int):
    """@提及通知"""
    return create_notification(mentioned_user_id, "mention",
                               from_user_id=from_user_id, post_id=post_id)


def notify_favourite(user_id: int, from_user_id: int, post_id: int):
    """收藏通知（仅通知帖子作者）"""
    return create_notification(user_id, "favourite",
                               from_user_id=from_user_id, post_id=post_id)


def notify_reblog(user_id: int, from_user_id: int, post_id: int):
    """转发通知"""
    return create_notification(user_id, "reblog",
                               from_user_id=from_user_id, post_id=post_id)


# ---------------------------------------------------------------------------
# 已读管理
# ---------------------------------------------------------------------------

@transactional
def mark_read(notification_ids: list[int], user_id: int) -> int:
    """将指定通知标为已读（仅限自己的通知）"""
    conn = get_conn()
    placeholders = ",".join(["?"] * len(notification_ids))
    cursor = conn.execute(
        f"UPDATE notifications SET is_read = 1 "
        f"WHERE id IN ({placeholders}) AND user_id = ?",
        notification_ids + [user_id]
    )
    return cursor.rowcount


@transactional
def mark_all_read(user_id: int) -> int:
    """将所有通知标为已读"""
    conn = get_conn()
    cursor = conn.execute(
        "UPDATE notifications SET is_read = 1 WHERE user_id = ? AND is_read = 0",
        (user_id,)
    )
    return cursor.rowcount


def get_unread_count(user_id: int) -> int:
    """获取未读通知数"""
    conn = get_conn()

    # 排除被屏蔽/静音用户的
    blocked_ids = [
        r[0] for r in
        conn.execute("SELECT blocked_id FROM blocks WHERE user_id = ?", (user_id,))
    ]
    muted_ids = [
        r[0] for r in
        conn.execute("SELECT muted_id FROM mutes WHERE user_id = ?", (user_id,))
    ]
    exclude = blocked_ids + muted_ids

    if exclude:
        placeholders = ",".join(["?"] * len(exclude))
        row = conn.execute(
            f"SELECT COUNT(*) AS cnt FROM notifications "
            f"WHERE user_id = ? AND is_read = 0 AND from_user_id NOT IN ({placeholders})",
            [user_id] + exclude
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT COUNT(*) AS cnt FROM notifications WHERE user_id = ? AND is_read = 0",
            (user_id,)
        ).fetchone()
    return row["cnt"]


# ---------------------------------------------------------------------------
# 通知聚合（合并同类通知）
# ---------------------------------------------------------------------------

def get_aggregated_notifications(user_id: int,
                                 limit: int = 40) -> list[dict]:
    """
    获取聚合后的通知列表。
    同一类型 + 同一帖子的通知会被聚合，避免重复展示。
    例如：5 个人收藏了你的帖子 → 显示为 "张三 等 5 人收藏了你的帖子"
    """
    conn = get_conn()

    # 获取可聚合类型的最新通知
    groups = conn.execute("""
        SELECT n.notification_type,
               n.post_id,
               COUNT(*) AS count,
               MAX(n.created_at) AS latest_at,
               GROUP_CONCAT(n.from_user_id) AS from_user_ids
        FROM notifications n
        WHERE n.user_id = ?
          AND n.is_read = 0
          AND n.notification_type IN ('favourite', 'reblog', 'follow')
        GROUP BY n.notification_type,
                 CASE WHEN n.notification_type = 'follow' THEN n.id
                      ELSE n.post_id END
        ORDER BY latest_at DESC
        LIMIT ?
    """, (user_id, limit)).fetchall()#聚合当前用户，未读，三种类型的通知

    result = []
    for g in groups:
        g = dict(g)
        ids = [int(x) for x in g["from_user_ids"].split(",") if x]#字符串转为整数列表
        if not ids:
            continue
        latest_user = conn.execute(
            "SELECT username, display_name, avatar FROM users WHERE id = ?",
            (ids[0],)
        ).fetchone()#获取第一个触发者信息

        result.append({
            "type": g["notification_type"],
            "post_id": g["post_id"],
            "count": g["count"],
            "latest_at": g["latest_at"],
            "latest_user": dict(latest_user) if latest_user else None,
            "user_ids": ids,
        })

    return result


# ---------------------------------------------------------------------------
# 推送订阅管理
# ---------------------------------------------------------------------------

@transactional
def subscribe_push(user_id: int, endpoint: str,
                   p256dh_key: str, auth_key: str,
                   alerts: dict | None = None) -> dict:
    """注册 WebPush 订阅"""
    conn = get_conn()
    import json
    try:
        conn.execute(
            "INSERT INTO push_subscriptions (user_id, endpoint, p256dh_key, auth_key, alerts) "
            "VALUES (?, ?, ?, ?, ?)",
            (user_id, endpoint, p256dh_key, auth_key,
             json.dumps(alerts or {}, ensure_ascii=False))
        )
    except sqlite3.IntegrityError:
        # 更新已有订阅
        conn.execute(
            "UPDATE push_subscriptions SET p256dh_key = ?, auth_key = ?, alerts = ? "#加密密钥和推送偏好
            "WHERE user_id = ? AND endpoint = ?",#浏览器推送服务端点
            (p256dh_key, auth_key, json.dumps(alerts or {}, ensure_ascii=False),
             user_id, endpoint)
        )
    return {"status": "subscribed"}


@transactional
def unsubscribe_push(user_id: int, endpoint: str) -> dict:
    """取消 WebPush 订阅"""
    conn = get_conn()
    conn.execute(
        "DELETE FROM push_subscriptions WHERE user_id = ? AND endpoint = ?",
        (user_id, endpoint)
    )
    return {"status": "unsubscribed"}


def get_push_subscriptions(user_id: int) -> list[dict]:
    """获取用户的推送订阅"""
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM push_subscriptions WHERE user_id = ?",
        (user_id,)
    ).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# 通知推送引擎（WebPush）
# ---------------------------------------------------------------------------

def push_notification(user_id: int, title: str, body: str,
                      notification_type: str | None = None,
                      post_id: int | None = None) -> list[dict]:
    """
    向用户的所有订阅端点推送通知。
    返回每个端点的推送结果。

    实际生产环境需集成 pywebpush / py-vapid 库。
    这里提供算法骨架。
    """
    subscriptions = get_push_subscriptions(user_id)
    results = []

    for sub in subscriptions:
        # 检查用户是否已关闭该类型通知
        import json
        try:#解析json
            alerts = json.loads(sub["alerts"])
        except (json.JSONDecodeError, TypeError):
            alerts = {}

        if notification_type and not alerts.get(notification_type, True):
            results.append({"endpoint": sub["endpoint"], "status": "disabled"})
            continue

        # === WebPush 推送逻辑（骨架） ===
        # payload = json.dumps({
        #     "title": title,
        #     "body": body,
        #     "icon": "/icon.png",
        #     "data": {
        #         "notification_type": notification_type,
        #         "post_id": post_id,
        #     }
        # })
        #
        # try:
        #     from pywebpush import webpush
        #     webpush(
        #         subscription_info={
        #             "endpoint": sub["endpoint"],
        #             "keys": {"p256dh": sub["p256dh_key"], "auth": sub["auth_key"]}
        #         },
        #         data=payload,
        #         vapid_private_key=VAPID_PRIVATE_KEY,
        #         vapid_claims={"sub": "mailto:admin@example.com"}
        #     )
        #     results.append({"endpoint": sub["endpoint"], "status": "sent"})
        # except Exception as e:
        #     results.append({"endpoint": sub["endpoint"], "status": "failed",
        #                     "error": str(e)})

        # 骨架：标记为已推送
        results.append({
            "endpoint": sub["endpoint"],
            "status": "sent",
            "title": title,
            "body": body,
        })

    return results


def notify_admin(reporter_id: int, post_id: int, reason: str = ""):
    """通知所有管理员（举报）"""
    conn = get_conn()
    admin_ids = conn.execute(
        "SELECT id FROM users WHERE role = 'admin'"
    ).fetchall()
    results = []
    for a in admin_ids:
        r = create_notification(a["id"], "admin.report",
                                from_user_id=reporter_id, post_id=post_id)
        results.append(r)
    return results


# ---------------------------------------------------------------------------
# 辅助
# ---------------------------------------------------------------------------

def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
