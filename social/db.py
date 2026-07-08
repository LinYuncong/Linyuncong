"""
数据库连接：将数据保存到磁盘，用SQL高效查询，支持并发，保证数据完整性
事务管理：保证原子性，保证事务前后数据库始终满足约束，并发事务互不干扰，提交事务后永久保存修改
"""
import sqlite3
import threading
DB_PATH = r"c:\Users\lyc\Desktop\work\social.db"#用r防止反斜杠转义出错

_local = threading.local()#各线程不共享数据


def get_conn() -> sqlite3.Connection:#返回一个类型为 sqlite3.Connection 的对象
    """每个线程只会创建一次连接，后续所有调用都复用同一个连接"""
    conn = getattr(_local, "conn", None)#尝试从当前线程的_local对象中获取名为conn的属性。如果该线程尚未建立连接，则返回None
    if conn is None:
        conn = sqlite3.connect(DB_PATH)#建立到DB_PATH指定数据库文件的连接。如果文件不存在，SQLite会自动创建
        conn.execute("PRAGMA journal_mode=WAL")#设置SQLite的日志模式为WAL，允许并发读写，显著提高多线程场景下的性能，同时保证事务的原子性和持久性
        conn.execute("PRAGMA foreign_keys=ON")#启用外键约束，实现级联删除，防止帖子指向不存在的用户
        conn.execute("PRAGMA busy_timeout=5000")#等待锁的超时时间为5s，当一个操作无法立即获得数据库锁时，SQLite会等待这段时间，避免立即抛出database is locked错误
        conn.row_factory = sqlite3.Row#使查询结果的行对象既支持数字索引也支持列名索引（类似字典）
        _local.conn = conn#将新创建的连接存储到线程本地对象中，供当前线程后续复用
    return conn


def close_conn():
    """关闭当前线程的连接"""
    conn = getattr(_local, "conn", None)
    if conn:
        conn.close()
        _local.conn = None


def transactional(fn):
    """装饰器：自动提交事务，发生异常时回滚"""

    def wrapper(*args, **kwargs):#定义内部包装函数，接收任意位置参数和关键字参数
        conn = get_conn()
        try:
            result = fn(*args, **kwargs)#执行被装饰的业务函数
            conn.commit()#成功，提交事务
            return result
        except Exception:
            conn.rollback()#回滚事务，撤销本次调用中所有未提交的修改，保持数据库状态一致
            raise#重新抛出异常，让上层调用者感知错误

    return wrapper#执行follow函数，但实际上执行wrapper函数
