"""
Static def-use tree extractor for a single Python file.

Core compiler idea: a "def-use chain" links each variable definition to
every point where it is subsequently read. Walking the AST gives us
that graph without executing the code.

Two outputs:
  1. VARIABLE INDEX: every name -> list of defs (line, RHS text, guessed type)
                                   and list of uses (line, context snippet)
  2. ANCESTRY TREE: for a seed variable (default: `readings`), recursively
                   walk from RHS back to the names that fed it, printing a
                   tree rooted at the seed.

Limitations (honest ones):
- Python is dynamically typed; type guesses are heuristic, not proofs.
- We do not resolve cross-module names or attribute chains deeply.
- Reassignment into the same name is shown as multiple definitions.
"""

import ast
import os
import sys
import textwrap

SOURCE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "DAQ2700.py")


def guess_type_from_rhs(node):
    """Cheap syntactic type inference. Good enough for a reading aid."""
    if isinstance(node, ast.Constant):
        return type(node.value).__name__
    if isinstance(node, ast.Dict):
        return "dict (literal)"
    if isinstance(node, ast.List):
        return "list (literal)"
    if isinstance(node, ast.Tuple):
        return "tuple (literal)"
    if isinstance(node, ast.Set):
        return "set (literal)"
    if isinstance(node, ast.DictComp):
        return "dict (comprehension)"
    if isinstance(node, ast.ListComp):
        return "list (comprehension)"
    if isinstance(node, ast.SetComp):
        return "set (comprehension)"
    if isinstance(node, ast.GeneratorExp):
        return "generator"
    if isinstance(node, ast.JoinedStr):
        return "str (f-string)"
    if isinstance(node, ast.BinOp):
        return "expr (BinOp)"
    if isinstance(node, ast.Call):
        func = node.func
        if isinstance(func, ast.Attribute):
            method = func.attr
            known_returns = {
                "split": "list[str]",
                "strip": "str",
                "keys": "dict_keys",
                "values": "dict_values",
                "items": "dict_items",
                "get": "any (dict.get)",
                "ask": "str (instrument reply)",
                "write": "None",
                "items": "dict_items",
                "readline": "str",
                "read": "str | bytes",
            }
            if method in known_returns:
                return known_returns[method]
            return f"call -> {method}(...)"
        if isinstance(func, ast.Name):
            ctor_hints = {
                "dict": "dict", "list": "list", "set": "set", "tuple": "tuple",
                "int": "int", "float": "float", "str": "str", "bool": "bool",
                "open": "file (text or binary)", "time": "float",
                "read_snapshot": "dict | None",
                "compute_freshness": "tuple(age, status)",
                "extract_tdk_columns": "dict",
            }
            if func.id in ctor_hints:
                return ctor_hints[func.id]
            return f"call -> {func.id}(...)"
        return "call"
    if isinstance(node, ast.Attribute):
        return f"attr .{node.attr}"
    if isinstance(node, ast.Subscript):
        return "subscript []"
    if isinstance(node, ast.Name):
        return f"alias of `{node.id}`"
    return type(node).__name__


def names_in(node):
    """All `ast.Name` identifiers reachable from `node` (i.e., RHS dependencies)."""
    out = []
    for sub in ast.walk(node):
        if isinstance(sub, ast.Name):
            out.append(sub.id)
    return out


def rhs_snippet(source_lines, node, width=80):
    seg = ast.get_source_segment("\n".join(source_lines), node)
    if seg is None:
        return "<unavailable>"
    seg = " ".join(seg.split())
    return seg if len(seg) <= width else seg[: width - 1] + "…"


def build_index(tree, source_lines):
    """Return {name: {'defs': [...], 'uses': [...]}}.

    defs entry: (line, guessed_type, rhs_text, rhs_names, kind)
    uses entry: (line, context_snippet)
    """
    index = {}

    def get(name):
        return index.setdefault(name, {"defs": [], "uses": []})

    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            rhs = node.value
            rhs_text = rhs_snippet(source_lines, rhs)
            rhs_type = guess_type_from_rhs(rhs)
            rhs_names = sorted(set(names_in(rhs)))
            for target in node.targets:
                for sub in ast.walk(target):
                    if isinstance(sub, ast.Name):
                        get(sub.id)["defs"].append(
                            (node.lineno, rhs_type, rhs_text, rhs_names, "assign")
                        )
        elif isinstance(node, ast.AugAssign):
            if isinstance(node.target, ast.Name):
                rhs_text = rhs_snippet(source_lines, node.value)
                get(node.target.id)["defs"].append(
                    (node.lineno, "augmented", rhs_text, sorted(set(names_in(node.value))), "augassign")
                )
        elif isinstance(node, ast.For):
            if isinstance(node.target, ast.Name):
                rhs_text = rhs_snippet(source_lines, node.iter)
                get(node.target.id)["defs"].append(
                    (node.lineno, "loop var", rhs_text, sorted(set(names_in(node.iter))), "for")
                )
        elif isinstance(node, ast.FunctionDef):
            for arg in node.args.args + node.args.kwonlyargs:
                get(arg.arg)["defs"].append(
                    (node.lineno, "parameter", f"param of {node.name}()", [], "param")
                )

    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
            line = getattr(node, "lineno", None)
            if line is None:
                continue
            snippet = source_lines[line - 1].strip() if line - 1 < len(source_lines) else ""
            if len(snippet) > 90:
                snippet = snippet[:89] + "…"
            get(node.id)["uses"].append((line, snippet))

    return index


def print_variable_index(index, out):
    out.write("=" * 78 + "\n")
    out.write("VARIABLE INDEX  (every name -> its defs and uses)\n")
    out.write("=" * 78 + "\n\n")
    for name in sorted(index):
        info = index[name]
        if not info["defs"] and not info["uses"]:
            continue
        out.write(f"## {name}\n")
        if info["defs"]:
            out.write("  defs:\n")
            for line, typ, text, deps, kind in info["defs"]:
                out.write(f"    L{line:>4}  [{kind:>8}]  type? {typ}\n")
                out.write(f"           RHS: {text}\n")
                if deps:
                    out.write(f"           depends on: {', '.join(deps)}\n")
        if info["uses"]:
            out.write(f"  uses ({len(info['uses'])}):\n")
            for line, snip in info["uses"][:12]:
                out.write(f"    L{line:>4}  {snip}\n")
            if len(info["uses"]) > 12:
                out.write(f"    … {len(info['uses']) - 12} more\n")
        out.write("\n")


def print_ancestry_tree(index, seed, out, max_depth=6):
    out.write("=" * 78 + "\n")
    out.write(f"ANCESTRY TREE rooted at `{seed}` (depth<= {max_depth})\n")
    out.write("=" * 78 + "\n\n")

    def walk(name, depth, visited, prefix):
        if name not in index or not index[name]["defs"]:
            out.write(f"{prefix}{name}   [external / not defined in this file]\n")
            return
        for line, typ, text, deps, kind in index[name]["defs"]:
            out.write(f"{prefix}{name}   (L{line}, {kind}, {typ})\n")
            out.write(f"{prefix}    := {text}\n")
            if depth >= max_depth:
                out.write(f"{prefix}    … (depth limit)\n")
                continue
            for dep in deps:
                if dep in visited:
                    out.write(f"{prefix}    ├── {dep}  [cycle]\n")
                    continue
                walk(dep, depth + 1, visited | {dep}, prefix + "    ")

    walk(seed, 0, {seed}, "")


def main():
    seed = sys.argv[1] if len(sys.argv) > 1 else "readings"
    out_path = os.path.join(os.path.dirname(__file__), "ast_tree.out.txt")

    with open(SOURCE_FILE, "r", encoding="utf-8") as f:
        source = f.read()
    source_lines = source.splitlines()
    tree = ast.parse(source)

    index = build_index(tree, source_lines)

    with open(out_path, "w", encoding="utf-8") as out:
        out.write(f"source: {os.path.abspath(SOURCE_FILE)}\n")
        out.write(f"seed variable for ancestry tree: {seed}\n\n")
        print_ancestry_tree(index, seed, out)
        out.write("\n")
        print_variable_index(index, out)

    print(f"[ast_tree] wrote {out_path}")


if __name__ == "__main__":
    main()
