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

pytestmark = pytest.mark.integration


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


def test_dwg_to_png_pipeline(test_dwg: Path, tmp_path: Path) -> None:
    """真实 DWG 走通 DWG→DXF→PNG 管线，输出非空。"""
    output = process_dwg(test_dwg, tmp_path, RenderOptions(dpi=200), output_format="png")
    assert output.is_file()
    assert output.stat().st_size > 0
