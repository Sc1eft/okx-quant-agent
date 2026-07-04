#!/usr/bin/env python3
"""
生成项目代码映射索引 CODE_MAP.md

为 LLM 和开发者提供快速代码定位：
- 每个文件中的 类/函数/关键变量 及其行号
- 模块用途一句话摘要
- 导出符号一览

用法:
    python scripts/generate_code_map.py
    默认在项目根目录生成 CODE_MAP.md
"""
from __future__ import annotations

import ast
import os
import sys
from pathlib import Path
from typing import Optional

REPO_ROOT = Path(__file__).resolve().parent.parent

# 排除的目录/文件
EXCLUDE_DIRS = {
    "__pycache__", ".git", ".venv", "venv", "env", "node_modules",
    "logs", "data", "frontend", "__init__",
}
EXCLUDE_FILES = {
    "CODE_MAP.md",
}

# 本文件无法 parse 的模块（已知兼容性问题）跳过
SKIP_PARSE = set()


def get_module_docstring(body: list[ast.stmt]) -> str:
    """提取模块/类/函数的文档字符串第一行"""
    if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
        doc = body[0].value.value
        if isinstance(doc, str):
            first_line = doc.strip().split("\n")[0]
            return first_line[:120]
    return ""


def describe_function(node: ast.FunctionDef) -> str:
    """从函数名和文档推断用途标签"""
    name = node.name
    if name.startswith("_"):
        visibility = "🔒内部" if name.startswith("__") else "🔐内部"
    else:
        visibility = "🔓公开"

    doc = get_module_docstring(node.body)
    if doc:
        return f"{visibility} {doc}"

    # 从函数名推断
    hints = {
        "get_": "读取",
        "set_": "设置",
        "check_": "检查",
        "validate_": "校验",
        "run": "主循环",
        "stop": "停止",
        "handle_": "处理",
        "build_": "构建",
        "execute_": "执行",
        "_on_": "回调",
        "_check_": "检查",
        "_refresh_": "刷新",
        "on_": "回调",
        "update_": "更新",
        "clear_": "清空",
        "connect": "连接",
        "disconnect": "断开",
    }
    for prefix, hint in hints.items():
        if name.startswith(prefix):
            return f"{visibility} {hint}"

    return visibility


def describe_class(node: ast.ClassDef) -> str:
    doc = get_module_docstring(node.body)
    if doc:
        return doc
    return ""


def extract_file_info(filepath: Path) -> Optional[dict]:
    """提取单个 Python 文件的代码结构"""
    if filepath.name in EXCLUDE_FILES:
        return None
    if filepath.name in SKIP_PARSE:
        return None

    try:
        source = filepath.read_text(encoding="utf-8")
    except Exception:
        return None

    try:
        tree = ast.parse(source, filename=str(filepath))
    except SyntaxError as e:
        return {"path": str(filepath.relative_to(REPO_ROOT)), "error": f"SyntaxError: {e}", "lines": 0, "classes": [], "functions": []}

    lines = source.count("\n")
    module_doc = get_module_docstring(tree.body)

    classes = []
    functions = []
    imports = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imports.append(node.module)

    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            methods = []
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    methods.append({
                        "name": item.name,
                        "line": item.lineno,
                        "description": describe_function(item),
                        "async": isinstance(item, ast.AsyncFunctionDef),
                    })
            classes.append({
                "name": node.name,
                "line": node.lineno,
                "description": describe_class(node),
                "methods": methods,
            })
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            functions.append({
                "name": node.name,
                "line": node.lineno,
                "description": describe_function(node),
                "async": isinstance(node, ast.AsyncFunctionDef),
            })

    # 去重 import（只保留有意义的外部导入）
    imports = sorted(set(imp for imp in imports if "." in imp or imp not in ("typing", "abc", "enum", "dataclasses")))[:20]

    return {
        "path": str(filepath.relative_to(REPO_ROOT)),
        "module_doc": module_doc,
        "lines": lines,
        "imports": imports,
        "classes": classes,
        "functions": functions,
    }


def build_markdown(files: list[dict]) -> str:
    """组装 CODE_MAP.md"""
    lines = []
    lines.append("# 项目代码映射\n")
    lines.append("本文件由 `scripts/generate_code_map.py` 自动生成。\n")
    lines.append("用途：快速定位代码位置，避免读取整个源文件。\n")
    lines.append(f"> 生成时间: {__import__('datetime').datetime.now().strftime('%Y-%m-%d %H:%M')}\n")
    lines.append("---\n")

    # 按目录分组
    by_dir: dict[str, list[dict]] = {}
    for f in files:
        parts = f["path"].split("/")
        if len(parts) > 1:
            d = parts[0]
        else:
            d = "."
        by_dir.setdefault(d, []).append(f)

    for directory in sorted(by_dir):
        lines.append(f"## 📁 {directory}/\n")
        dir_files = sorted(by_dir[directory], key=lambda x: x["path"])

        for f in dir_files:
            if f.get("error"):
                lines.append(f"- ⚠️ **{f['path']}** — {f['error']}\n")
                continue

            badge = ""
            module_doc = f.get("module_doc", "")
            abbrev = f" — {module_doc}" if module_doc else ""
            lines.append(f"### 📄 {f['path']} ({f['lines']}行){abbrev}\n")

            if f["imports"]:
                lines.append(f"  - 导入: `{'`,`'.join(f['imports'][:8])}`\n")

            # 类
            for cls in f["classes"]:
                desc = f" — {cls['description'][:80]}" if cls["description"] else ""
                lines.append(f"  - 🏛️ `{cls['name']}` L{cls['line']}{desc}\n")
                for m in cls["methods"]:
                    async_tag = " async" if m["async"] else ""
                    desc = f" — {m['description']}" if m["description"] else ""
                    lines.append(f"    - └{'async' if m['async'] else ''} `{m['name']}()` L{m['line']}{desc}\n")

            # 顶层函数
            for fn in f["functions"]:
                async_tag = " async" if fn["async"] else ""
                desc = f" — {fn['description']}" if fn["description"] else ""
                lines.append(f"  - ⚡{async_tag} `{fn['name']}()` L{fn['line']}{desc}\n")

            lines.append("\n")

    # 统计
    total_files = len([f for f in files if not f.get("error")])
    error_files = len([f for f in files if f.get("error")])
    total_lines = sum(f.get("lines", 0) for f in files)
    total_classes = sum(len(f.get("classes", [])) for f in files)
    total_functions = sum(len(f.get("functions", [])) for f in files)
    total_methods = sum(
        len(cls["methods"]) for f in files for cls in f.get("classes", [])
    )

    lines.append("---\n")
    lines.append(f"**统计**: {total_files} 文件 | {total_lines} 行代码 | "
                 f"{total_classes} 类 | {total_functions+total_methods} 函数/方法"
                 f"{' | ⚠️ ' + str(error_files) + ' 解析失败' if error_files else ''}\n")

    return "".join(lines)


def main():
    py_files = []
    for root, dirs, files in os.walk(REPO_ROOT):
        # 跳过排除目录
        dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS and not d.startswith(".")]
        for f in sorted(files):
            if f.endswith(".py") and f not in EXCLUDE_FILES:
                py_files.append(Path(root) / f)

    py_files.sort()

    infos = []
    for fp in py_files:
        info = extract_file_info(fp)
        if info:
            infos.append(info)

    markdown = build_markdown(infos)
    output_path = REPO_ROOT / "CODE_MAP.md"
    output_path.write_text(markdown, encoding="utf-8")
    print(f"[OK] CODE_MAP.md generated ({output_path})")
    print(f"    {len(infos)} files, "
          f"{sum(f.get('lines',0) for f in infos)} lines of code")


if __name__ == "__main__":
    main()
