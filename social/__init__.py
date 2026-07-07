"""
社交网络平台 - 核心业务模块

├── db.py            数据库连接 & 事务管理
├── direct_message.py 私信 / 会话管理
├── models.py        用户 & 帖子 CRUD 辅助函数
├── notification.py  通知创建 / 查询 / 已读 / 聚合 / WebPush 推送
├── search.py        全文搜索（FTS5）/ 标签搜索 / 用户搜索 / 高级搜索
└── social.py        关注 / 取关 / 屏蔽 / 静音 / 域名屏蔽
"""

from social.db import get_conn, close_conn, transactional
