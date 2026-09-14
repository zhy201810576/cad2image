"""命令行入口（typer）。

支持三种输入：

- 单个 DXF 文件 → 直接渲染为 PNG/SVG
- 单个 DWG 文件 → 先经 ODA 转 DXF，再渲染
- 目录 → 批量处理（部分失败不中断整批）
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import typer

from cad2image.batch import BatchResult, process_directory
from cad2image.config import RenderOptions
from cad2image.dwg2dxf import convert_dwg_to_dxf
from cad2image.render import render_dxf

app = typer.Typer(
    name="cad2image",
    help="DWG/DXF → PNG/SVG 渲染管线（ODA File Converter + ezdxf）。",
    add_completion=False,
)

# 进程退出码语义（对齐 Acme 报告：0 成功 / 1 通用失败）。
_EXIT_OK = 0
_EXIT_ERROR = 1


@app.command()
def render(
    input_path: Path = typer.Argument(..., exists=True, help="输入：DWG/DXF 文件，或包含 DWG 的目录"),
    output: Path = typer.Option(None, "--output", "-o", help="输出文件/目录；缺省与输入同目录"),
    dpi: int = typer.Option(300, "--dpi", help="PNG 分辨率（DPI）"),
    output_format: str = typer.Option("png", "--format", "-f", help="输出格式：png / svg"),
    background: str = typer.Option("white", "--background", help="背景策略：default/white/black/off"),
    color: str = typer.Option("color", "--color", help="颜色策略：color/monochrome/grayscale/black/white"),
    lineweight: str = typer.Option("absolute", "--lineweight", help="线宽策略：absolute/relative"),
    lineweight_scaling: float = typer.Option(1.0, "--lineweight-scaling", help="线宽整体缩放系数（仅绝对线宽生效）"),
    min_lineweight: float = typer.Option(None, "--min-lineweight", help="最小打印线宽（mm）"),
    relative_max_stroke_width: float = typer.Option(0.001, "--relative-max-stroke-width", help="相对线宽：最粗线宽占页面较小边比例（默认 0.1%）"),
    relative_min_stroke_width: float = typer.Option(0.05, "--relative-min-stroke-width", help="相对线宽：最细线宽占最粗线宽比例（默认 5%）"),
    ctb: str = typer.Option("", "--ctb", help="CTB 打印样式表路径"),
    font_dir: str = typer.Option("", "--font-dir", help="附加的 SHX/TTF 字体目录"),
    layout: str = typer.Option(None, "--layout", help="布局名；缺省渲染模型空间"),
    width: float = typer.Option(None, "--width", help="页面宽度（mm）"),
    height: float = typer.Option(None, "--height", help="页面高度（mm）"),
    fit: bool = typer.Option(False, "--fit", help="按内容包围盒自适应页面尺寸"),
    margin: float = typer.Option(3.0, "--margin", help="内容自适应页面时的四周余量（%，相对内容较小边）"),
    recursive: bool = typer.Option(False, "--recursive", help="目录批量时递归处理子目录"),
    oda_path: Path = typer.Option(None, "--oda-path", help="ODAFileConverter.exe 路径；缺省从环境变量/默认路径解析"),
) -> None:
    """把 DWG/DXF 渲染为 PNG 或 SVG。"""
    options = RenderOptions(
        dpi=dpi,
        background=background,
        color_policy=color,
        lineweight_policy=lineweight,
        lineweight_scaling=lineweight_scaling,
        min_lineweight=min_lineweight,
        relative_max_stroke_width=relative_max_stroke_width,
        relative_min_stroke_width=relative_min_stroke_width,
        ctb=ctb,
        font_dir=font_dir,
        layout_name=layout,
        width_mm=width,
        height_mm=height,
        fit_to_extents=fit,
        margin=margin,
    )
    try:
        _run(input_path, output, options, output_format, recursive, oda_path)
    except (FileNotFoundError, ValueError, RuntimeError, NotImplementedError) as exc:
        typer.echo(f"错误：{exc}", err=True)
        raise typer.Exit(code=_EXIT_ERROR) from exc


def _run(
    input_path: Path,
    output: Path | None,
    options: RenderOptions,
    output_format: str,
    recursive: bool,
    oda_path: Path | None,
) -> None:
    """根据输入类型分发到单文件或目录批处理。"""
    if input_path.is_dir():
        _run_batch(input_path, output or input_path, options, output_format, recursive, oda_path)
        return

    if input_path.suffix.lower() == ".dxf":
        target = output or input_path.with_suffix(f".{output_format}")
        render_dxf(input_path, target, options)
        typer.echo(f"已生成：{target}")
        return

    if input_path.suffix.lower() == ".dwg":
        _run_single_dwg(input_path, output, options, output_format, oda_path)
        return

    raise ValueError(f"不支持的文件类型 '{input_path.suffix}'，仅支持 .dwg / .dxf")


def _run_single_dwg(
    input_path: Path,
    output: Path | None,
    options: RenderOptions,
    output_format: str,
    oda_path: Path | None,
) -> None:
    """处理单个 DWG：临时目录转换 + 渲染到指定输出。"""
    target = output or input_path.with_suffix(f".{output_format}")
    with tempfile.TemporaryDirectory(prefix="oda_cli_") as dxf_dir:
        dxf = convert_dwg_to_dxf(input_path, dxf_dir, oda_path=oda_path)
        render_dxf(dxf, target, options)
    typer.echo(f"已生成：{target}")


def _run_batch(
    input_dir: Path,
    output_dir: Path,
    options: RenderOptions,
    output_format: str,
    recursive: bool,
    oda_path: Path | None,
) -> None:
    """批量处理目录，打印汇总报告；存在失败时返回非零退出码。"""
    result = process_directory(
        input_dir,
        output_dir,
        options,
        output_format=output_format,
        recursive=recursive,
        oda_path=oda_path,
    )
    _print_report(result)
    if result.failure_count > 0:
        raise typer.Exit(code=_EXIT_ERROR)


def _print_report(result: BatchResult) -> None:
    """打印批处理汇总，失败明细输出到 stderr。"""
    typer.echo(f"完成：成功 {result.success_count}，失败 {result.failure_count}")
    for failure in result.failures:
        typer.echo(f"  失败[{failure.stage}] {failure.source.name}: {failure.error}", err=True)


if __name__ == "__main__":
    app()
