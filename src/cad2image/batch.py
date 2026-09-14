"""批量处理：多文件转换与渲染，支持部分失败不中断整批。

批处理遵循「单条失败不中止整批」原则：每个文件独立转换与渲染，异常被捕获并记录，
最终返回包含 ``successes`` / ``failures`` 的结果对象。
"""

from __future__ import annotations

import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from cad2image.config import RenderOptions
from cad2image.dwg2dxf import convert_directory, convert_dwg_to_dxf
from cad2image.render import render_dxf

# 支持输出的图片格式。
_SUPPORTED_FORMATS = {"png", "svg"}


@dataclass
class BatchFailure:
    """单个文件处理失败的信息。

    Attributes:
        source: 源 DWG 文件路径。
        stage: 失败阶段，``convert``（DWG→DXF）或 ``render``（DXF→图片）。
        error: 错误信息。
    """

    source: Path
    stage: str
    error: str


@dataclass
class BatchResult:
    """批处理结果。

    Attributes:
        successes: 成功生成的图片文件路径列表。
        failures: 失败项列表。
    """

    successes: list[Path] = field(default_factory=list)
    failures: list[BatchFailure] = field(default_factory=list)

    @property
    def success_count(self) -> int:
        """成功文件数。"""
        return len(self.successes)

    @property
    def failure_count(self) -> int:
        """失败文件数。"""
        return len(self.failures)


def process_dwg(
    dwg_path: str | Path,
    output_dir: str | Path,
    options: RenderOptions,
    *,
    output_format: str = "png",
    oda_path: str | Path | None = None,
) -> Path:
    """处理单个 DWG：DWG→DXF→图片。

    Args:
        dwg_path: 源 DWG 文件路径。
        output_dir: 图片输出目录。
        options: 渲染参数。
        output_format: 输出格式，``png`` 或 ``svg``。
        oda_path: ODA File Converter 可执行文件路径。

    Returns:
        生成的图片文件路径。

    Raises:
        ValueError: 当 ``output_format`` 不受支持时。
        FileNotFoundError: 当源文件或 ODA 可执行文件不存在时。
        RuntimeError: 当转换或渲染失败时。
    """
    _validate_format(output_format)
    source = Path(dwg_path)
    target_dir = Path(output_dir)
    target_dir.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="oda_single_") as dxf_dir:
        dxf = convert_dwg_to_dxf(source, dxf_dir, oda_path=oda_path)
        output = target_dir / f"{source.stem}.{output_format}"
        render_dxf(dxf, output, options)
        return output


def process_directory(
    input_dir: str | Path,
    output_dir: str | Path,
    options: RenderOptions,
    *,
    output_format: str = "png",
    recursive: bool = False,
    oda_path: str | Path | None = None,
) -> BatchResult:
    """批量处理目录内全部 DWG 文件。

    先一次性目录级转换 DWG→DXF，再逐文件渲染。单文件失败不中断整批。

    Args:
        input_dir: 包含 DWG 的输入目录。
        output_dir: 图片输出目录。
        options: 渲染参数。
        output_format: 输出格式，``png`` 或 ``svg``。
        recursive: 是否递归处理子目录。
        oda_path: ODA File Converter 可执行文件路径。

    Returns:
        包含成功与失败明细的 ``BatchResult``。
    """
    _validate_format(output_format)
    source_dir = Path(input_dir)
    target_dir = Path(output_dir)
    target_dir.mkdir(parents=True, exist_ok=True)

    dwg_files = sorted(source_dir.rglob("*.dwg") if recursive else source_dir.glob("*.dwg"))
    result = BatchResult()
    if not dwg_files:
        return result

    with tempfile.TemporaryDirectory(prefix="oda_batch_") as dxf_dir:
        try:
            convert_directory(source_dir, dxf_dir, oda_path=oda_path, recursive=recursive)
        except (FileNotFoundError, RuntimeError) as exc:
            for dwg in dwg_files:
                result.failures.append(BatchFailure(dwg, "convert", str(exc)))
            return result

        for dwg in dwg_files:
            relative = dwg.relative_to(source_dir)
            dxf = Path(dxf_dir) / relative.with_suffix(".dxf")
            if not dxf.is_file():
                result.failures.append(BatchFailure(dwg, "convert", "未生成 DXF 文件"))
                continue
            try:
                output = target_dir / relative.with_suffix(f".{output_format}")
                output.parent.mkdir(parents=True, exist_ok=True)
                render_dxf(dxf, output, options)
                result.successes.append(output)
            except (FileNotFoundError, ValueError, RuntimeError) as exc:
                result.failures.append(BatchFailure(dwg, "render", str(exc)))

    return result


def _validate_format(output_format: str) -> None:
    """校验输出格式，非法时 fail-fast。"""
    if output_format not in _SUPPORTED_FORMATS:
        raise ValueError(f"不支持的输出格式 '{output_format}'，仅支持：{', '.join(sorted(_SUPPORTED_FORMATS))}")
