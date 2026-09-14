"""DWG → DXF 转换：ODA File Converter 封装。

ODA File Converter 是**目录级**转换工具：一次调用把输入目录内全部文件转换为指定
格式输出到输出目录。本模块负责处理「单文件 → 临时目录 → 收集输出 → 清理」的编排，
以及目录级批量转换，全部用 ``tempfile`` + ``with`` 管理临时资源。
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

from cad2image.config import get_oda_converter_path

# ODA File Converter 的输入版本（对 DWG 而言通常自动识别，指定为近年版本即可）。
DEFAULT_INPUT_VERSION = "ACAD2018"
# 输出版本为 "DXF" 表示输出 ASCII DXF（与 Acme 迁移方案一致）。
DEFAULT_OUTPUT_VERSION = "DXF"

# 单次转换的超时（秒），避免复杂文件导致进程挂死。
_CONVERSION_TIMEOUT_SECONDS = 300


def convert_dwg_to_dxf(
    dwg_path: str | Path,
    output_dir: str | Path,
    *,
    oda_path: str | Path | None = None,
    input_version: str = DEFAULT_INPUT_VERSION,
    output_version: str = DEFAULT_OUTPUT_VERSION,
) -> Path:
    """把单个 DWG 文件转换为 DXF。

    由于 ODA FC 是目录级工具，本函数会把目标 DWG 复制到临时输入目录，转换后从
    临时输出目录收集生成的 DXF 到 ``output_dir``，最后清理临时目录。

    Args:
        dwg_path: 源 DWG 文件路径。
        output_dir: 生成的 DXF 的存放目录。
        oda_path: ODA File Converter 可执行文件路径，缺省时从环境/默认路径解析。
        input_version: 输入版本，默认 ``ACAD2018``。
        output_version: 输出版本，默认 ``DXF``。

    Returns:
        生成的 DXF 文件路径。

    Raises:
        FileNotFoundError: 当源 DWG 或 ODA 可执行文件不存在时。
        RuntimeError: 当转换失败（ODA 退出码非零或未生成 DXF）时。
    """
    source = Path(dwg_path)
    if not source.is_file():
        raise FileNotFoundError(f"源 DWG 文件不存在：{source}")

    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)

    oda_exe = Path(oda_path) if oda_path else get_oda_converter_path()
    if not oda_exe.is_file():
        raise FileNotFoundError(f"ODA File Converter 可执行文件不存在：{oda_exe}")

    with tempfile.TemporaryDirectory(prefix="oda_in_") as in_dir, tempfile.TemporaryDirectory(
        prefix="oda_out_"
    ) as out_dir:
        _copy_source_to_input(source, Path(in_dir))
        _run_converter(
            oda_exe,
            input_dir=Path(in_dir),
            output_dir=Path(out_dir),
            input_version=input_version,
            output_version=output_version,
        )
        return _collect_single_dxf(Path(out_dir), source.stem, destination)


def convert_directory(
    input_dir: str | Path,
    output_dir: str | Path,
    *,
    oda_path: str | Path | None = None,
    input_version: str = DEFAULT_INPUT_VERSION,
    output_version: str = DEFAULT_OUTPUT_VERSION,
    recursive: bool = False,
) -> list[Path]:
    """把输入目录内全部 DWG 转换为 DXF。

    直接调用 ODA FC 的目录级能力，一次转换整个目录，效率优于逐文件转换。

    Args:
        input_dir: 包含 DWG 文件的输入目录。
        output_dir: DXF 输出目录。
        oda_path: ODA File Converter 可执行文件路径，缺省时从环境/默认路径解析。
        input_version: 输入版本，默认 ``ACAD2018``。
        output_version: 输出版本，默认 ``DXF``。
        recursive: 是否递归处理子目录。

    Returns:
        生成的 DXF 文件路径列表。

    Raises:
        FileNotFoundError: 当输入目录或 ODA 可执行文件不存在时。
        RuntimeError: 当 ODA 转换失败时。
    """
    source_dir = Path(input_dir)
    if not source_dir.is_dir():
        raise FileNotFoundError(f"输入目录不存在：{source_dir}")

    target_dir = Path(output_dir)
    target_dir.mkdir(parents=True, exist_ok=True)

    oda_exe = Path(oda_path) if oda_path else get_oda_converter_path()
    if not oda_exe.is_file():
        raise FileNotFoundError(f"ODA File Converter 可执行文件不存在：{oda_exe}")

    _run_converter(
        oda_exe,
        input_dir=source_dir,
        output_dir=target_dir,
        input_version=input_version,
        output_version=output_version,
        recursive=recursive,
    )
    return sorted(target_dir.glob("**/*.dxf") if recursive else target_dir.glob("*.dxf"))


def _copy_source_to_input(source: Path, input_dir: Path) -> None:
    """把单个源 DWG 复制到临时输入目录。"""
    shutil.copy2(source, input_dir / source.name)


def _run_converter(
    oda_exe: Path,
    *,
    input_dir: Path,
    output_dir: Path,
    input_version: str,
    output_version: str,
    recursive: bool = False,
) -> None:
    """调用 ODA File Converter 命令行执行转换。"""
    command = [
        str(oda_exe),
        str(input_dir),
        str(output_dir),
        input_version,
        output_version,
        "1" if recursive else "0",
        "1",  # audit：转换前审计文件
    ]
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=_CONVERSION_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"ODA File Converter 转换超时（>{_CONVERSION_TIMEOUT_SECONDS}s）：{input_dir}") from exc

    if completed.returncode != 0:
        stderr = (completed.stderr or "").strip()
        raise RuntimeError(f"ODA File Converter 转换失败（退出码 {completed.returncode}）：{stderr or '未知错误'}")


def _collect_single_dxf(output_dir: Path, stem: str, destination: Path) -> Path:
    """从临时输出目录收集生成的 DXF 并移动到目标目录。"""
    generated = output_dir / f"{stem}.dxf"
    if not generated.is_file():
        raise RuntimeError(f"ODA 未生成预期的 DXF 文件：{stem}.dxf")
    target = destination / generated.name
    shutil.move(str(generated), str(target))
    return target
