"""端到端集成测试：真实 DWG → DXF → PNG。

需要真实 DWG 文件与 ODA File Converter，默认跳过。通过以下方式启用：

    CAD_TEST_DWG=E:/path/to/轴套.dwg pytest tests/test_integration.py -m integration
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from cad2image.batch import process_dwg
from cad2image.config import RenderOptions
from cad2image.dwg2dxf import convert_dwg_to_dxf

pytestmark = pytest.mark.integration

# 项目根目录（tests/ 的上一级）。
_REPO_ROOT = Path(__file__).resolve().parents[1]

# ODA File Converter 候选安装路径（按版本从新到旧），命中第一个存在的。
_ODA_CANDIDATES = (
    Path(r"D:\ODAFileConverter 27.9.0\ODAFileConverter.exe"),
    Path(r"D:\ODA\ODAFileConverter_title 21.5.0\ODAFileConverter.exe"),
)


@pytest.fixture
def test_dwg() -> Path:
    """从环境变量读取测试 DWG，未设置则跳过。"""
    raw = os.getenv("CAD_TEST_DWG")
    if not raw:
        pytest.skip("未设置 CAD_TEST_DWG 环境变量，跳过集成测试")
    path = Path(raw)
    if not path.is_file():
        pytest.skip(f"测试 DWG 不存在：{path}")
    return path


def _locate_oda() -> Path:
    """解析 ODA File Converter 路径（环境变量优先，候选路径兜底）。"""
    raw = os.getenv("ODA_FILE_CONVERTER_PATH")
    if raw:
        path = Path(raw)
        if path.is_file():
            return path
    for candidate in _ODA_CANDIDATES:
        if candidate.is_file():
            return candidate
    pytest.skip("未找到 ODA File Converter（ODA_FILE_CONVERTER_PATH 或候选路径）")


def _locate_902_dwg() -> Path:
    """定位 90-2.dwg（优先 CAD_TEST_DWG，否则取根目录 CAD问题图纸/90-2.dwg）。"""
    raw = os.getenv("CAD_TEST_DWG")
    if raw:
        path = Path(raw)
        if path.is_file():
            return path
    default = _REPO_ROOT / "CAD问题图纸" / "90-2.dwg"
    if default.is_file():
        return default
    pytest.skip("未找到 90-2.dwg（设置 CAD_TEST_DWG 或放置于 CAD问题图纸/）")


def test_dwg_to_png_pipeline(test_dwg: Path, tmp_path: Path) -> None:
    """真实 DWG 走通 DWG→DXF→PNG 管线，输出非空。"""
    output = process_dwg(test_dwg, tmp_path, RenderOptions(dpi=200), output_format="png")
    assert output.is_file()
    assert output.stat().st_size > 0


def test_902_tolerance_dimensions_use_cjk_font(tmp_path: Path) -> None:
    """90-2.dwg 的公差尺寸标注应重指到思源宋体，堆叠不换行（回归）。

    回归：0.1.30 曾把带 ``\\S`` 公差堆叠的 DIMENSION 几何块 MTEXT 重指到等宽
    NotoSansMono，以免"公差换行"。实证思源宋体同样能正确紧凑堆叠上下偏差（上下偏差
    齐全、不拆两行），故改为统一用思源宋体，与主尺寸数字字体一致、避免观感割裂。
    这里用真实 90-2.dwg 走 DWG→DXF，校验三处公差尺寸（Ø158 / Ø143.95 / Ø60）的几何块
    文字均重指到思源宋体，并渲染 PNG 非空。
    """
    import ezdxf
    from ezdxf.entities import Dimension

    from cad2image.render import _OPEN_CJK_FONT, _configure_fonts, _remap_dimension_geometry_texts

    dwg = _locate_902_dwg()
    dxf = convert_dwg_to_dxf(dwg, tmp_path, oda_path=_locate_oda())
    doc = ezdxf.readfile(dxf)
    _configure_fonts(None)
    _remap_dimension_geometry_texts(doc)

    tol_fonts: list[str] = []
    for layout in doc.layouts:
        for entity in layout:
            if not isinstance(entity, Dimension):
                continue
            block = entity.get_geometry_block()
            if block is None:
                continue
            for block_entity in block:
                if block_entity.dxftype() == "MTEXT" and "\\S" in block_entity.dxf.get("text", ""):
                    style = doc.styles.get(block_entity.dxf.get("style", ""))
                    tol_fonts.append(style.dxf.get("font", "") if style is not None else "")

    # 90-2 有且仅有三处带公差的直径尺寸。
    assert len(tol_fonts) == 3, tol_fonts
    assert all(font == _OPEN_CJK_FONT for font in tol_fonts), tol_fonts

    # 渲染 PNG 非空（走真实管线，确认不崩溃）。
    from cad2image.render import render_dxf

    png = render_dxf(dxf, tmp_path / "90-2.png", RenderOptions(dpi=200))
    assert png.is_file()
    assert png.stat().st_size > 0
