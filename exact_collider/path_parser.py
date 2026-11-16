"""Cross-platform parser for file/folder paths dragged into a terminal window."""

from __future__ import annotations

import os
import re
from pathlib import Path
from urllib.parse import unquote, urlparse

from .core import SUPPORTED_EXTENSIONS


def _is_windows_drive_path(token: str) -> bool:
    return len(token) >= 2 and token[1] == ":" and token[0].isalpha()


def _uri_to_path(uri: str) -> str:
    parsed = urlparse(uri)
    raw_path = unquote(parsed.path)
    # Windows file:///C:/path -> C:/path
    if os.name == "nt" and len(raw_path) >= 3 and raw_path[0] == "/" and raw_path[2] == ":":
        raw_path = raw_path[1:]
    elif len(raw_path) >= 3 and raw_path[0] == "/" and raw_path[2] == ":":
        raw_path = raw_path[1:]
    return raw_path


def _split_dragged_tokens(raw_input: str) -> list[str]:
    """
    Split a terminal line containing one or more dragged file paths.
    Handles:
      - Windows PowerShell `& 'C:\\...'` or `"C:\\..."`
      - macOS/Linux backslash-escaped spaces `/path/my\\ file.glb`
      - Single/double quotes `'...'` / `"..."`
      - `file://...` URIs
    """
    text = raw_input.strip()
    if not text:
        return []

    # Strip leading PowerShell call operator `& `
    if text.startswith("& ") or text.startswith("&\t"):
        text = text[1:].lstrip()

    tokens: list[str] = []
    buf: list[str] = []
    quote: str | None = None
    i = 0
    n = len(text)

    while i < n:
        ch = text[i]

        if quote is not None:
            if ch == quote:
                quote = None
            else:
                buf.append(ch)
            i += 1
            continue

        if ch in ("'", '"'):
            quote = ch
            i += 1
            continue

        # Backslash escape on POSIX-style paths (e.g. "\ ", "\(", "\)")
        # Avoid breaking Windows UNC/drive backslashes like C:\Users\...
        if ch == "\\" and i + 1 < n:
            next_ch = text[i + 1]
            current = "".join(buf)
            is_win = _is_windows_drive_path(current) or current.startswith("\\\\")
            if not is_win and next_ch in (" ", "\t", "'", '"', "(", ")", "[", "]", "&", ";", "\\"):
                buf.append(next_ch)
                i += 2
                continue

        if ch in (" ", "\t", "\r", "\n"):
            if buf:
                tokens.append("".join(buf))
                buf.clear()
            i += 1
            continue

        buf.append(ch)
        i += 1

    if buf:
        tokens.append("".join(buf))

    cleaned: list[str] = []
    for tok in tokens:
        if tok == "&":
            continue
        if tok.startswith("file://"):
            tok = _uri_to_path(tok)
        cleaned.append(tok)
    return cleaned


def resolve_dropped_models(raw_input: str) -> tuple[list[Path], list[str]]:
    """
    Parse dragged terminal text into existing 3D model Paths.
    Returns (valid_model_paths, warnings).
    """
    tokens = _split_dragged_tokens(raw_input)
    models: list[Path] = []
    warnings: list[str] = []
    seen: set[Path] = set()

    for tok in tokens:
        p = Path(tok).expanduser()
        if not p.exists():
            warnings.append(f"路径不存在: {tok}")
            continue

        if p.is_dir():
            found = sorted(
                f for f in p.rglob("*")
                if f.is_file() and f.suffix.lower() in SUPPORTED_EXTENSIONS
                and "_collider" not in f.stem
            )
            if not found:
                warnings.append(f"文件夹内未找到支持的 3D 模型: {p}")
            for f in found:
                rf = f.resolve()
                if rf not in seen:
                    seen.add(rf)
                    models.append(rf)
        elif p.is_file():
            if p.suffix.lower() not in SUPPORTED_EXTENSIONS:
                warnings.append(f"不支持的格式 ({p.suffix}): {p.name}")
                continue
            rf = p.resolve()
            if rf not in seen:
                seen.add(rf)
                models.append(rf)

    return models, warnings
