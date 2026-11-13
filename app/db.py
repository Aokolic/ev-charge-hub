"""数据库连接与会话管理模块（全组公共底座）。

采用“数据库先行”原则：表结构、视图、存储过程与触发器统一由 sql/ 脚本维护，
本模块仅负责初始化 SQLAlchemy 2.0 连接池、会话工厂及请求级事务生命周期管理。
"""

# 1. 导入区与配置引入
from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import settings

# 2. 创建数据库引擎（全局单例连接池，连接串与 SQL 日志开关统一来自 config.py）
engine = create_engine(
    settings.DATABASE_URL,
    echo=settings.SQL_ECHO,  # 开发调试时在 .env 设置 SQL_ECHO=true 可打印底层 SQL
    pool_pre_ping=True,      # 每次借出连接前先探活，防止 MySQL wait_timeout 断连导致报错
    pool_recycle=3600,       # 连接存活满 1 小时主动回收重建，避免长连接老化
    pool_size=10,            # 连接池常驻连接数
    max_overflow=20,         # 高并发时允许在 pool_size 之外额外创建的临时连接上限
)

# 3. 会话工厂与 ORM 映射基类
# autoflush=False: 避免查询前隐式刷新半成品对象，由 DAO 显式控制写入时机
# autocommit=False: 关闭自动提交，确保多表业务（如核销+支付）处于显式事务控制下
SessionLocal = sessionmaker(autoflush=False, autocommit=False, bind=engine)


class Base(DeclarativeBase):
    """全组 ORM 模型基类（app/models/*.py 统一继承）。

    注意：严禁在任何启动脚本中调用 Base.metadata.create_all(bind=engine)，
    所有建表与约束变更均以 sql/01_ddl.sql 为唯一真源。
    """

    pass


# 4. FastAPI 请求级数据库会话依赖
def get_db() -> Generator[Session, None, None]:
    """为每个 HTTP 请求分配独立的数据库 Session，并在结束时自动清理。

    用法：在 FastAPI 路由参数中注入 `db: Session = Depends(get_db)`。
    """
    db = SessionLocal()
    try:
        yield db          # 将会话交给路由与 DAO 使用（写操作需由业务层显式 db.commit()）
    except Exception:
        db.rollback()     # 请求处理途中发生异常时强制回滚，防止脏事务残留进连接池
        raise             # 原样向上抛出异常，交由 FastAPI 异常处理或 HTTP 错误映射返回
    finally:
        db.close()        # 无论请求成功或失败，最终都安全归还连接到连接池
