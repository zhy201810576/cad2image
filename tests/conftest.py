"""pytest 共享 fixture。"""

from __future__ import annotations

from pathlib import Path

import ezdxf
import pytest


@pytest.fixture
def sample_dxf(tmp_path: Path) -> Path:
    """生成一个含直线/圆/圆弧/多段线的合成 DXF，用于渲染测试。"""
    doc = ezdxf.new("R2018")
    msp = doc.modelspace()
    msp.add_line((0, 0), (100, 0))
    msp.add_line((100, 0), (100, 50))
    msp.add_circle((50, 25), 20)
    msp.add_arc((50, 25), 30, 0, 270)
    msp.add_lwpolyline([(0, 0), (20, 40), (60, 40), (100, 0)])
    path = tmp_path / "sample.dxf"
    doc.saveas(path)
    return path


@pytest.fixture
def empty_dxf(tmp_path: Path) -> Path:
    """生成一个空模型空间的 DXF，用于测试空布局边界。"""
    doc = ezdxf.new("R2018")
    path = tmp_path / "empty.dxf"
    doc.saveas(path)
    return path


@pytest.fixture
def solid_dxf(tmp_path: Path) -> Path:
    """生成含 SOLID 图元（填充三角形，尺寸箭头形态）的 DXF。

    用于回归测试 PyMuPDF 1.24.11 无法解析 ezdxf Vec2 的 bug——SOLID 会走
    ``draw_filled_polygon`` → ``Shape.draw_polyline`` 路径。
    """
    doc = ezdxf.new("R2018")
    msp = doc.modelspace()
    msp.add_solid([(0, 0), (10, 0), (5, 10), (5, 10)])
    path = tmp_path / "solid.dxf"
    doc.saveas(path)
    return path
