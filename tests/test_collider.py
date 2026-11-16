#!/usr/bin/env python3
"""Automated verification tests for ExactCollider cross-platform package."""

import json
import subprocess
import sys
import tempfile
from pathlib import Path

import trimesh

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from exact_collider.core import ColliderConfig, process_model
from exact_collider.path_parser import _split_dragged_tokens, resolve_dropped_models


def make_concave_arch() -> trimesh.Trimesh:
    left = trimesh.creation.box(extents=[0.4, 0.6, 1.2])
    left.apply_translation([-0.6, 0.0, 0.6])
    right = trimesh.creation.box(extents=[0.4, 0.6, 1.2])
    right.apply_translation([0.6, 0.0, 0.6])
    top = trimesh.creation.box(extents=[1.6, 0.6, 0.4])
    top.apply_translation([0.0, 0.0, 1.4])
    return trimesh.util.concatenate([left, right, top])


def test_path_parser_cross_platform():
    # macOS backslash-escaped spaces + trailing space
    assert _split_dragged_tokens(r"/Users/me/My\ Models/arch\ 01.glb ") == [
        "/Users/me/My Models/arch 01.glb"
    ]
    # Linux single-quoted & file:// URI
    assert _split_dragged_tokens("'/home/me/My Models/hero.obj' file:///tmp/a%20b.stl") == [
        "/home/me/My Models/hero.obj",
        "/tmp/a b.stl",
    ]
    # Windows PowerShell drag-and-drop `& 'C:\...'` and CMD `"C:\..."`
    assert _split_dragged_tokens(r"& 'C:\Users\Me\My Models\ship.glb'") == [
        r"C:\Users\Me\My Models\ship.glb"
    ]
    assert _split_dragged_tokens(r'"C:\Users\Me\a.glb" "D:\b.obj"') == [
        r"C:\Users\Me\a.glb",
        r"D:\b.obj",
    ]
    print("PASS test_path_parser_cross_platform")


def test_trimesh_and_coacd_and_interactive_repl():
    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        spaced_dir = tmp_dir / "My Models"
        spaced_dir.mkdir()
        arch_path = spaced_dir / "my arch.glb"
        torus_path = spaced_dir / "torus.obj"

        make_concave_arch().export(str(arch_path))
        trimesh.creation.torus(major_radius=1.0, minor_radius=0.3, major_sections=24, minor_sections=16).export(str(torus_path))

        # 1. Trimesh zero-error
        out_tm = tmp_dir / "arch_tm.glb"
        res = process_model(arch_path, out_tm, ColliderConfig(mode="trimesh"))
        assert res["metrics"]["mean_surface_error"] == 0.0
        assert res["metrics"]["max_surface_error"] == 0.0

        # 2. CoACD ultra-tight
        out_cx = tmp_dir / "torus_cx.glb"
        res2 = process_model(torus_path, out_cx, ColliderConfig(mode="convex", threshold=0.015, max_convex_hull=64))
        assert res2["metrics"]["all_hulls_convex"] is True
        assert res2["metrics"]["collider_count"] >= 4

        # 3. Dropped path resolution with backslash-escaped spaces
        escaped = str(arch_path).replace(" ", r"\ ")
        models, warnings = resolve_dropped_models(escaped + " ")
        assert not warnings
        assert models == [arch_path.resolve()]

        # 4. Interactive REPL test (drop file with escaped spaces, then quit)
        proc = subprocess.run(
            ["exact-collider"],
            input=f"mode trimesh\n{escaped}\nq\n",
            capture_output=True,
            text=True,
            check=True,
        )
        expected_out = arch_path.with_name("my arch_collider.glb")
        assert expected_out.exists(), f"Interactive mode did not create {expected_out}"

        # 5. Direct CLI --json test
        cli_proc = subprocess.run(
            ["exact-collider", str(torus_path), "-m", "convex", "--preset", "high", "--json"],
            capture_output=True,
            text=True,
            check=True,
        )
        data = json.loads(cli_proc.stdout)
        assert Path(data["output"]).exists()

        print("PASS test_trimesh_and_coacd_and_interactive_repl")


if __name__ == "__main__":
    test_path_parser_cross_platform()
    test_trimesh_and_coacd_and_interactive_repl()
    print("ALL CROSS-PLATFORM TESTS PASSED")
