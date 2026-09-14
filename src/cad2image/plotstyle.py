"""打印样式（CTB/STB）加载。

对应 Acme 的 ``/pw`` 参数（打印样式表）。ezdxf 1.1.x 已内置 CTB/STB 解析器
（``ezdxf.addons.acadctb``），渲染时通过 ``RenderContext(ctb=path)`` 自动应用
线宽/颜色/灰度覆盖；本模块仅提供面向用户的加载与查询封装。
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional, cast

from ezdxf.addons import acadctb

__all__ = ["load_ctb", "get_lineweight"]


def load_ctb(ctb_path: str | Path) -> acadctb.ColorDependentPlotStyles:
    """加载 CTB 颜色相关打印样式表。

    Args:
        ctb_path: CTB 样式表文件路径。

    Returns:
        ``ColorDependentPlotStyles`` 实例，可通过 ``styles[aci]`` 或
        ``styles.get_lineweight(aci)`` 访问颜色索引对应的打印属性。

    Raises:
        FileNotFoundError: 当 CTB 文件不存在时。
    """
    source = Path(ctb_path)
    if not source.is_file():
        raise FileNotFoundError(f"CTB 样式表不存在：{source}")
    return cast(acadctb.ColorDependentPlotStyles, acadctb.load(str(source)))


def get_lineweight(styles: acadctb.ColorDependentPlotStyles, aci: int) -> float | None:
    """返回颜色索引 ``aci`` 的打印线宽（mm）。

    Args:
        styles: 由 :func:`load_ctb` 返回的样式表。
        aci: AutoCAD 颜色索引（1-255）。

    Returns:
        打印线宽（mm）；当该颜色未在 CTB 中覆盖线宽（即沿用对象线宽）时返回 ``None``。

    Raises:
        ValueError: 当 ``aci`` 超出合法范围时。
    """
    if not 1 <= aci <= 255:
        raise ValueError(f"ACI 颜色索引必须在 1-255 之间，得到 {aci}")
    return cast(Optional[float], styles.get_lineweight(aci))
