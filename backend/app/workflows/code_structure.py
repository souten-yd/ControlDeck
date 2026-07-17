"""取得した公開コードの有限・非実行な構造索引。Deep Researchの根拠補助に使う。"""
from __future__ import annotations

import ast
import re


def _python_structure(path: str, content: str) -> dict:
    try:
        tree = ast.parse(content)
    except (SyntaxError, ValueError):
        return {"path": path, "language": "python", "parse_error": True}
    functions: list[str] = []
    classes: list[str] = []
    variables: list[str] = []
    imports: list[str] = []
    routes: list[str] = []
    calls: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            args = [arg.arg for arg in (*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs)]
            functions.append(f"{'async ' if isinstance(node, ast.AsyncFunctionDef) else ''}{node.name}({', '.join(args)})")
            for decorator in node.decorator_list:
                text = ast.unparse(decorator) if hasattr(ast, "unparse") else ""
                if any(token in text for token in (".get(", ".post(", ".put(", ".patch(", ".delete(", ".websocket(")):
                    routes.append(f"{node.name}: @{text}")
        elif isinstance(node, ast.ClassDef):
            classes.append(node.name)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            if isinstance(node, ast.Import):
                imports.extend(alias.name for alias in node.names)
            elif node.module:
                imports.append(node.module)
        elif isinstance(node, (ast.Assign, ast.AnnAssign)) and isinstance(getattr(node, "target", None), ast.Name):
            variables.append(node.target.id)
        elif isinstance(node, ast.Assign):
            variables.extend(target.id for target in node.targets if isinstance(target, ast.Name))
        elif isinstance(node, ast.Call):
            func = node.func
            if isinstance(func, ast.Name):
                calls.add(func.id)
            elif isinstance(func, ast.Attribute):
                calls.add(func.attr)
    return {
        "path": path, "language": "python", "functions": functions[:80], "classes": classes[:50],
        "variables": list(dict.fromkeys(variables))[:80], "imports": list(dict.fromkeys(imports))[:80],
        "routes": routes[:40], "calls": sorted(calls)[:100],
    }


def _script_structure(path: str, content: str) -> dict:
    functions = re.findall(
        r"(?:export\s+)?(?:async\s+)?function\s+([A-Za-z_$][\w$]*)\s*\(([^)]*)\)|"
        r"(?:export\s+)?(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*(?:async\s*)?\(([^)]*)\)\s*=>",
        content,
    )
    function_names = [f"{a or c}({(b or d).strip()})" for a, b, c, d in functions]
    classes = re.findall(r"(?:export\s+)?class\s+([A-Za-z_$][\w$]*)", content)
    interfaces = re.findall(r"(?:export\s+)?(?:interface|type)\s+([A-Za-z_$][\w$]*)", content)
    variables = re.findall(r"(?:export\s+)?(?:const|let|var)\s+([A-Za-z_$][\w$]*)", content)
    imports = re.findall(r"(?:from\s+|import\s*\()(['\"])([^'\"]+)\1", content)
    routes = re.findall(r"\.(?:get|post|put|patch|delete|use)\s*\(\s*(['\"])([^'\"]+)\1", content)
    return {
        "path": path, "language": "typescript/javascript", "functions": function_names[:80],
        "classes": classes[:50], "interfaces": interfaces[:80],
        "variables": list(dict.fromkeys(variables))[:100],
        "imports": list(dict.fromkeys(module for _, module in imports))[:100],
        "routes": list(dict.fromkeys(route for _, route in routes))[:50],
    }


def analyze_file(path: str, content: str) -> dict:
    lower = path.casefold()
    if lower.endswith((".py", ".pyi")):
        return _python_structure(path, content)
    if lower.endswith((".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs")):
        return _script_structure(path, content)
    return {"path": path, "language": "text/config"}


def repository_structure_summary(files: list[dict]) -> str:
    """主要ファイル間の静的構造をLLMが監査できる短い索引へする。"""
    sections = [
        "静的解析（コードは実行していない。動的dispatchや生成コードは未解決の場合がある）"
    ]
    for item in files:
        meta = item.get("meta") if isinstance(item.get("meta"), dict) else {}
        path = str(meta.get("path") or "")
        if not path:
            continue
        structure = analyze_file(path, str(item.get("snippet") or ""))
        lines = [f"## {path} ({structure.get('language', 'unknown')})"]
        for key, label in (
            ("classes", "classes"), ("interfaces", "interfaces/types"), ("functions", "functions"),
            ("variables", "module variables/exports"), ("imports", "imports/dependencies"),
            ("routes", "routes/endpoints"), ("calls", "observed calls"),
        ):
            values = structure.get(key)
            if values:
                lines.append(f"{label}: {', '.join(str(value) for value in values)}")
        if structure.get("parse_error"):
            lines.append("parse: failed")
        sections.append("\n".join(lines))
    return "\n\n".join(sections)[:24_000]
