"""CLI 入口的回归测试。"""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from cad2image.cli import app

runner = CliRunner()


def test_render_dxf_via_cli(sample_dxf: Path, tmp_path: Path) -> None:
    """通过 CLI 渲染合成 DXF，退出码 0 且产物存在。"""
    output = tmp_path / "out.png"
    result = runner.invoke(app, [str(sample_dxf), "-o", str(output), "--dpi", "120"])
    assert result.exit_code == 0, result.output
    assert output.is_file()


def test_missing_input_returns_error(tmp_path: Path) -> None:
    """输入文件不存在时退出码非零。"""
    result = runner.invoke(app, [str(tmp_path / "missing.dxf"), "-o", str(tmp_path / "out.png")])
    assert result.exit_code != 0


def test_unsupported_suffix_returns_error(sample_dxf: Path, tmp_path: Path) -> None:
    """输出格式非法时退出码非零。"""
    result = runner.invoke(app, [str(sample_dxf), "-o", str(tmp_path / "out.jpg")])
    assert result.exit_code != 0
