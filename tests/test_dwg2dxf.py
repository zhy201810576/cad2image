"""dwg2dxf 模块的 ODA 封装测试。

仅覆盖错误路径与命令构造（通过 mock subprocess），真实 DWG 转换属于集成测试，
在 ``tests/test_integration.py`` 中以标记跳过。
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from cad2image.dwg2dxf import convert_dwg_to_dxf


def test_missing_source_raises(tmp_path: Path) -> None:
    """源 DWG 不存在时抛出 FileNotFoundError。"""
    with pytest.raises(FileNotFoundError, match="源 DWG"):
        convert_dwg_to_dxf(tmp_path / "missing.dwg", tmp_path / "out")


def test_missing_oda_raises(tmp_path: Path) -> None:
    """ODA 可执行文件不存在时抛出 FileNotFoundError。"""
    dwg = tmp_path / "part.dwg"
    dwg.write_bytes(b"DWG")
    with pytest.raises(FileNotFoundError, match="ODA File Converter"):
        convert_dwg_to_dxf(dwg, tmp_path / "out", oda_path=tmp_path / "no-oda.exe")


def test_command_construction(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """验证 ODA 命令行的构造（目录级参数顺序）。"""
    dwg = tmp_path / "part.dwg"
    dwg.write_bytes(b"DWG")
    oda = tmp_path / "ODAFileConverter.exe"
    oda.write_bytes(b"EXE")

    captured: dict[str, list[str]] = {}

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        captured["command"] = command
        output_dir = Path(command[2])
        (output_dir / "part.dxf").write_text("DXF")
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr("cad2image.dwg2dxf.subprocess.run", fake_run)

    result = convert_dwg_to_dxf(dwg, tmp_path / "out", oda_path=oda)

    command = captured["command"]
    assert command[0] == str(oda)
    assert command[3] == "ACAD2018"  # 输入版本
    assert command[4] == "DXF"  # 输出版本
    assert command[5] == "0"  # 非递归
    assert command[6] == "1"  # 审计
    assert result.name == "part.dxf"
    assert result.is_file()


def test_conversion_failure_raises(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """ODA 退出码非零时抛出 RuntimeError。"""
    dwg = tmp_path / "part.dwg"
    dwg.write_bytes(b"DWG")
    oda = tmp_path / "ODAFileConverter.exe"
    oda.write_bytes(b"EXE")

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(command, 2, "", "ODA failed")

    monkeypatch.setattr("cad2image.dwg2dxf.subprocess.run", fake_run)

    with pytest.raises(RuntimeError, match="转换失败"):
        convert_dwg_to_dxf(dwg, tmp_path / "out", oda_path=oda)
