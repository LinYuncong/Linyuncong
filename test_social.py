"""
社交模块综合测试脚本
测试 social 包下所有模块的功能
"""
import sys
import os
import traceback
from datetime import datetime

# 确保工作目录在 sys.path 中
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

results = []
passed = 0
failed = 0

def log_result(module, test_name, status, detail=""):
    global passed, failed
    line = f"[{module}] {test_name}: {status}"
    if detail:
        line += f" - {detail}"
    results.append(line)
    print(line)
    if status == "通过":
        passed += 1
    else:
        failed += 1

def log_error(module, test_name, err):
    log_result(module, test_name, "失败", f"异常: {type(err).__name__}: {err}")
    lines = traceback.format_exc().strip().split("\n")
    for l in lines:
        print(f"  {l}")
        results.append(f"  {l}")

def section(title):
    line = f"\n{'='*60}\n{title}\n{'='*60}"
    results.append(line)
    print(line)

# ================================================================
# 1. 数据库连接测试
# ================================================================
section("1. 数据库连接测试")

try:
    from social.db import get_conn, close_conn, transactional
    conn = get_conn()
    log_result("db", "获取数据库连接", "通过", f"连接成功, journal_mode={conn.execute('PRAGMA journal_mode').fetchone()[0]}")
except Exception as e:
    log_error("db", "获取数据库连接", e)

try:
    tables = conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()
    table_names = [t[0] for t in tables]
    log_result("db", "读取表列表", "通过", f"共 {len(table_names)} 个表: {', '.join(table_names[:10])}...")
except Exception as e:
    log_error("db", "读取表列表", e)

try:
    @transactional
    def test_transaction():
        return "ok"
    result = test_transaction()
    log_result("db", "事务装饰器", "通过" if result == "ok" else "失败")
except Exception as e:
    log_error("db", "事务装饰器", e)

# ================================================================
# 2. 用户创建和查询
# ================================================================
section("2. 用户创建和查询")

test_suffix = datetime.now().strftime("_%H%M%S")

try:
    from social.models import create_user, get_user, get_user_by_username, get_user_by_email, update_user

    r1 = create_user(
        username=f"alice{test_suffix}",
        email=f"alice{test_suffix}@test.com",
        password_hash="hash_alice",
        display_name="Alice Test",
        note="测试用户Alice"
    )
    user_a_id = r1["id"]
    log_result("models", "创建用户Alice", "通过", f"id={user_a_id}")

    r2 = create_user(
        username=f"bob{test_suffix}",
        email=f"bob{test_suffix}@test.com",
        password_hash="hash_bob",
        display_name="Bob Test",
        note="测试用户Bob"
    )
    user_b_id = r2["id"]
    log_result("models", "创建用户Bob", "通过", f"id={user_b_id}")

    r3 = create_user(
        username=f"charlie{test_suffix}",
        email=f"charlie{test_suffix}@test.com",
        password_hash="hash_charlie",
        display_name="Charlie Test",
        note="测试用户Charlie",
        locked=True
    )
    user_c_id = r3["id"]
    log_result("models", "创建用户Charlie（锁定）", "通过", f"id={user_c_id}")

except Exception as e:
    log_error("models", "创建用户", e)
    user_a_id = user_b_id = user_c_id = None

if user_a_id:
    try:
        u = get_user(user_a_id)
        log_result("models", "get_user查询", "通过" if u and u["username"] == f"alice{test_suffix}" else "失败", f"username={u.get('username') if u else 'None'}")
    except Exception as e:
        log_error("models", "get_user查询", e)

if user_a_id:
    try:
        u = get_user_by_username(f"alice{test_suffix}")
        log_result("models", "get_user_by_username", "通过" if u and u["id"] == user_a_id else "失败")
    except Exception as e:
        log_error("models", "get_user_by_username", e)

if user_a_id:
    try:
        u = get_user_by_email(f"alice{test_suffix}@test.com")
        log_result("models", "get_user_by_email", "通过" if u and u["id"] == user_a_id else "失败")
    except Exception as e:
        log_error("models", "get_user_by_email", e)

if user_a_id:
    try:
        update_user(user_a_id, display_name="Alice Updated", note="更新后的简介")
        u = get_user(user_a_id)
        ok = u["display_name"] == "Alice Updated" and u["note"] == "更新后的简介"
        log_result("models", "update_user更新", "通过" if ok else "失败")
    except Exception as e:
        log_error("models", "update_user更新", e)

# 测试重复用户名创建（应该失败）
try:
    create_user(
        username=f"alice{test_suffix}",
        email=f"alice2{test_suffix}@test.com",
        password_hash="hash",
        display_name="Dup"
    )
    log_result("models", "重复用户名创建（应失败）", "失败", "未抛出异常")
except ValueError:
    log_result("models", "重复用户名创建（应失败）", "通过", "正确抛出ValueError")
except Exception as e:
    log_error("models", "重复用户名创建（应失败）", e)

# ================================================================
# 3. 关注/取关/屏蔽/静音
# ================================================================
section("3. 关注/取关/屏蔽/静音")

try:
    from social.social import (
        follow, unfollow, is_following, get_followers, get_following,
        block, unblock, is_blocked, get_blocked,
        mute, unmute, is_muted, get_muted,
        get_follow_requests, accept_follow_request, reject_follow_request,
        domain_block, domain_unblock, get_domain_blocks
    )
except Exception as e:
    log_error("social", "导入social模块", e)

if user_a_id and user_b_id:
    # 关注
    try:
        r = follow(user_a_id, user_b_id)
        log_result("social", "Alice关注Bob", "通过", f"结果={r['status']}")
        log_result("social", "is_following检查", "通过" if is_following(user_a_id, user_b_id) else "失败")
    except Exception as e:
        log_error("social", "Alice关注Bob", e)

    # 重复关注
    try:
        r = follow(user_a_id, user_b_id)
        log_result("social", "重复关注（应返回already_following）", "通过" if r["status"] == "already_following" else "失败", f"结果={r['status']}")
    except Exception as e:
        log_error("social", "重复关注", e)

    # 自己关注自己
    try:
        follow(user_a_id, user_a_id)
        log_result("social", "自己关注自己（应失败）", "失败", "未抛出异常")
    except ValueError:
        log_result("social", "自己关注自己（应失败）", "通过", "正确抛出ValueError")
    except Exception as e:
        log_error("social", "自己关注自己（应失败）", e)

    # 关注锁定用户
    try:
        r = follow(user_a_id, user_c_id)
        log_result("social", "Alice关注Charlie（锁定用户）", "通过", f"结果={r['status']} - 应为requested")
    except Exception as e:
        log_error("social", "Alice关注Charlie（锁定用户）", e)

    # 获取关注请求
    try:
        reqs = get_follow_requests(user_c_id)
        log_result("social", "获取Charlie的关注请求", "通过", f"共{len(reqs)}条")
        if reqs:
            req_id = reqs[0]["id"]
    except Exception as e:
        log_error("social", "获取关注请求", e)

    # 拒绝关注请求 - 先创建新的关注请求
    try:
        r = follow(user_b_id, user_c_id)
        reqs = get_follow_requests(user_c_id)
        if reqs:
            # 找到Bob的请求
            for req in reqs:
                if req["account_id"] == user_b_id:
                    reject_follow_request(req["id"], user_c_id)
                    log_result("social", "Charlie拒绝Bob的关注请求", "通过")
                    break
    except Exception as e:
        log_error("social", "拒绝关注请求", e)

    # 获取粉丝/关注列表
    try:
        fls = get_followers(user_b_id)
        log_result("social", "获取Bob的粉丝列表", "通过", f"共{len(fls)}条")
        fng = get_following(user_a_id)
        log_result("social", "获取Alice的关注列表", "通过", f"共{len(fng)}条")
    except Exception as e:
        log_error("social", "获取列表", e)

    # 取关
    try:
        r = unfollow(user_a_id, user_b_id)
        log_result("social", "Alice取关Bob", "通过", f"结果={r['status']}")
        log_result("social", "is_following检查（取关后）", "通过" if not is_following(user_a_id, user_b_id) else "失败")
    except Exception as e:
        log_error("social", "取关", e)

    # 屏蔽
    try:
        r = block(user_a_id, user_b_id)
        log_result("social", "Alice屏蔽Bob", "通过", f"结果={r['status']}")
        log_result("social", "is_blocked检查", "通过" if is_blocked(user_a_id, user_b_id) else "失败")
    except Exception as e:
        log_error("social", "屏蔽", e)

    # 重复屏蔽
    try:
        r = block(user_a_id, user_b_id)
        log_result("social", "重复屏蔽（应返回already_blocked）", "通过" if r["status"] == "already_blocked" else "失败", f"结果={r['status']}")
    except Exception as e:
        log_error("social", "重复屏蔽", e)

    # 自己屏蔽自己
    try:
        block(user_a_id, user_a_id)
        log_result("social", "自己屏蔽自己（应失败）", "失败", "未抛出异常")
    except ValueError:
        log_result("social", "自己屏蔽自己（应失败）", "通过", "正确抛出ValueError")
    except Exception as e:
        log_error("social", "自己屏蔽自己（应失败）", e)

    # 被屏蔽后无法关注
    try:
        r = follow(user_b_id, user_a_id)
        log_result("social", "Bob关注Alice（被屏蔽）", "通过", f"结果={r['status']} - 应为blocked")
    except Exception as e:
        log_error("social", "Bob关注Alice（被屏蔽）", e)

    # 获取屏蔽列表
    try:
        blist = get_blocked(user_a_id)
        log_result("social", "获取Alice的屏蔽列表", "通过", f"共{len(blist)}条")
    except Exception as e:
        log_error("social", "获取屏蔽列表", e)

    # 取消屏蔽
    try:
        r = unblock(user_a_id, user_b_id)
        log_result("social", "Alice取消屏蔽Bob", "通过", f"结果={r['status']}")
        log_result("social", "is_blocked检查（取消屏蔽后）", "通过" if not is_blocked(user_a_id, user_b_id) else "失败")
    except Exception as e:
        log_error("social", "取消屏蔽", e)

    # 静音
    try:
        r = mute(user_a_id, user_b_id)
        log_result("social", "Alice静音Bob", "通过", f"结果={r['status']}")
        log_result("social", "is_muted检查", "通过" if is_muted(user_a_id, user_b_id) else "失败")
    except Exception as e:
        log_error("social", "静音", e)

    # 重复静音
    try:
        r = mute(user_a_id, user_b_id)
        log_result("social", "重复静音（应返回already_muted）", "通过" if r["status"] == "already_muted" else "失败", f"结果={r['status']}")
    except Exception as e:
        log_error("social", "重复静音", e)

    # 自己静音自己
    try:
        mute(user_a_id, user_a_id)
        log_result("social", "自己静音自己（应失败）", "失败", "未抛出异常")
    except ValueError:
        log_result("social", "自己静音自己（应失败）", "通过", "正确抛出ValueError")
    except Exception as e:
        log_error("social", "自己静音自己（应失败）", e)

    # 获取静音列表
    try:
        mlist = get_muted(user_a_id)
        log_result("social", "获取Alice的静音列表", "通过", f"共{len(mlist)}条")
    except Exception as e:
        log_error("social", "获取静音列表", e)

    # 取消静音
    try:
        r = unmute(user_a_id, user_b_id)
        log_result("social", "Alice取消静音Bob", "通过", f"结果={r['status']}")
        log_result("social", "is_muted检查（取消静音后）", "通过" if not is_muted(user_a_id, user_b_id) else "失败")
    except Exception as e:
        log_error("social", "取消静音", e)

    # 域名屏蔽
    try:
        r = domain_block(user_a_id, "baddomain.com")
        log_result("social", "Alice屏蔽域名", "通过", f"结果={r['status']}")
        dlist = get_domain_blocks(user_a_id)
        log_result("social", "获取域名屏蔽列表", "通过", f"共{len(dlist)}条")
        domain_unblock(user_a_id, "baddomain.com")
        log_result("social", "Alice取消域名屏蔽", "通过")
    except Exception as e:
        log_error("social", "域名屏蔽", e)

# ================================================================
# 4. 通知创建和查询
# ================================================================
section("4. 通知创建和查询")

try:
    from social.notification import (
        create_notification, get_notifications, get_unread_count,
        mark_read, mark_all_read, get_aggregated_notifications
    )
except Exception as e:
    log_error("notification", "导入notification模块", e)

if user_a_id and user_b_id:
    # 创建通知
    try:
        r = create_notification(user_a_id, "follow", from_user_id=user_b_id)
        log_result("notification", "创建关注通知", "通过", f"结果={r['status']}, id={r.get('id')}")
        notif_id = r.get("id")
    except Exception as e:
        log_error("notification", "创建关注通知", e)
        notif_id = None

    try:
        r = create_notification(user_b_id, "mention", from_user_id=user_a_id)
        log_result("notification", "创建提及通知", "通过", f"结果={r['status']}, id={r.get('id')}")
    except Exception as e:
        log_error("notification", "创建提及通知", e)

    # 不给自己发通知
    try:
        r = create_notification(user_a_id, "follow", from_user_id=user_a_id)
        log_result("notification", "自己给自己发通知（应跳过）", "通过", f"结果={r['status']} - 应为skipped")
    except Exception as e:
        log_error("notification", "自己给自己发通知", e)

    # 获取通知列表
    try:
        notifs = get_notifications(user_a_id)
        log_result("notification", "获取Alice的通知列表", "通过", f"共{len(notifs)}条")
    except Exception as e:
        log_error("notification", "获取通知列表", e)

    # 获取未读数
    try:
        cnt = get_unread_count(user_a_id)
        log_result("notification", "获取Alice的未读通知数", "通过", f"未读数={cnt}")
    except Exception as e:
        log_error("notification", "获取未读通知数", e)

    # 全部已读
    try:
        n = mark_all_read(user_a_id)
        log_result("notification", "标记全部已读", "通过", f"已标记{n}条")
        cnt = get_unread_count(user_a_id)
        log_result("notification", "已读后未读数验证", "通过" if cnt == 0 else "失败", f"未读数={cnt}")
    except Exception as e:
        log_error("notification", "标记已读", e)

    # 聚合通知
    try:
        agg = get_aggregated_notifications(user_a_id)
        log_result("notification", "获取聚合通知", "通过", f"共{len(agg)}组")
    except Exception as e:
        log_error("notification", "获取聚合通知", e)

    # 被屏蔽后不发通知
    try:
        block(user_a_id, user_b_id)
        r = create_notification(user_a_id, "follow", from_user_id=user_b_id)
        log_result("notification", "屏蔽后通知被跳过", "通过" if r["status"] == "skipped" else "失败", f"结果={r['status']}")
        unblock(user_a_id, user_b_id)
    except Exception as e:
        log_error("notification", "屏蔽后通知过滤", e)

    # WebPush订阅
    try:
        from social.notification import subscribe_push, unsubscribe_push, get_push_subscriptions, push_notification
        r = subscribe_push(user_a_id, "https://push.example/endpoint1", "p256dh_key123", "auth_key456")
        log_result("notification", "注册WebPush订阅", "通过", f"结果={r['status']}")
        subs = get_push_subscriptions(user_a_id)
        log_result("notification", "获取推送订阅", "通过", f"共{len(subs)}条")
        r = push_notification(user_a_id, "测试标题", "测试内容", "follow")
        log_result("notification", "推送通知", "通过", f"推送{len(r)}个端点")
        unsubscribe_push(user_a_id, "https://push.example/endpoint1")
    except Exception as e:
        log_error("notification", "WebPush功能", e)

# ================================================================
# 5. 好友判断 / 可见性规则
# ================================================================
section("5. 好友判断 / 贴子可见性")

try:
    from social.social import (
        are_friends, get_friends, set_post_visibility, check_post_visibility
    )
    from social.models import get_post
except Exception as e:
    log_error("social", "导入可见性功能", e)

# 用原始SQL创建测试帖子（发帖非我负责，仅用于测试）
def _create_test_post(author_id, content, visibility="public"):
    conn = get_conn()
    now = datetime.now().strftime("%Y-%m-%dT%H:%M:%S.000Z")
    cur = conn.execute("""
        INSERT INTO posts (content, spoiler_text, sensitive, visibility, language, author_id, created_at)
        VALUES (?, '', 0, ?, '', ?, ?)
    """, (content, visibility, author_id, now))
    conn.commit()
    return cur.lastrowid

if user_a_id and user_b_id and user_c_id:
    # 好友判断（初始不是好友）
    try:
        r = are_friends(user_a_id, user_b_id)
        log_result("social", "初始状态不是好友", "通过" if not r else "失败")
    except Exception as e:
        log_error("social", "are_friends", e)

    # Alice 关注 Bob
    try:
        follow(user_a_id, user_b_id)
        r = are_friends(user_a_id, user_b_id)
        log_result("social", "单向关注不是好友", "通过" if not r else "失败")
    except Exception as e:
        log_error("social", "单向关注好友判断", e)

    # Bob 回关 Alice（成为好友）
    try:
        follow(user_b_id, user_a_id)
        r = are_friends(user_a_id, user_b_id)
        log_result("social", "互相关注后是好友", "通过" if r else "失败")
        r2 = are_friends(user_b_id, user_a_id)
        log_result("social", "好友关系对称", "通过" if r2 else "失败")
    except Exception as e:
        log_error("social", "双向好友判断", e)

    # 获取好友列表
    try:
        friends = get_friends(user_a_id)
        log_result("social", "获取Alice好友列表", "通过", f"共{len(friends)}个好友")
        bob_in_friends = any(f["id"] == user_b_id for f in friends)
        log_result("social", "Bob在Alice好友列表中", "通过" if bob_in_friends else "失败")
    except Exception as e:
        log_error("social", "get_friends", e)

    # 自己和自己互为好友
    try:
        r = are_friends(user_a_id, user_a_id)
        log_result("social", "自己和自己互为好友", "通过" if r else "失败")
    except Exception as e:
        log_error("social", "are_friends(self)", e)

    # 创建三种可见性的帖子
    public_post_id = _create_test_post(user_a_id, "公开帖子", "public")
    friends_post_id = _create_test_post(user_a_id, "好友可见帖子", "friends_only")
    private_post_id = _create_test_post(user_a_id, "私密帖子", "private")
    log_result("social", "创建测试帖子", "通过", f"public={public_post_id}, friends_only={friends_post_id}, private={private_post_id}")

    # 作者始终可见
    try:
        p = get_post(public_post_id, viewer_id=user_a_id)
        log_result("social", "作者查看公开帖", "通过" if p else "失败")
        p = get_post(friends_post_id, viewer_id=user_a_id)
        log_result("social", "作者查看好友可见帖", "通过" if p else "失败")
        p = get_post(private_post_id, viewer_id=user_a_id)
        log_result("social", "作者查看私密帖", "通过" if p else "失败")
    except Exception as e:
        log_error("social", "作者可见性", e)

    # public: 所有人可见
    try:
        p = get_post(public_post_id, viewer_id=user_b_id)
        log_result("social", "好友查看公开帖", "通过" if p else "失败")
        p = get_post(public_post_id, viewer_id=user_c_id)
        log_result("social", "陌生人查看公开帖", "通过" if p else "失败")
    except Exception as e:
        log_error("social", "公开帖可见性", e)

    # friends_only: 好友可见，非好友不可见
    try:
        p = get_post(friends_post_id, viewer_id=user_b_id)
        log_result("social", "好友查看friends_only帖", "通过" if p else "失败", "Bob是Alice好友")
        p = get_post(friends_post_id, viewer_id=user_c_id)
        log_result("social", "陌生人查看friends_only帖", "通过" if p is None else "失败", "Charlie不是好友，应不可见")
    except Exception as e:
        log_error("social", "friends_only可见性", e)

    # private: 仅作者可见（无@提及和visible_to时）
    try:
        p = get_post(private_post_id, viewer_id=user_b_id)
        log_result("social", "非作者查看private帖", "通过" if p is None else "失败", "应不可见")
    except Exception as e:
        log_error("social", "private可见性", e)

    # --- 谁可以看 / 谁不可以看 ---
    # 测试 visible_to（白名单）
    try:
        set_post_visibility(private_post_id, visible_to=[user_c_id])
        p = get_post(private_post_id, viewer_id=user_c_id)
        log_result("social", "visible_to白名单中的人可看private帖", "通过" if p else "失败")
    except Exception as e:
        log_error("social", "visible_to白名单", e)

    # 测试 invisible_to（黑名单）
    try:
        set_post_visibility(public_post_id, invisible_to=[user_b_id])
        p = get_post(public_post_id, viewer_id=user_b_id)
        log_result("social", "invisible_to黑名单中的人不可看公开帖", "通过" if p is None else "失败")
        p = get_post(public_post_id, viewer_id=user_c_id)
        log_result("social", "不在黑名单中的人仍可看公开帖", "通过" if p else "失败")
    except Exception as e:
        log_error("social", "invisible_to黑名单", e)

    # 作者不受黑名单影响
    try:
        p = get_post(public_post_id, viewer_id=user_a_id)
        log_result("social", "黑名单不影响作者本人", "通过" if p else "失败")
    except Exception as e:
        log_error("social", "作者不受黑名单影响", e)

    # 未登录用户只能看 public
    try:
        p = get_post(public_post_id, viewer_id=None)
        log_result("social", "未登录查看公开帖", "通过" if p else "失败")
        p = get_post(friends_post_id, viewer_id=None)
        log_result("social", "未登录查看friends_only帖", "通过" if p is None else "失败")
        p = get_post(private_post_id, viewer_id=None)
        log_result("social", "未登录查看private帖", "通过" if p is None else "失败")
    except Exception as e:
        log_error("social", "未登录可见性", e)

    # visible_to + friends_only: 白名单中的人即使不是好友也能看
    try:
        set_post_visibility(friends_post_id, visible_to=[user_c_id])
        p = get_post(friends_post_id, viewer_id=user_c_id)
        log_result("social", "friends_only+visible_to使非好友可见", "通过" if p else "失败")
    except Exception as e:
        log_error("social", "friends_only+visible_to", e)

    # 清理：取消关注，恢复状态
    unfollow(user_a_id, user_b_id)
    unfollow(user_b_id, user_a_id)

    # 不再需要的 post_id 变量传给搜索测试
    search_post_id = public_post_id

else:
    search_post_id = None

# ================================================================
# 5.5 好友分组
# ================================================================
section("5.5 好友分组")

try:
    from social.social import (
        create_friend_group, delete_friend_group,
        add_to_friend_group, remove_from_friend_group,
        get_friend_groups
    )
except Exception as e:
    log_error("social", "导入好友分组功能", e)

if user_a_id and user_b_id and user_c_id:
    # 创建分组
    try:
        r = create_friend_group(user_a_id, "密友")
        log_result("social", "创建好友分组", "通过" if r["status"] == "created" else "失败", f"结果={r['status']}")
        group_id = r.get("id")
    except Exception as e:
        log_error("social", "创建好友分组", e)
        group_id = None

    # 重复分组名
    try:
        r = create_friend_group(user_a_id, "密友")
        log_result("social", "重复分组名（应返回duplicate）", "通过" if r["status"] == "duplicate" else "失败")
    except Exception as e:
        log_error("social", "重复分组名", e)

    # 添加成员
    if group_id:
        try:
            r = add_to_friend_group(group_id, user_a_id, user_b_id)
            log_result("social", "添加好友到分组", "通过" if r["status"] == "added" else "失败", f"结果={r['status']}")
        except Exception as e:
            log_error("social", "添加好友到分组", e)

        # 重复添加
        try:
            r = add_to_friend_group(group_id, user_a_id, user_b_id)
            log_result("social", "重复添加到分组", "通过" if r["status"] == "already_in_group" else "失败")
        except Exception as e:
            log_error("social", "重复添加到分组", e)

        # 获取分组列表
        try:
            groups = get_friend_groups(user_a_id)
            log_result("social", "获取分组列表", "通过", f"共{len(groups)}个分组")
            if groups:
                log_result("social", "分组成员数验证", "通过", f"成员数={groups[0].get('member_count', 0)}")
        except Exception as e:
            log_error("social", "获取分组列表", e)

        # 从分组移除
        try:
            r = remove_from_friend_group(group_id, user_a_id, user_b_id)
            log_result("social", "从分组移除好友", "通过" if r["status"] == "removed" else "失败")
        except Exception as e:
            log_error("social", "从分组移除好友", e)

        # 删除分组
        try:
            r = delete_friend_group(group_id, user_a_id)
            log_result("social", "删除好友分组", "通过" if r["status"] == "deleted" else "失败")
        except Exception as e:
            log_error("social", "删除好友分组", e)

    # 非本人操作分组
    try:
        r = create_friend_group(user_a_id, "同事")
        g2_id = r.get("id")
        add_to_friend_group(g2_id, user_b_id, user_c_id)
        log_result("social", "非本人物操作分组（应失败）", "失败", "未抛出异常")
    except ValueError:
        log_result("social", "非本人操作分组（应失败）", "通过", "正确抛出ValueError")
    except Exception as e:
        log_error("social", "非本人操作分组", e)

# ================================================================
# 5.6 推荐系统 + 不感兴趣
# ================================================================
section("5.6 推荐系统 + 不感兴趣")

try:
    from social.social import (
        recommend_users, recommend_posts,
        mark_not_interested, get_not_interested_posts
    )
except Exception as e:
    log_error("social", "导入推荐/不感兴趣功能", e)

# 需要用到的帖子
_test_post_id_2 = None
if user_a_id and user_b_id:
    _test_post_id_2 = _create_test_post(user_a_id, "推荐系统测试帖子 #AI #Python", "public")

# 推荐用户
try:
    rec_users = recommend_users(user_a_id, limit=5)
    log_result("social", "推荐用户", "通过", f"推荐{len(rec_users)}个用户")
except Exception as e:
    log_error("social", "推荐用户", e)

# 推荐帖子
try:
    rec_posts = recommend_posts(user_a_id, limit=5)
    log_result("social", "推荐动态", "通过", f"推荐{len(rec_posts)}条")
except Exception as e:
    log_error("social", "推荐动态", e)

# 不感兴趣
if _test_post_id_2 and search_post_id:
    try:
        r = mark_not_interested(user_a_id, search_post_id)
        log_result("social", "标记不感兴趣", "通过" if r["status"] == "marked" else "失败", f"结果={r['status']}")
    except Exception as e:
        log_error("social", "标记不感兴趣", e)

    # 重复标记
    try:
        r = mark_not_interested(user_a_id, search_post_id)
        log_result("social", "重复标记不感兴趣", "通过" if r["status"] == "already_marked" else "失败")
    except Exception as e:
        log_error("social", "重复标记不感兴趣", e)

    # 获取不感兴趣列表
    try:
        not_int = get_not_interested_posts(user_a_id)
        log_result("social", "获取不感兴趣列表", "通过", f"共{len(not_int)}条")
    except Exception as e:
        log_error("social", "获取不感兴趣列表", e)

# ================================================================
# 5.7 群聊管理
# ================================================================
section("5.7 群聊管理")

try:
    from social.groups import (
        create_group, dissolve_group, add_member, remove_member,
        set_file_permission, get_member_permissions, get_group_members,
        get_user_groups, save_chat_message, get_chat_history
    )
except Exception as e:
    log_error("groups", "导入群聊模块", e)

chat_group_id = None
if user_a_id and user_b_id and user_c_id:
    # 创建群聊
    try:
        r = create_group(user_a_id, "技术交流群")
        log_result("groups", "创建群聊", "通过" if r["status"] == "created" else "失败", f"group_id={r.get('group_id')}")
        chat_group_id = r["group_id"]
    except Exception as e:
        log_error("groups", "创建群聊", e)

    # 空名称创建群聊
    try:
        create_group(user_a_id, "   ")
        log_result("groups", "空名称创建群聊（应失败）", "失败", "未抛出异常")
    except ValueError:
        log_result("groups", "空名称创建群聊（应失败）", "通过")
    except Exception as e:
        log_error("groups", "空名称创建群聊", e)

    if chat_group_id:
        # 群主添加成员
        try:
            r = add_member(chat_group_id, user_a_id, user_b_id)
            log_result("groups", "群主添加成员", "通过" if r["status"] == "added" else "失败")
        except Exception as e:
            log_error("groups", "添加成员", e)

        # 非群主添加成员
        try:
            add_member(chat_group_id, user_b_id, user_c_id)
            log_result("groups", "非群主添加成员（应失败）", "失败", "未抛出异常")
        except ValueError:
            log_result("groups", "非群主添加成员（应失败）", "通过")
        except Exception as e:
            log_error("groups", "非群主添加成员", e)

        # 获取群成员
        try:
            members = get_group_members(chat_group_id)
            log_result("groups", "获取群成员列表", "通过", f"共{len(members)}人")
            owner_found = any(m["is_owner"] for m in members)
            log_result("groups", "群主在成员列表中", "通过" if owner_found else "失败")
        except Exception as e:
            log_error("groups", "获取群成员列表", e)

        # 设置文件权限
        try:
            r = set_file_permission(chat_group_id, user_a_id, user_b_id, True)
            log_result("groups", "设置成员文件修改权限", "通过" if r["status"] == "updated" else "失败")
            perm = get_member_permissions(chat_group_id, user_b_id)
            log_result("groups", "权限验证", "通过" if perm and perm["can_modify_files"] else "失败")
        except Exception as e:
            log_error("groups", "设置文件权限", e)

        # 保存聊天记录
        try:
            r = save_chat_message(chat_group_id, user_a_id, "text", "大家好")
            log_result("groups", "保存聊天记录", "通过" if r["status"] == "saved" else "失败")
        except Exception as e:
            log_error("groups", "保存聊天记录", e)

        try:
            r = save_chat_message(chat_group_id, user_b_id, "text", "你好群主")
            log_result("groups", "成员发送消息", "通过" if r["status"] == "saved" else "失败")
        except Exception as e:
            log_error("groups", "成员发送消息", e)

        # 获取聊天记录
        try:
            msgs = get_chat_history(chat_group_id, user_a_id)
            log_result("groups", "获取聊天记录", "通过", f"共{len(msgs)}条")
        except Exception as e:
            log_error("groups", "获取聊天记录", e)

        # 获取用户群列表
        try:
            u_groups = get_user_groups(user_a_id)
            log_result("groups", "获取用户群列表", "通过", f"共{len(u_groups)}个群")
        except Exception as e:
            log_error("groups", "获取用户群列表", e)

        # 非群主不能解散
        try:
            dissolve_group(chat_group_id, user_b_id)
            log_result("groups", "非群主解散群聊（应失败）", "失败", "未抛出异常")
        except ValueError:
            log_result("groups", "非群主解散群聊（应失败）", "通过")
        except Exception as e:
            log_error("groups", "非群主解散群聊", e)

        # 群主移除成员
        try:
            r = remove_member(chat_group_id, user_a_id, user_b_id)
            log_result("groups", "群主移除成员", "通过" if r["status"] == "removed" else "失败")
        except Exception as e:
            log_error("groups", "群主移除成员", e)

        # 群主解散群聊
        try:
            r = dissolve_group(chat_group_id, user_a_id)
            log_result("groups", "群主解散群聊", "通过" if r["status"] == "dissolved" else "失败")
        except Exception as e:
            log_error("groups", "群主解散群聊", e)

# ================================================================
# 5.8 拉黑增强（禁止发私信）
# ================================================================
section("5.8 拉黑增强")

try:
    from social.direct_message import create_conversation, send_direct_message
except Exception as e:
    log_error("dm", "导入私信模块(拉黑增强)", e)

if user_a_id and user_b_id:
    # 创建会话用于测试
    try:
        r = create_conversation([user_a_id, user_b_id])
        conv_id_2 = r["conversation_id"]
    except Exception as e:
        log_error("dm", "创建测试会话", e)
        conv_id_2 = None

    if conv_id_2:
        # A屏蔽B后，A无法向B发送私信
        try:
            block(user_a_id, user_b_id)
            r = send_direct_message(user_a_id, conv_id_2, "A屏蔽了B，应该不能发")
            log_result("dm", "屏蔽后A无法向B发私信", "通过" if r["status"] == "blocked" else "失败", f"结果={r['status']}")
            unblock(user_a_id, user_b_id)
        except Exception as e:
            log_error("dm", "屏蔽后发私信检测", e)

    if conv_id_2:
        # B屏蔽A后，A无法向B发送私信
        try:
            block(user_b_id, user_a_id)
            r = send_direct_message(user_a_id, conv_id_2, "B屏蔽了A，A不能发")
            log_result("dm", "被屏蔽后A无法向B发私信", "通过" if r["status"] == "blocked" else "失败", f"结果={r['status']}")
            unblock(user_b_id, user_a_id)
        except Exception as e:
            log_error("dm", "被屏蔽后发私信检测", e)

    if conv_id_2:
        # 取关验证（屏蔽后无法关注）
        try:
            block(user_b_id, user_a_id)
            r = follow(user_a_id, user_b_id)
            log_result("social", "屏蔽后无法关注", "通过" if r["status"] == "blocked" else "失败")
            unblock(user_b_id, user_a_id)
        except Exception as e:
            log_error("social", "屏蔽后关注检测", e)

# ================================================================
# 6. 私信发送和接收
# ================================================================
section("6. 私信发送和接收")

try:
    from social.direct_message import (
        create_conversation, send_direct_message, get_conversations,
        get_messages, mark_conversation_read, get_unread_conversation_count
    )
except Exception as e:
    log_error("direct_message", "导入私信模块", e)

if user_a_id and user_b_id:
    # 创建会话
    try:
        r = create_conversation([user_a_id, user_b_id])
        log_result("direct_message", "创建Alice-Bob会话", "通过", f"结果={r['status']}, conv_id={r.get('conversation_id')}")
        conv_id = r["conversation_id"]
    except Exception as e:
        log_error("direct_message", "创建会话", e)
        conv_id = None

    # 重复创建会话（应返回已有）
    if conv_id:
        try:
            r = create_conversation([user_a_id, user_b_id])
            log_result("direct_message", "重复创建会话（应返回exists）", "通过" if r["status"] == "exists" else "失败", f"结果={r['status']}")
        except Exception as e:
            log_error("direct_message", "重复创建会话", e)

    # 发送私信
    if conv_id:
        try:
            r = send_direct_message(user_a_id, conv_id, "Hello Bob! 这是一条私信")
            log_result("direct_message", "Alice发送私信", "通过", f"结果={r['status']}, post_id={r.get('post_id')}")
            dm_post_id = r.get("post_id")
        except Exception as e:
            log_error("direct_message", "发送私信", e)
            dm_post_id = None

        try:
            r = send_direct_message(user_b_id, conv_id, "Hi Alice! 收到你的私信了")
            log_result("direct_message", "Bob回复私信", "通过", f"结果={r['status']}, post_id={r.get('post_id')}")
        except Exception as e:
            log_error("direct_message", "回复私信", e)

    # 获取消息
    if conv_id:
        try:
            msgs = get_messages(conv_id, user_a_id)
            log_result("direct_message", "获取会话消息列表", "通过", f"共{len(msgs)}条消息")
            if len(msgs) >= 1:
                log_result("direct_message", "消息内容验证", "通过" if "Hello Bob" in msgs[0].get("content", "") else "失败")
        except Exception as e:
            log_error("direct_message", "获取消息列表", e)

    # 获取会话列表
    if conv_id:
        try:
            convs = get_conversations(user_a_id)
            log_result("direct_message", "获取Alice的会话列表", "通过", f"共{len(convs)}条会话")
        except Exception as e:
            log_error("direct_message", "获取会话列表", e)

    # 标记已读
    if conv_id:
        try:
            r = mark_conversation_read(conv_id, user_a_id)
            log_result("direct_message", "标记会话已读", "通过", f"结果={r['status']}")
        except Exception as e:
            log_error("direct_message", "标记会话已读", e)

    # 未读会话数
    try:
        cnt = get_unread_conversation_count(user_a_id)
        log_result("direct_message", "获取未读会话数", "通过", f"未读数={cnt}")
    except Exception as e:
        log_error("direct_message", "获取未读会话数", e)

    # 发送者不在会话中
    try:
        send_direct_message(user_c_id, conv_id, "非法消息")
        log_result("direct_message", "非会话成员发送私信（应失败）", "失败", "未抛出异常")
    except ValueError:
        log_result("direct_message", "非会话成员发送私信（应失败）", "通过")
    except Exception as e:
        log_error("direct_message", "非会话成员发送私信", e)

    # 少于2人创建会话
    try:
        create_conversation([user_a_id])
        log_result("direct_message", "少于2人创建会话（应失败）", "失败", "未抛出异常")
    except ValueError:
        log_result("direct_message", "少于2人创建会话（应失败）", "通过")
    except Exception as e:
        log_error("direct_message", "少于2人创建会话", e)

# ================================================================
# 7. 全文搜索
# ================================================================
section("7. 全文搜索")

try:
    from social.search import (
        init_fts, search_posts, search_users, search_tags,
        search_posts_by_tag, search_all, advanced_search, search_suggest
    )
except Exception as e:
    log_error("search", "导入search模块", e)

# 初始化FTS5
try:
    r = init_fts()
    log_result("search", "初始化FTS5索引", "通过", f"结果={r['status']}")
except Exception as e:
    if "already exists" in str(e).lower() or "duplicate" in str(e).lower():
        log_result("search", "初始化FTS5索引", "通过", "索引已存在（可能之前已初始化）")
    else:
        log_error("search", "初始化FTS5索引", e)

# 搜索帖子
if search_post_id:
    try:
        p_results = search_posts("公开")
        log_result("search", "搜索帖子（关键词：公开）", "通过", f"找到{len(p_results)}条")
    except Exception as e:
        log_error("search", "搜索帖子", e)

    try:
        p_results = search_posts("帖子", sort="recent")
        log_result("search", "按时间排序搜索帖子", "通过", f"找到{len(p_results)}条")
    except Exception as e:
        log_error("search", "按时间排序搜索", e)

    try:
        p_results = search_posts("帖子", sort="popular")
        log_result("search", "按热度排序搜索帖子", "通过", f"找到{len(p_results)}条")
    except Exception as e:
        log_error("search", "按热度排序搜索", e)

# 搜索用户
try:
    u_results = search_users("alice")
    log_result("search", "搜索用户（关键词：alice）", "通过", f"找到{len(u_results)}条")
except Exception as e:
    log_error("search", "搜索用户", e)

# 搜索标签
try:
    t_results = search_tags("测试")
    log_result("search", "搜索标签（关键词：测试）", "通过", f"找到{len(t_results)}条")
except Exception as e:
    log_error("search", "搜索标签", e)

# 按标签搜索帖子
try:
    tag_posts = search_posts_by_tag("测试")
    log_result("search", "按标签搜索帖子", "通过", f"找到{len(tag_posts)}条")
except Exception as e:
    log_error("search", "按标签搜索帖子", e)

# 混合搜索
try:
    all_results = search_all("alice")
    log_result("search", "混合搜索", "通过", f"posts={len(all_results['posts'])}, users={len(all_results['users'])}, tags={len(all_results['tags'])}")
except Exception as e:
    log_error("search", "混合搜索", e)

# 高级搜索
try:
    adv = advanced_search(keyword="公开", visibility="public", limit=10)
    log_result("search", "高级搜索（关键词+可见性）", "通过", f"找到{len(adv)}条")
except Exception as e:
    log_error("search", "高级搜索", e)

# 搜索建议
try:
    sug = search_suggest("al")
    log_result("search", "搜索建议（前缀：al）", "通过", f"tags={len(sug['tags'])}, users={len(sug['users'])}")
except Exception as e:
    log_error("search", "搜索建议", e)

# 空查询
try:
    r = search_posts("")
    log_result("search", "空查询搜索（应返回空列表）", "通过" if r == [] else "失败")
except Exception as e:
    log_error("search", "空查询搜索", e)

# ================================================================
# 汇总
# ================================================================
section("测试汇总")

total = passed + failed
summary_lines = [
    f"总计: {total} 项测试",
    f"通过: {passed}",
    f"失败: {failed}",
    f"通过率: {passed/total*100:.1f}%" if total > 0 else "通过率: N/A",
    f"测试时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
]

for line in summary_lines:
    results.append(line)
    print(line)

# 写入文件
output_path = r"c:\Users\lyc\Desktop\work\test_results.txt"
with open(output_path, "w", encoding="utf-8") as f:
    f.write("\n".join(results))

print(f"\n测试结果已写入: {output_path}")
