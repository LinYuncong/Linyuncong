"""
全文搜索模块

基于 SQLite FTS5 引擎实现搜索：
  - 帖子搜索（内容 + 敏感文本）
  - 用户搜索（用户名 + 显示名 + 简介）
  - 标签搜索（标签名）
  - 混合搜索（一次性搜索帖子 + 用户 + 标签）

FTS5 索引需在建表后通过 init_fts() 初始化。
"""

import re
import sqlite3
from social.db import get_conn, transactional


# ---------------------------------------------------------------------------
# FTS5 索引初始化（首次运行需执行一次）
# ---------------------------------------------------------------------------

@transactional
def init_fts():
    """
    创建 FTS5 全文搜索虚拟表及触发器。
    建表后执行一次即可，后续通过触发器自动同步。
    """
    conn = get_conn()

    # --- 帖子 FTS 索引 ---
    conn.execute("""
        CREATE VIRTUAL TABLE IF NOT EXISTS posts_fts USING fts5(
            content,
            spoiler_text,
            content='posts',
            content_rowid='id'
        )
    """)#使用外部索引表，FTS只存索引

    # 触发器：插入帖子时同步 FTS
    conn.executescript("""
        CREATE TRIGGER IF NOT EXISTS posts_ai AFTER INSERT ON posts BEGIN
            INSERT INTO posts_fts(rowid, content, spoiler_text)
            VALUES (new.id, new.content, new.spoiler_text);
        END;

        CREATE TRIGGER IF NOT EXISTS posts_ad AFTER DELETE ON posts BEGIN
            INSERT INTO posts_fts(posts_fts, rowid, content, spoiler_text)
            VALUES ('delete', old.id, old.content, old.spoiler_text);
        END;

        CREATE TRIGGER IF NOT EXISTS posts_au AFTER UPDATE ON posts BEGIN
            INSERT INTO posts_fts(posts_fts, rowid, content, spoiler_text)
            VALUES ('delete', old.id, old.content, old.spoiler_text);
            INSERT INTO posts_fts(rowid, content, spoiler_text)
            VALUES (new.id, new.content, new.spoiler_text);
        END;
    """)#三个触发器分别对应INSERT,DELETE,UPDATE

    # --- 用户 FTS 索引 ---
    conn.execute("""
        CREATE VIRTUAL TABLE IF NOT EXISTS users_fts USING fts5(
            username,
            display_name,
            note,
            content='users',
            content_rowid='id'
        )
    """)

    conn.executescript("""
        CREATE TRIGGER IF NOT EXISTS users_ai AFTER INSERT ON users BEGIN
            INSERT INTO users_fts(rowid, username, display_name, note)
            VALUES (new.id, new.username, new.display_name, new.note);
        END;

        CREATE TRIGGER IF NOT EXISTS users_ad AFTER DELETE ON users BEGIN
            INSERT INTO users_fts(users_fts, rowid, username, display_name, note)
            VALUES ('delete', old.id, old.username, old.display_name, old.note);
        END;

        CREATE TRIGGER IF NOT EXISTS users_au AFTER UPDATE ON users BEGIN
            INSERT INTO users_fts(users_fts, rowid, username, display_name, note)
            VALUES ('delete', old.id, old.username, old.display_name, old.note);
            INSERT INTO users_fts(rowid, username, display_name, note)
            VALUES (new.id, new.username, new.display_name, new.note);
        END;
    """)

    # --- 标签 FTS 索引 ---
    conn.execute("""
        CREATE VIRTUAL TABLE IF NOT EXISTS tags_fts USING fts5(
            name,
            content='tags',
            content_rowid='id'
        )
    """)

    conn.executescript("""
        CREATE TRIGGER IF NOT EXISTS tags_ai AFTER INSERT ON tags BEGIN
            INSERT INTO tags_fts(rowid, name) VALUES (new.id, new.name);
        END;

        CREATE TRIGGER IF NOT EXISTS tags_ad AFTER DELETE ON tags BEGIN
            INSERT INTO tags_fts(tags_fts, rowid, name) VALUES ('delete', old.id, old.name);
        END;

        CREATE TRIGGER IF NOT EXISTS tags_au AFTER UPDATE ON tags BEGIN
            INSERT INTO tags_fts(tags_fts, rowid, name) VALUES ('delete', old.id, old.name);
            INSERT INTO tags_fts(rowid, name) VALUES (new.id, new.name);
        END;
    """)

    # 初始化：将已有数据同步到 FTS
    conn.execute("INSERT INTO posts_fts(posts_fts) VALUES ('rebuild')")
    conn.execute("INSERT INTO users_fts(users_fts) VALUES ('rebuild')")
    conn.execute("INSERT INTO tags_fts(tags_fts) VALUES ('rebuild')")

    return {"status": "fts_initialized"}


# ---------------------------------------------------------------------------
# 帖子全文搜索
# ---------------------------------------------------------------------------

def search_posts(query: str,#搜索词
                 viewer_id: int | None = None,
                 limit: int = 20,
                 offset: int = 0,
                 sort: str = "relevance") -> list[dict]:
    """
    全文搜索帖子。

    搜索范围: content, spoiler_text

    sort:
      - "relevance"  按 FTS 相关性排序 (bm25)
      - "recent"     按发布时间倒序
      - "popular"    按收藏+转发数倒序

    自动过滤:
      - direct 私密帖子（仅作者和提及者可见）
      - 被屏蔽用户发的帖子
    """
    conn = get_conn()

    # 获取屏蔽列表
    blocked_ids = _get_blocked_ids(conn, viewer_id) if viewer_id else []

    # 清理查询输入
    query = _sanitize_query(query)
    if not query:
        return []

    if sort == "relevance":
        # FTS5 bm25 相关性排序
        rows = conn.execute("""
            SELECT p.id, p.content, p.spoiler_text, p.sensitive, p.visibility,
                   p.author_id, p.in_reply_to_id, p.favourites_count,
                   p.reblogs_count, p.replies_count,
                   p.created_at, p.edited_at, p.url,
                   u.username, u.display_name, u.acct, u.avatar,
                   rank
            FROM posts_fts f
            JOIN posts p ON p.id = f.rowid
            JOIN users u ON u.id = p.author_id
            WHERE posts_fts MATCH ?
              AND p.visibility != 'direct'
            ORDER BY rank
            LIMIT ? OFFSET ?
        """, (query, limit, offset)).fetchall()#获取作者信息，排除私信
    elif sort == "recent":
        rows = conn.execute("""
            SELECT p.*, u.username, u.display_name, u.acct, u.avatar
            FROM posts_fts f
            JOIN posts p ON p.id = f.rowid
            JOIN users u ON u.id = p.author_id
            WHERE posts_fts MATCH ?
              AND p.visibility != 'direct'
            ORDER BY p.created_at DESC
            LIMIT ? OFFSET ?
        """, (query, limit, offset)).fetchall()#p.*表示按时间和热度排序
    elif sort == "popular":
        rows = conn.execute("""
            SELECT p.*, u.username, u.display_name, u.acct, u.avatar
            FROM posts_fts f
            JOIN posts p ON p.id = f.rowid
            JOIN users u ON u.id = p.author_id
            WHERE posts_fts MATCH ?
              AND p.visibility != 'direct'
            ORDER BY (p.favourites_count + p.reblogs_count) DESC
            LIMIT ? OFFSET ?
        """, (query, limit, offset)).fetchall()
    else:
        raise ValueError(f"未知排序方式: {sort}")

    # 过滤被屏蔽用户
    if blocked_ids:
        rows = [r for r in rows if r["author_id"] not in blocked_ids]

    # 统一过滤可见性（public / friends_only / private / direct + 黑白名单）
    rows = [_filter_post_visibility(dict(r), viewer_id) for r in rows]
    rows = [r for r in rows if r is not None]

    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# 用户搜索
# ---------------------------------------------------------------------------

def search_users(query: str,
                 limit: int = 20,
                 offset: int = 0) -> list[dict]:
    """
    全文搜索用户。

    搜索范围: username, display_name, note（个人简介）
    按相关性排序（bm25）。
    """
    conn = get_conn()

    query = _sanitize_query(query)
    if not query:
        return []

    rows = conn.execute("""
        SELECT u.id, u.username, u.display_name, u.acct, u.avatar,
               u.note, u.followers_count, u.following_count,
               u.statuses_count, u.created_at, u.url,
               rank
        FROM users_fts f
        JOIN users u ON u.id = f.rowid
        WHERE users_fts MATCH ?
        ORDER BY rank
        LIMIT ? OFFSET ?
    """, (query, limit, offset)).fetchall()

    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# 标签搜索
# ---------------------------------------------------------------------------

def search_tags(query: str,
                limit: int = 20,
                offset: int = 0) -> list[dict]:
    """
    搜索标签。

    搜索范围: name
    按使用量排序（total_uses）。
    """
    conn = get_conn()

    query = _sanitize_query(query)
    if not query:
        return []

    rows = conn.execute("""
        SELECT t.id, t.name, t.url, t.total_uses, t.total_accounts, t.created_at
        FROM tags_fts f
        JOIN tags t ON t.id = f.rowid
        WHERE tags_fts MATCH ?
        ORDER BY t.total_uses DESC
        LIMIT ? OFFSET ?
    """, (query, limit, offset)).fetchall()

    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# 标签下的帖子搜索
# ---------------------------------------------------------------------------

def search_posts_by_tag(tag_name: str,
                        viewer_id: int | None = None,
                        limit: int = 20,
                        offset: int = 0,
                        sort: str = "recent") -> list[dict]:
    """
    搜索某个标签下的帖子。
    """
    conn = get_conn()

    blocked_ids = _get_blocked_ids(conn, viewer_id) if viewer_id else []

    order = "p.created_at DESC" if sort == "recent" else "p.favourites_count + p.reblogs_count DESC"

    rows = conn.execute(f"""
        SELECT p.*, u.username, u.display_name, u.acct, u.avatar
        FROM post_tags pt
        JOIN posts p ON p.id = pt.post_id
        JOIN tags t ON t.id = pt.tag_id
        JOIN users u ON u.id = p.author_id
        WHERE t.name = ?
          AND p.visibility != 'direct'
        ORDER BY {order}
        LIMIT ? OFFSET ?
    """, (tag_name, limit, offset)).fetchall()

    if blocked_ids:
        rows = [r for r in rows if r["author_id"] not in blocked_ids]

    rows = [_filter_post_visibility(dict(r), viewer_id) for r in rows]
    rows = [r for r in rows if r is not None]

    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# 混合搜索（帖子 + 用户 + 标签）
# ---------------------------------------------------------------------------

def search_all(query: str,
               viewer_id: int | None = None,
               limit: int = 20) -> dict:
    """
    一次搜索返回帖子、用户、标签三类结果。
    返回 {"posts": [...], "users": [...], "tags": [...]}
    """
    return {
        "posts": search_posts(query, viewer_id=viewer_id, limit=limit, sort="relevance"),
        "users": search_users(query, limit=limit),
        "tags": search_tags(query, limit=limit),
    }


# ---------------------------------------------------------------------------
# 带过滤的高级搜索
# ---------------------------------------------------------------------------

def advanced_search(keyword: str | None = None,
                    author_id: int | None = None,
                    tag: str | None = None,
                    visibility: str | None = None,
                    date_from: str | None = None,
                    date_to: str | None = None,
                    viewer_id: int | None = None,
                    limit: int = 20,
                    offset: int = 0) -> list[dict]:
    """
    高级组合搜索：
    - keyword: 全文关键词
    - author_id: 指定作者
    - visibility: 限定可见性
    - date_from / date_to: 时间范围
    - tag: 限定标签
    """
    conn = get_conn()

    conditions = ["1=1"]#默认为真
    params: list = []

    if keyword:
        conditions.append("p.id IN (SELECT rowid FROM posts_fts WHERE posts_fts MATCH ?)")
        params.append(_sanitize_query(keyword))

    if author_id:
        conditions.append("p.author_id = ?")
        params.append(author_id)

    if visibility:
        conditions.append("p.visibility = ?")
        params.append(visibility)

    if date_from:
        conditions.append("p.created_at >= ?")
        params.append(date_from)

    if date_to:
        conditions.append("p.created_at <= ?")
        params.append(date_to)

    if tag:
        conditions.append("p.id IN (SELECT pt.post_id FROM post_tags pt "
                          "JOIN tags t ON t.id = pt.tag_id WHERE t.name = ?)")
        params.append(tag)

    where = " AND ".join(conditions)

    rows = conn.execute(f"""
        SELECT p.*, u.username, u.display_name, u.acct, u.avatar
        FROM posts p
        JOIN users u ON u.id = p.author_id
        WHERE {where}
        ORDER BY p.created_at DESC
        LIMIT ? OFFSET ?
    """, params + [limit, offset]).fetchall()

    blocked_ids = _get_blocked_ids(conn, viewer_id) if viewer_id else []
    if blocked_ids:
        rows = [r for r in rows if r["author_id"] not in blocked_ids]

    rows = [_filter_post_visibility(dict(r), viewer_id) for r in rows]
    rows = [r for r in rows if r is not None]

    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# 搜索建议（自动补全）
# ---------------------------------------------------------------------------

def search_suggest(query: str,
                   max_results: int = 8) -> dict:
    """
    搜索建议 / 自动补全。
    返回匹配的标签 + 用户名建议。
    """
    conn = get_conn()

    if not query or len(query) < 1:
        return {"tags": [], "users": []}

    like_pattern = f"{query}%"

    # 标签建议（按使用量排序）
    tag_rows = conn.execute("""
        SELECT name, total_uses
        FROM tags
        WHERE name LIKE ?
        ORDER BY total_uses DESC
        LIMIT ?
    """, (like_pattern, max_results)).fetchall()

    # 用户建议（按粉丝数排序）
    user_rows = conn.execute("""
        SELECT username, display_name, avatar, followers_count
        FROM users
        WHERE username LIKE ? OR display_name LIKE ?
        ORDER BY followers_count DESC
        LIMIT ?
    """, (like_pattern, like_pattern, max_results)).fetchall()

    return {
        "tags": [dict(r) for r in tag_rows],
        "users": [dict(r) for r in user_rows],
    }


# ---------------------------------------------------------------------------
# 内部辅助
# ---------------------------------------------------------------------------

def _sanitize_query(query: str) -> str:
    """
    清理 & 转义搜索查询。
    FTS5 语法处理：将用户输入的纯文本转为安全的 MATCH 表达式。
    """
    if not query:
        return ""
    # 去除 FTS5 特殊字符，防止语法错误
    # 保留中文、英文、数字、空格
    cleaned = re.sub(r'[^\w\u4e00-\u9fff\s-]', '', query).strip()
    if not cleaned:
        return ""
    # 每个词添加前缀匹配 *
    terms = cleaned.split()
    return " AND ".join(f'"{t}"*' for t in terms)


def _get_blocked_ids(conn, user_id: int) -> list[int]:
    """获取用户屏蔽的 ID 列表"""
    rows = conn.execute(
        "SELECT blocked_id FROM blocks WHERE user_id = ?", (user_id,)
    ).fetchall()
    return [r[0] for r in rows]


def _filter_post_visibility(post: dict, viewer_id: int | None) -> dict | None:
    """
    统一过滤帖子可见性。
    支持: public / unlisted / friends_only / private / direct + 黑白名单
    通过 social.check_post_visibility 实现。
    """
    from social.social import check_post_visibility
    visible, _ = check_post_visibility(post["id"], viewer_id)
    return post if visible else None
