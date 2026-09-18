"""路由包。``current_services`` 是唯一的依赖取出点。

放在这里而不是 ``app.py``：``app.py`` 要 include 各个路由模块，路由模块再
import ``app`` 就成环；依赖函数属于「路由层怎么拿到服务」，放在路由包的
``__init__`` 里方向是干净的。
"""

from __future__ import annotations

from fastapi import Request

from ...svc import Services


def current_services(request: Request) -> Services:
    return request.app.state.services


__all__ = ["current_services"]
