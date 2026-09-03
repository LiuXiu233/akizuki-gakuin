"""秋月学院 · 单 Agent 驱动的日式校园生活 / 恋爱 / TRPG 模拟引擎。

架构原则:
    LLM 负责内容 —— Python 负责规则 —— 存档负责历史。

对外唯一入口是 :mod:`engine.tools`。任何 LLM Agent 都只应通过其中的
工具函数读取和修改世界状态。
"""

__version__ = "1.0.0"
__all__ = ["__version__"]
