"""plotstyle 模块的 CTB 加载与查询测试。"""

from __future__ import annotations

from pathlib import Path

import pytest
from ezdxf.addons import acadctb

from cad2image.plotstyle import get_lineweight, load_ctb


@pytest.fixture
def sample_ctb(tmp_path: Path) -> Path:
    """生成一个把颜色 1 线宽设为 0.8mm 的合成 CTB。"""
    ctb = acadctb.new_ctb()
    ctb[1].set_lineweight(0.8)
    path = tmp_path / "test.ctb"
    ctb.save(str(path))
    return path


def test_load_ctb_roundtrip(sample_ctb: Path) -> None:
    """加载 CTB 返回 ColorDependentPlotStyles 且保留线宽覆盖。"""
    styles = load_ctb(sample_ctb)
    assert isinstance(styles, acadctb.ColorDependentPlotStyles)


def test_get_lineweight_override(sample_ctb: Path) -> None:
    """颜色 1 被覆盖为 0.8mm。"""
    styles = load_ctb(sample_ctb)
    assert get_lineweight(styles, 1) == pytest.approx(0.8)


def test_get_lineweight_no_override_returns_none(sample_ctb: Path) -> None:
    """未覆盖的颜色返回 None（沿用对象线宽）。"""
    styles = load_ctb(sample_ctb)
    assert get_lineweight(styles, 2) is None


def test_load_ctb_missing_raises(tmp_path: Path) -> None:
    """CTB 文件不存在时抛出 FileNotFoundError。"""
    with pytest.raises(FileNotFoundError):
        load_ctb(tmp_path / "missing.ctb")


def test_get_lineweight_invalid_aci_raises(sample_ctb: Path) -> None:
    """ACI 越界时抛出 ValueError。"""
    styles = load_ctb(sample_ctb)
    with pytest.raises(ValueError):
        get_lineweight(styles, 0)
