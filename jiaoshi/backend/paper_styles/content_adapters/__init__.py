# -*- coding: utf-8 -*-
"""
内容适配器模块
每个地域适配器负责为试卷添加本地特色内容
"""
from backend.paper_styles.content_adapters.adapter_base import BaseContentAdapter
from backend.paper_styles.content_adapters.beijing_adapter import BeijingAdapter
from backend.paper_styles.content_adapters.shanghai_adapter import ShanghaiAdapter
from backend.paper_styles.content_adapters.guangdong_adapter import GuangdongAdapter

_ADAPTERS = {
    "beijing": BeijingAdapter,
    "shanghai": ShanghaiAdapter,
    "guangdong": GuangdongAdapter,
}


def get_adapter(region: str) -> BaseContentAdapter:
    """根据地域获取内容适配器"""
    adapter_cls = _ADAPTERS.get(region, BaseContentAdapter)
    return adapter_cls()
