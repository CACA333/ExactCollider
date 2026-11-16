"""
ExactCollider Cross-Platform CLI (Windows / Linux / macOS)
Run `exact-collider` with no arguments for Interactive Terminal Drag-and-Drop mode,
or pass file/folder paths and flags for direct CLI / batch execution.
"""

from __future__ import annotations

import argparse
import json
import platform
import sys
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from . import __version__
from .core import SUPPORTED_EXTENSIONS, ColliderConfig, process_model
from .path_parser import resolve_dropped_models


PRESETS = {
    "ultra": {"threshold": 0.01, "max_convex_hull": 128, "resolution": 3000, "mcts_iterations": 200},
    "high": {"threshold": 0.025, "max_convex_hull": 64, "resolution": 2500, "mcts_iterations": 150},
    "balanced": {"threshold": 0.05, "max_convex_hull": 32, "resolution": 2000, "mcts_iterations": 120},
}

console = Console()


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="exact-collider",
        description="ExactCollider (Windows / Linux / macOS): 零误差 & 极致贴合 3D 碰撞体生成工具",
    )
    p.add_argument(
        "inputs",
        nargs="*",
        help="输入 3D 模型文件或文件夹；留空则进入终端拖拽交互模式",
    )
    p.add_argument("-o", "--output", help="指定输出路径（仅单文件模式有效）")
    p.add_argument(
        "-m",
        "--mode",
        choices=["trimesh", "convex", "both"],
        default="convex",
        help="碰撞模式: trimesh (静态1:1零误差) | convex (CoACD动态凸分解) | both (双模式)",
    )
    p.add_argument(
        "--preset",
        choices=["ultra", "high", "balanced"],
        default="ultra",
        help="凸分解贴合预设: ultra(分毫不离0.01) | high(高精度0.025) | balanced(标准0.05)",
    )
    p.add_argument("--threshold", type=float, default=None, help="自定义 CoACD 凹度阈值 (0.005 ~ 0.2)")
    p.add_argument("--max-hulls", type=int, default=None, help="最大凸包数量 (8 ~ 256)")
    p.add_argument("--resolution", type=int, default=None, help="体素采样分辨率 (1000 ~ 5000)")
    p.add_argument("--simplify", type=float, default=1.0, help="Trimesh 简化比例 (1.0=完全1:1不简化)")
    p.add_argument("--repair", action="store_true", help="对 Trimesh 进行顶点合并与法线修复")
    p.add_argument("--naming", choices=["generic", "godot"], default="generic", help="节点命名规范")
    p.add_argument("--include-visual", action="store_true", help="在导出文件中同时保留原视觉网格")
    p.add_argument("--format", choices=["glb", "obj", "stl"], default="glb", help="输出格式 (默认 glb)")
    p.add_argument("--json", action="store_true", help="以 JSON 格式输出结果")
    p.add_argument("-v", "--version", action="version", version=f"%(prog)s {__version__}")
    return p


def _make_config(
    mode: str,
    preset: str,
    threshold: float | None = None,
    max_hulls: int | None = None,
    resolution: int | None = None,
    simplify: float = 1.0,
    repair: bool = False,
    naming: str = "generic",
    include_visual: bool = False,
) -> ColliderConfig:
    p = PRESETS[preset]
    return ColliderConfig(
        mode=mode,  # type: ignore[arg-type]
        threshold=threshold if threshold is not None else p["threshold"],
        max_convex_hull=max_hulls if max_hulls is not None else p["max_convex_hull"],
        resolution=resolution if resolution is not None else p["resolution"],
        mcts_iterations=p["mcts_iterations"],
        simplify_ratio=simplify,
        repair_trimesh=repair,
        naming_style=naming,  # type: ignore[arg-type]
        include_visual=include_visual,
    )


def _print_result_rich(res: dict) -> None:
    m = res["metrics"]
    table = Table(show_header=False, box=None, padding=(0, 2))
    table.add_row("[bold green]输出文件[/bold green]", str(res["output"]))
    table.add_row("模式 / 耗时", f"{m['mode']}  |  {m['elapsed_seconds']}s")
    table.add_row("原模型", f"{m['input_vertices']} 顶点, {m['input_faces']} 三角面")
    table.add_row("碰撞体", f"{m['collider_count']} 个组件, {m['collider_vertices']} 顶点, {m['collider_faces']} 三角面")
    table.add_row("表面平均 / 最大误差", f"{m['mean_surface_error']} / {m['max_surface_error']}")
    if m["volume_ratio"] is not None:
        table.add_row("体积贴合度", f"{m['volume_ratio'] * 100:.2f}%")
    if m["all_hulls_convex"] is not None:
        status = "[green]通过 ✓[/green]" if m["all_hulls_convex"] else "[yellow]部分非凸[/yellow]"
        table.add_row("全凸包校验", status)
    console.print(Panel(table, title="[bold cyan]碰撞体生成完成[/bold cyan]", border_style="green"))


def _print_status_bar(mode: str, preset: str, fmt: str, naming: str, include_visual: bool) -> None:
    vis_str = "保留" if include_visual else "仅碰撞体"
    console.print(
        f"[bold]当前配置:[/bold] 模式=[cyan]{mode}[/cyan] | "
        f"精度=[cyan]{preset}[/cyan] | 格式=[cyan].{fmt}[/cyan] | "
        f"命名=[cyan]{naming}[/cyan] | 视觉网格=[cyan]{vis_str}[/cyan]"
    )


def _print_interactive_help() -> None:
    console.print(
        Panel(
            "[bold]交互命令说明:[/bold]\n"
            "  直接把 3D 模型文件或文件夹 [bold cyan]拖入终端窗口[/bold cyan] 并按回车即可生成碰撞体\n\n"
            "  [cyan]mode convex | trimesh | both[/cyan]   切换碰撞模式 (凸分解 / 静态零误差 / 双模式)\n"
            "  [cyan]preset ultra | high | balanced[/cyan] 切换贴合精度 (0.01 / 0.025 / 0.05)\n"
            "  [cyan]format glb | obj | stl[/cyan]         切换导出格式\n"
            "  [cyan]naming generic | godot[/cyan]         切换命名规范 (通用 / Godot 4)\n"
            "  [cyan]visual[/cyan]                         切换导出时是否保留原视觉网格\n"
            "  [cyan]h[/cyan] / [cyan]help[/cyan]                       显示帮助\n"
            "  [cyan]q[/cyan] / [cyan]quit[/cyan]                       退出程序",
            border_style="blue",
        )
    )


def interactive_loop(
    mode: str = "convex",
    preset: str = "ultra",
    fmt: str = "glb",
    naming: str = "generic",
    include_visual: bool = False,
) -> int:
    """Interactive terminal REPL supporting file/folder drag-and-drop."""
    sys_name = f"{platform.system()} ({platform.machine()})"
    console.print(
        Panel(
            f"[bold cyan]ExactCollider v{__version__}[/bold cyan]  ·  {sys_name}\n"
            "支持格式: .glb / .gltf / .obj / .stl / .ply / .off\n"
            "把模型文件或文件夹 [bold green]直接拖入终端窗口[/bold green] 后按回车，即可自动套上紧贴碰撞体！",
            border_style="cyan",
        )
    )
    _print_status_bar(mode, preset, fmt, naming, include_visual)
    console.print("[dim]提示: 输入 h 查看切换模式/格式命令，输入 q 退出[/dim]\n")

    while True:
        try:
            raw = console.input("[bold green]拖入模型或输入命令 > [/bold green]")
        except (EOFError, KeyboardInterrupt):
            console.print("\n[dim]已退出 ExactCollider[/dim]")
            return 0

        cmd = raw.strip()
        if not cmd:
            continue

        lower = cmd.lower()
        if lower in ("q", "quit", "exit"):
            console.print("[dim]已退出 ExactCollider[/dim]")
            return 0

        if lower in ("h", "help", "?"):
            _print_interactive_help()
            continue

        if lower == "visual":
            include_visual = not include_visual
            _print_status_bar(mode, preset, fmt, naming, include_visual)
            continue

        parts = lower.split()
        if len(parts) == 2 and parts[0] == "mode" and parts[1] in ("convex", "trimesh", "both"):
            mode = parts[1]
            _print_status_bar(mode, preset, fmt, naming, include_visual)
            continue
        if len(parts) == 2 and parts[0] == "preset" and parts[1] in PRESETS:
            preset = parts[1]
            _print_status_bar(mode, preset, fmt, naming, include_visual)
            continue
        if len(parts) == 2 and parts[0] == "format" and parts[1] in ("glb", "obj", "stl"):
            fmt = parts[1]
            _print_status_bar(mode, preset, fmt, naming, include_visual)
            continue
        if len(parts) == 2 and parts[0] == "naming" and parts[1] in ("generic", "godot"):
            naming = parts[1]
            _print_status_bar(mode, preset, fmt, naming, include_visual)
            continue

        models, warnings = resolve_dropped_models(cmd)
        for w in warnings:
            console.print(f"[yellow]⚠ {w}[/yellow]")

        if not models:
            if not warnings:
                console.print("[yellow]未识别到有效模型路径，请重新拖入文件或输入 h 查看帮助[/yellow]")
            continue

        cfg = _make_config(mode=mode, preset=preset, naming=naming, include_visual=include_visual)
        for model_path in models:
            out_path = model_path.with_name(f"{model_path.stem}_collider.{fmt}")
            with console.status(f"[cyan]正在为 {model_path.name} 生成碰撞体 ({mode}/{preset})...[/cyan]"):
                try:
                    res = process_model(model_path, out_path, cfg)
                    _print_result_rich(res)
                except Exception as exc:
                    console.print(f"[red]❌ {model_path.name} 处理失败: {exc}[/red]")

    return 0


def _collect_cli_models(raw_inputs: list[str]) -> list[Path]:
    models: list[Path] = []
    seen: set[Path] = set()
    for item in raw_inputs:
        p = Path(item).expanduser()
        if not p.exists():
            raise FileNotFoundError(f"输入路径不存在: {item}")
        if p.is_dir():
            for f in sorted(p.rglob("*")):
                if f.is_file() and f.suffix.lower() in SUPPORTED_EXTENSIONS and "_collider" not in f.stem:
                    rf = f.resolve()
                    if rf not in seen:
                        seen.add(rf)
                        models.append(rf)
        else:
            rf = p.resolve()
            if rf not in seen:
                seen.add(rf)
                models.append(rf)
    return models


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    # No positional input -> launch Interactive Terminal Drag-and-Drop mode
    if not args.inputs:
        return interactive_loop(
            mode=args.mode,
            preset=args.preset,
            fmt=args.format,
            naming=args.naming,
            include_visual=args.include_visual,
        )

    try:
        models = _collect_cli_models(args.inputs)
    except FileNotFoundError as exc:
        if args.json:
            print(json.dumps({"error": str(exc)}, ensure_ascii=False))
        else:
            print(f"❌ {exc}", file=sys.stderr)
        return 1

    if not models:
        msg = "未找到支持的 3D 模型文件"
        if args.json:
            print(json.dumps({"error": msg}, ensure_ascii=False))
        else:
            print(f"❌ {msg}", file=sys.stderr)
        return 1

    if args.output and len(models) > 1:
        msg = "--output 仅支持单个输入文件；批量处理请省略 -o"
        if args.json:
            print(json.dumps({"error": msg}, ensure_ascii=False))
        else:
            print(f"❌ {msg}", file=sys.stderr)
        return 1

    cfg = _make_config(
        mode=args.mode,
        preset=args.preset,
        threshold=args.threshold,
        max_hulls=args.max_hulls,
        resolution=args.resolution,
        simplify=args.simplify,
        repair=args.repair,
        naming=args.naming,
        include_visual=args.include_visual,
    )

    results = []
    for model_path in models:
        out_path = Path(args.output).expanduser().resolve() if args.output else model_path.with_name(f"{model_path.stem}_collider.{args.format}")
        try:
            res = process_model(model_path, out_path, cfg)
            results.append(res)
            if not args.json:
                _print_result_rich(res)
        except Exception as exc:
            if args.json:
                print(json.dumps({"error": str(exc), "input": str(model_path)}, ensure_ascii=False))
            else:
                console.print(f"[red]❌ {model_path.name} 处理失败: {exc}[/red]")
            return 1

    if args.json:
        print(json.dumps(results[0] if len(results) == 1 else results, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
