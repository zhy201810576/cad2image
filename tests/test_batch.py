"""batch 模块的批处理与部分失败测试。"""

from __future__ import annotations

from pathlib import Path

import pytest

from cad2image.batch import process_directory
from cad2image.config import RenderOptions


def test_empty_directory_returns_empty_result(tmp_path: Path) -> None:
    """空目录批处理返回空结果而非报错。"""
    input_dir = tmp_path / "in"
    input_dir.mkdir()
    result = process_directory(input_dir, tmp_path / "out", RenderOptions())
    assert result.success_count == 0
    assert result.failure_count == 0


def test_partial_failure_does_not_abort_batch(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """单个文件渲染失败不中断整批，返回成功与失败明细。"""
    input_dir = tmp_path / "in"
    input_dir.mkdir()
    (input_dir / "a.dwg").write_bytes(b"DWG")
    (input_dir / "b.dwg").write_bytes(b"DWG")
    output_dir = tmp_path / "out"

    def fake_convert(source_dir: Path, target_dir: Path, **kwargs: object) -> list[Path]:
        target = Path(target_dir)
        (target / "a.dxf").write_text("DXF")
        (target / "b.dxf").write_text("DXF")
        return []

    def fake_render(dxf_path: Path, output_path: Path, options: RenderOptions) -> Path:
        if Path(dxf_path).stem == "b":
            raise RuntimeError("render boom")
        Path(output_path).write_bytes(b"PNG")
        return Path(output_path)

    monkeypatch.setattr("cad2image.batch.convert_directory", fake_convert)
    monkeypatch.setattr("cad2image.batch.render_dxf", fake_render)

    result = process_directory(input_dir, output_dir, RenderOptions())

    assert result.success_count == 1
    assert result.failure_count == 1
    assert result.successes[0].name == "a.png"
    assert result.failures[0].source.name == "b.dwg"
    assert result.failures[0].stage == "render"


def test_missing_dxf_marks_convert_failure(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """转换后缺少对应 DXF 的文件标记为 convert 阶段失败。"""
    input_dir = tmp_path / "in"
    input_dir.mkdir()
    (input_dir / "a.dwg").write_bytes(b"DWG")
    (input_dir / "b.dwg").write_bytes(b"DWG")

    def fake_convert(source_dir: Path, target_dir: Path, **kwargs: object) -> list[Path]:
        # 仅生成 a.dxf，b 转换失败
        (Path(target_dir) / "a.dxf").write_text("DXF")
        return []

    def fake_render(dxf_path: Path, output_path: Path, options: RenderOptions) -> Path:
        Path(output_path).write_bytes(b"PNG")
        return Path(output_path)

    monkeypatch.setattr("cad2image.batch.convert_directory", fake_convert)
    monkeypatch.setattr("cad2image.batch.render_dxf", fake_render)

    result = process_directory(input_dir, tmp_path / "out", RenderOptions())

    assert result.success_count == 1
    assert result.failure_count == 1
    assert result.failures[0].source.name == "b.dwg"
    assert result.failures[0].stage == "convert"


def test_invalid_output_format_raises(tmp_path: Path) -> None:
    """非法输出格式 fail-fast 抛出 ValueError。"""
    input_dir = tmp_path / "in"
    input_dir.mkdir()
    with pytest.raises(ValueError, match="不支持的输出格式"):
        process_directory(input_dir, tmp_path / "out", RenderOptions(), output_format="jpg")
