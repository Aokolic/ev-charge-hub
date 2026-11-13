"""业务线1：场站与充电设施 —— FastAPI 路由层（示范模板）。

其余 4 条业务线（order / monitor / user / settle）可参照本文件结构编写：
1. 定义 APIRouter(prefix=..., tags=...)；
2. 路由函数通过 Depends(get_db) 注入数据库会话，底层约束/触发器异常由 main.py 自动拦截；
3. 编写完成后在 app/main.py 中通过 app.include_router(...) 挂载即可生效。
"""

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db import get_db

router = APIRouter(prefix="/api/stations", tags=["业务线1-场站与充电设施"])


@router.get("/ping", summary="业务线1路由连通性示例")
def ping_station(db: Session = Depends(get_db)) -> dict[str, str]:
    """示例接口：演示如何注入 get_db 并执行参数化查询。"""
    db_name = db.execute(text("SELECT DATABASE()")).scalar()
    return {"module": "station", "status": "ready", "database": str(db_name)}
