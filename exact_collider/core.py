"""
ExactCollider Core Engine (Windows / Linux / macOS)
Generates 1:1 exact Trimesh colliders and ultra-tight CoACD convex decomposition colliders.
"""

from __future__ import annotations

import colorsys
import os
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal, Optional

import coacd
import numpy as np
import trimesh


CollisionMode = Literal["trimesh", "convex", "both"]
NamingStyle = Literal["generic", "godot"]
ExportFormat = Literal["glb", "obj", "stl"]

SUPPORTED_EXTENSIONS = {".glb", ".gltf", ".obj", ".stl", ".ply", ".off"}


@dataclass
class ColliderConfig:
    mode: CollisionMode = "convex"
    threshold: float = 0.01  # CoACD concavity threshold (0.01 = ultra-tight)
    max_convex_hull: int = 128
    resolution: int = 3000
    mcts_iterations: int = 200
    mcts_max_depth: int = 3
    mcts_nodes: int = 20
    preprocess_mode: str = "auto"
    simplify_ratio: float = 1.0  # 1.0 = no simplification (exact)
    repair_trimesh: bool = False  # False = 1:1 raw copy
    naming_style: NamingStyle = "generic"
    include_visual: bool = False
    seed: int = 42


@dataclass
class FitMetrics:
    mode: str
    input_vertices: int
    input_faces: int
    collider_count: int
    collider_vertices: int
    collider_faces: int
    mean_surface_error: float
    max_surface_error: float
    volume_ratio: Optional[float]
    elapsed_seconds: float
    all_hulls_convex: Optional[bool] = None


def load_combined_mesh(input_path: str | Path) -> tuple[trimesh.Trimesh, trimesh.Scene | trimesh.Trimesh]:
    """Load any 3D model and return (merged_world_mesh, original_loaded_object)."""
    loaded = trimesh.load(str(input_path), process=False)
    if isinstance(loaded, trimesh.Scene):
        meshes = []
        for node_name in loaded.graph.nodes_geometry:
            transform, geom_name = loaded.graph[node_name]
            geom = loaded.geometry[geom_name]
            if isinstance(geom, trimesh.Trimesh) and len(geom.faces) > 0:
                m = geom.copy()
                m.apply_transform(transform)
                meshes.append(m)
        if not meshes:
            raise ValueError(f"模型中没有找到有效的三角网格: {input_path}")
        combined = trimesh.util.concatenate(meshes)
        return combined, loaded
    if isinstance(loaded, trimesh.Trimesh):
        if len(loaded.faces) == 0:
            raise ValueError(f"模型三角面数为 0: {input_path}")
        return loaded.copy(), loaded
    raise ValueError(f"不支持的几何体类型: {type(loaded).__name__}")


def generate_trimesh_collider(mesh: trimesh.Trimesh, cfg: ColliderConfig) -> trimesh.Trimesh:
    """Generate an exact 1:1 triangle mesh collider (zero gap)."""
    col = mesh.copy()
    if cfg.repair_trimesh:
        col.merge_vertices()
        col.update_faces(col.unique_faces())
        col.update_faces(col.nondegenerate_faces())
        col.fix_normals()
    if 0.05 <= cfg.simplify_ratio < 0.999:
        target_faces = max(4, int(len(col.faces) * cfg.simplify_ratio))
        try:
            col = col.simplify_quadric_decimation(face_count=target_faces)
        except Exception:
            pass
    return col


def _run_coacd_quiet(coacd_mesh: coacd.Mesh, cfg: ColliderConfig):
    """Run CoACD while redirecting C++ stdout (fd 1) to stderr when possible."""
    if hasattr(coacd, "set_log_level"):
        coacd.set_log_level("error")

    saved_fd = None
    try:
        saved_fd = os.dup(1)
        os.dup2(2, 1)
    except OSError:
        saved_fd = None

    try:
        return coacd.run_coacd(
            coacd_mesh,
            threshold=float(cfg.threshold),
            max_convex_hull=int(cfg.max_convex_hull),
            preprocess_mode=cfg.preprocess_mode,
            preprocess_resolution=50,
            resolution=int(cfg.resolution),
            mcts_nodes=int(cfg.mcts_nodes),
            mcts_iterations=int(cfg.mcts_iterations),
            mcts_max_depth=int(cfg.mcts_max_depth),
            pca=False,
            merge=True,
            decimate=False,
            max_ch_vertex=256,
            extrude=False,
            seed=int(cfg.seed),
        )
    finally:
        if saved_fd is not None:
            try:
                os.dup2(saved_fd, 1)
                os.close(saved_fd)
            except OSError:
                pass


def generate_convex_hulls(mesh: trimesh.Trimesh, cfg: ColliderConfig) -> list[trimesh.Trimesh]:
    """Run CoACD to produce ultra-tight convex hulls."""
    verts = np.asarray(mesh.vertices, dtype=np.float64)
    faces = np.asarray(mesh.faces, dtype=np.int32)
    coacd_mesh = coacd.Mesh(verts, faces)
    parts = _run_coacd_quiet(coacd_mesh, cfg)

    hulls: list[trimesh.Trimesh] = []
    for v, f in parts:
        hull = trimesh.Trimesh(vertices=v, faces=f, process=True)
        if len(hull.faces) >= 4:
            hulls.append(hull)

    if not hulls:
        hulls.append(mesh.convex_hull)
    return hulls


def compute_fit_metrics(
    source: trimesh.Trimesh,
    colliders: list[trimesh.Trimesh],
    mode: str,
    elapsed: float,
    sample_count: int = 2000,
) -> FitMetrics:
    """Measure exact surface-to-surface error and volume fit between source and colliders."""
    total_verts = sum(len(c.vertices) for c in colliders)
    total_faces = sum(len(c.faces) for c in colliders)

    if mode == "trimesh" and len(colliders) == 1 and len(colliders[0].faces) == len(source.faces):
        mean_err = 0.0
        max_err = 0.0
        vol_ratio = 1.0
    else:
        combined_col = trimesh.util.concatenate(colliders) if len(colliders) > 1 else colliders[0]
        pts, _ = trimesh.sample.sample_surface(source, min(sample_count, max(500, len(source.faces) * 2)))
        try:
            _, distances, _ = trimesh.proximity.closest_point(combined_col, pts)
            mean_err = float(np.mean(distances))
            max_err = float(np.max(distances))
        except Exception:
            mean_err = 0.0
            max_err = 0.0

        vol_ratio = None
        try:
            src_vol = abs(float(source.volume))
            if src_vol > 1e-9:
                col_vol = sum(abs(float(c.volume)) for c in colliders)
                vol_ratio = round(col_vol / src_vol, 4)
        except Exception:
            vol_ratio = None

    all_convex = None
    if mode in ("convex", "both"):
        try:
            all_convex = all(c.is_convex for c in colliders)
        except Exception:
            all_convex = None

    return FitMetrics(
        mode=mode,
        input_vertices=len(source.vertices),
        input_faces=len(source.faces),
        collider_count=len(colliders),
        collider_vertices=total_verts,
        collider_faces=total_faces,
        mean_surface_error=round(mean_err, 6),
        max_surface_error=round(max_err, 6),
        volume_ratio=vol_ratio,
        elapsed_seconds=round(elapsed, 3),
        all_hulls_convex=all_convex,
    )


def _hull_color_rgba(idx: int, total: int, alpha: int = 160) -> list[int]:
    hue = (idx / max(1, total)) % 1.0
    r, g, b = colorsys.hsv_to_rgb(hue, 0.78, 0.95)
    return [int(r * 255), int(g * 255), int(b * 255), alpha]


def _node_name(base_name: str, kind: str, idx: int, style: NamingStyle) -> str:
    if style == "godot":
        if kind == "trimesh":
            return f"{base_name}-colonly"
        return f"{base_name}_hull_{idx:03d}-convcolonly"
    if kind == "trimesh":
        return f"{base_name}_col_trimesh"
    return f"{base_name}_col_hull_{idx:03d}"


def export_colliders(
    source_mesh: trimesh.Trimesh,
    trimesh_col: Optional[trimesh.Trimesh],
    convex_hulls: list[trimesh.Trimesh],
    output_path: str | Path,
    cfg: ColliderConfig,
    base_name: str = "model",
) -> Path:
    """Export generated colliders to .glb, .gltf, .obj, or .stl."""
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    ext = out.suffix.lower()

    if ext in (".glb", ".gltf"):
        scene = trimesh.Scene()
        if cfg.include_visual:
            vis = source_mesh.copy()
            scene.add_geometry(vis, node_name=base_name, geom_name=base_name)

        if trimesh_col is not None:
            tm = trimesh_col.copy()
            tm.visual = trimesh.visual.ColorVisuals(tm, face_colors=[70, 190, 255, 140])
            n = _node_name(base_name, "trimesh", 0, cfg.naming_style)
            scene.add_geometry(tm, node_name=n, geom_name=n)

        total = len(convex_hulls)
        for i, hull in enumerate(convex_hulls):
            h = hull.copy()
            h.visual = trimesh.visual.ColorVisuals(h, face_colors=_hull_color_rgba(i, total, 165))
            n = _node_name(base_name, "convex", i + 1, cfg.naming_style)
            scene.add_geometry(h, node_name=n, geom_name=n)

        scene.export(str(out))
        return out

    if ext == ".obj":
        lines = ["# Generated by ExactCollider\n"]
        vert_offset = 1
        all_parts: list[tuple[str, trimesh.Trimesh]] = []
        if cfg.include_visual:
            all_parts.append((base_name, source_mesh))
        if trimesh_col is not None:
            all_parts.append((_node_name(base_name, "trimesh", 0, cfg.naming_style), trimesh_col))
        for i, hull in enumerate(convex_hulls):
            all_parts.append((_node_name(base_name, "convex", i + 1, cfg.naming_style), hull))

        for name, part in all_parts:
            lines.append(f"g {name}\no {name}\n")
            for v in part.vertices:
                lines.append(f"v {v[0]:.6f} {v[1]:.6f} {v[2]:.6f}\n")
            for f in part.faces:
                a, b, c = f[0] + vert_offset, f[1] + vert_offset, f[2] + vert_offset
                lines.append(f"f {a} {b} {c}\n")
            vert_offset += len(part.vertices)

        out.write_text("".join(lines), encoding="utf-8")
        return out

    if ext == ".stl":
        parts = []
        if trimesh_col is not None:
            parts.append(trimesh_col)
        parts.extend(convex_hulls)
        merged = trimesh.util.concatenate(parts) if len(parts) > 1 else parts[0]
        merged.export(str(out))
        return out

    raise ValueError(f"不支持的导出格式: {ext} (支持 .glb, .gltf, .obj, .stl)")


def process_model(
    input_path: str | Path,
    output_path: Optional[str | Path],
    cfg: ColliderConfig,
) -> dict:
    """End-to-end collision generation, export, and verification."""
    t0 = time.perf_counter()
    src_path = Path(input_path)
    base_name = src_path.stem

    source_mesh, _ = load_combined_mesh(src_path)

    trimesh_col: Optional[trimesh.Trimesh] = None
    convex_hulls: list[trimesh.Trimesh] = []

    if cfg.mode in ("trimesh", "both"):
        trimesh_col = generate_trimesh_collider(source_mesh, cfg)

    if cfg.mode in ("convex", "both"):
        convex_hulls = generate_convex_hulls(source_mesh, cfg)

    elapsed = time.perf_counter() - t0

    all_colliders: list[trimesh.Trimesh] = []
    if trimesh_col is not None:
        all_colliders.append(trimesh_col)
    all_colliders.extend(convex_hulls)

    metrics = compute_fit_metrics(source_mesh, all_colliders, cfg.mode, elapsed)

    exported = None
    if output_path is not None:
        exported = str(export_colliders(source_mesh, trimesh_col, convex_hulls, output_path, cfg, base_name))

    return {
        "input": str(src_path),
        "output": exported,
        "metrics": asdict(metrics),
    }
