# ExactCollider

跨平台（**Windows / Linux / macOS**）3D 模型碰撞体自动生成 CLI 工具。
支持 **静态 1:1 零误差网格 (Trimesh)** 与 **动态刚体极致贴合凸分解 (CoACD)**，打开终端后**直接把模型拖入窗口**即可自动套上分毫不离的碰撞体。

## 安装方法 (Windows / Linux / macOS)

在项目目录下运行（需要 Python 3.9+）：

```bash
pip install .
```

或者使用 `pipx` 全局隔离安装：

```bash
pipx install .
```

安装完成后，任意终端窗口均可直接运行 `exact-collider`（Windows 下为 `exact-collider.exe`）。

## 使用方法

### 1. 终端拖拽交互模式（推荐）
直接输入命令启动：

```bash
exact-collider
```

然后把 `.glb` / `.gltf` / `.obj` / `.stl` / `.ply` 模型文件或整个文件夹 **直接拖入终端窗口并按回车**，程序会自动在原文件同目录生成 `*_collider.glb`（支持切换为 `.obj` / `.stl`）并输出贴合度校验报告。

交互窗口内支持随时输入命令切换参数：
- `mode convex` / `mode trimesh` / `mode both`：切换凸分解 / 静态零误差 / 双模式
- `preset ultra` / `preset high` / `preset balanced`：切换贴合精度（`0.01` / `0.025` / `0.05`）
- `format glb` / `format obj` / `format stl`：切换导出格式
- `naming generic` / `naming godot`：切换通用或 Godot 4 碰撞命名规范
- `visual`：切换是否在导出文件中保留原视觉网格
- `q`：退出

### 2. 命令行批处理模式

```bash
# 动态刚体极致贴合（CoACD 0.01 阈值）
exact-collider model.glb -m convex --preset ultra

# 静态物体 1:1 零误差 Trimesh
exact-collider scene.obj -m trimesh -o scene_col.glb

# 批量处理整个文件夹
exact-collider ./models_folder -m convex --format glb
```

## 打包为独立可执行文件（无需目标机器安装 Python）

在对应系统（Windows / Linux / macOS）上安装 PyInstaller 后执行：

```bash
pip install pyinstaller
pyinstaller --onefile --name exact-collider --collect-all coacd --collect-all manifold3d exact_collider/cli.py
```

生成的单文件可执行程序位于 `dist/exact-collider`（Windows 为 `dist/exact-collider.exe`），可直接双击打开终端窗口拖拽模型使用。
