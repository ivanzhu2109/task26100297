"""单文件词法符号索引库。

基于 Python 源码的静态作用域归属（ast 分析，不执行用户代码），
为编辑器上层提供"跳转定义"与"查找引用"能力。
源码与索引只保存在内存中。

支持 Python 3.11 可编译源码；绑定形式限于：函数名、形参、普通赋值、
解包赋值、增强赋值、for 目标、推导式目标、赋值表达式（海象运算符）。
不处理类、导入、注解、装饰器、删除绑定及其他绑定形式，不做跨文件解析。
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass

# 位置约定：(行号, 列偏移)，行号从 1 开始，列偏移从 0 开始，与 ast 节点一致。
Position = tuple[int, int]


@dataclass(frozen=True)
class Symbol:
    """一个词法符号：同一作用域内同名绑定的集合。"""

    name: str
    definitions: tuple[Position, ...]  # 全部实际绑定位置，升序
    references: tuple[Position, ...]   # 绑定位置 + 读取位置，升序


class SymbolIndex:
    """为一份源码建立的符号索引。"""

    def __init__(self, source: str) -> None:
        self._source = source
        tree = ast.parse(source)
        builder = _ScopeBuilder(source)
        builder.build(tree)
        self._symbols: dict[tuple[int, str], _SymbolRecord] = {}
        self._occurrences: dict[Position, _SymbolRecord] = {}
        self._module_scope = builder.module_scope
        self._retarget_declared(builder.scopes)
        self._resolve(builder)

    def symbol_at(self, line: int, col: int) -> Symbol | None:
        """返回覆盖指定位置的符号；该位置不是可查询名字时返回 None。"""
        record = self._occurrences.get((line, col))
        if record is None:
            return None
        return record.public

    def definitions_at(self, line: int, col: int) -> list[Position]:
        """跳转定义：返回选中名字所属符号的全部绑定位置。"""
        symbol = self.symbol_at(line, col)
        return list(symbol.definitions) if symbol is not None else []

    def references_at(self, line: int, col: int) -> list[Position]:
        """查找引用：返回选中名字所属符号的绑定 + 读取位置。"""
        symbol = self.symbol_at(line, col)
        return list(symbol.references) if symbol is not None else []

    # ---- 第二遍：声明重定向与解析 ----

    def _retarget_declared(self, scopes: list[_Scope]) -> None:
        """global/nonlocal 声明作用域内的绑定改归到目标作用域。"""
        for scope in scopes:  # 按创建顺序，外层先于内层
            for name in scope.global_decls:
                self._move_bindings(scope, self._module_scope, name)
            for name in scope.nonlocal_decls:
                target = self._nonlocal_target(scope, name)
                if target is not None:
                    self._move_bindings(scope, target, name)

    @staticmethod
    def _move_bindings(source_scope: _Scope, target_scope: _Scope, name: str) -> None:
        positions = source_scope.bound.pop(name, None)
        if positions:
            target_scope.bound.setdefault(name, []).extend(positions)

    def _nonlocal_target(self, scope: _Scope, name: str) -> _Scope | None:
        current = scope.parent
        while current is not None:
            if current.kind == _FUNCTION and name in current.bound:
                return current
            current = current.parent
        return None

    def _resolve(self, builder: _ScopeBuilder) -> None:
        for scope in builder.scopes:
            for name, positions in scope.bound.items():
                record = self._record_for(scope, name)
                for pos in positions:
                    record.definitions.add(pos)
                    record.references.add(pos)
                    self._occurrences[pos] = record
        for scope, name, pos in builder.reads:
            record = self._resolve_name(scope, name)
            record.references.add(pos)
            self._occurrences[pos] = record
        for scope in builder.scopes:
            for name, positions in scope.global_decls.items():
                record = self._record_for(self._module_scope, name)
                for pos in positions:
                    self._occurrences[pos] = record
            for name, positions in scope.nonlocal_decls.items():
                target = self._nonlocal_target(scope, name)
                if target is None:
                    continue
                record = self._record_for(target, name)
                for pos in positions:
                    self._occurrences[pos] = record

    def _resolve_name(self, scope: _Scope, name: str) -> "_SymbolRecord":
        current: _Scope | None = scope
        while current is not None:
            if current.kind != _COMPREHENSION:
                if name in current.global_decls:
                    return self._record_for(self._module_scope, name)
                if name in current.nonlocal_decls:
                    target = self._nonlocal_target(current, name)
                    if target is not None:
                        return self._record_for(target, name)
            if name in current.bound:
                return self._record_for(current, name)
            current = current.parent
        # 本文件没有绑定的全局名字：归模块级同名符号，定义集合为空。
        return self._record_for(self._module_scope, name)

    def _record_for(self, scope: _Scope, name: str) -> "_SymbolRecord":
        key = (id(scope), name)
        record = self._symbols.get(key)
        if record is None:
            record = _SymbolRecord(name)
            self._symbols[key] = record
        return record


_MODULE = "module"
_FUNCTION = "function"
_COMPREHENSION = "comprehension"


class _Scope:
    __slots__ = ("kind", "parent", "bound", "global_decls", "nonlocal_decls")

    def __init__(self, kind: str, parent: "_Scope | None") -> None:
        self.kind = kind
        self.parent = parent
        self.bound: dict[str, list[Position]] = {}
        # 声明本身不计入定义/引用，但名字位置要可用于查询。
        self.global_decls: dict[str, list[Position]] = {}
        self.nonlocal_decls: dict[str, list[Position]] = {}

    def add_binding(self, name: str, pos: Position) -> None:
        self.bound.setdefault(name, []).append(pos)


class _ScopeBuilder:
    """第一遍：构建作用域树，收集各作用域的原始绑定与 global/nonlocal 声明。

    同时记录全部读取位置 (scope, name, pos)，供第二遍解析归属。
    """

    def __init__(self, source: str) -> None:
        self.source = source
        self.module_scope = _Scope(_MODULE, None)
        self.scopes: list[_Scope] = [self.module_scope]
        self.reads: list[tuple[_Scope, str, Position]] = []

    def build(self, tree: ast.Module) -> None:
        self._build_body(tree.body, self.module_scope)

    # ---- 语句 ----

    def _build_body(self, stmts: list[ast.stmt], scope: _Scope) -> None:
        for stmt in stmts:
            self._build_stmt(stmt, scope)

    def _build_stmt(self, node: ast.stmt, scope: _Scope) -> None:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            scope.add_binding(node.name, self._def_name_pos(node))
            self._build_defaults(node.args, scope)
            # 装饰器与注解按规格不处理。
            func_scope = self._new_scope(_FUNCTION, scope)
            self._bind_params(node.args, func_scope)
            self._build_body(node.body, func_scope)
        elif isinstance(node, ast.ClassDef):
            pass  # 类不处理
        elif isinstance(node, ast.Assign):
            self._build_expr(node.value, scope)
            for target in node.targets:
                self._bind_target(target, scope)
        elif isinstance(node, ast.AugAssign):
            self._build_expr(node.value, scope)
            self._bind_target(node.target, scope)
        elif isinstance(node, ast.AnnAssign):
            pass  # 注解不处理
        elif isinstance(node, (ast.For, ast.AsyncFor)):
            self._build_expr(node.iter, scope)
            self._bind_target(node.target, scope)
            self._build_body(node.body, scope)
            self._build_body(node.orelse, scope)
        elif isinstance(node, ast.Global):
            self._record_decl(node, scope.global_decls)
        elif isinstance(node, ast.Nonlocal):
            self._record_decl(node, scope.nonlocal_decls)
        elif isinstance(node, (ast.Import, ast.ImportFrom, ast.Delete)):
            pass
        else:
            self._build_generic(node, scope)

    def _build_generic(self, node: ast.AST, scope: _Scope) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.expr):
                self._build_expr(child, scope)
            elif isinstance(child, ast.stmt):
                self._build_stmt(child, scope)
            else:
                self._build_generic(child, scope)

    # ---- 表达式 ----

    def _build_expr(self, node: ast.expr, scope: _Scope) -> None:
        if isinstance(node, ast.Name):
            if isinstance(node.ctx, ast.Load):
                self.reads.append((scope, node.id, _pos(node)))
            # Store 上下文由 _bind_target 处理，不会走到这里。
        elif isinstance(node, ast.Lambda):
            self._build_defaults(node.args, scope)
            func_scope = self._new_scope(_FUNCTION, scope)
            self._bind_params(node.args, func_scope)
            self._build_expr(node.body, func_scope)
        elif isinstance(node, ast.NamedExpr):
            self._build_expr(node.value, scope)
            target_scope = scope
            while target_scope.kind == _COMPREHENSION:
                target_scope = target_scope.parent
            target_scope.add_binding(node.target.id, _pos(node.target))
        elif isinstance(node, (ast.ListComp, ast.SetComp, ast.GeneratorExp, ast.DictComp)):
            self._build_comprehension(node, scope)
        else:
            self._build_generic(node, scope)

    def _build_comprehension(
        self,
        node: ast.ListComp | ast.SetComp | ast.GeneratorExp | ast.DictComp,
        outer: _Scope,
    ) -> None:
        comp_scope = self._new_scope(_COMPREHENSION, outer)
        for index, gen in enumerate(node.generators):
            # 最左生成子句的迭代源在外层解析，其余在内部解析。
            self._build_expr(gen.iter, outer if index == 0 else comp_scope)
            self._bind_target(gen.target, comp_scope)
            for cond in gen.ifs:
                self._build_expr(cond, comp_scope)
        if isinstance(node, ast.DictComp):
            self._build_expr(node.key, comp_scope)
            self._build_expr(node.value, comp_scope)
        else:
            self._build_expr(node.elt, comp_scope)

    # ---- 绑定辅助 ----

    def _bind_target(self, node: ast.expr, scope: _Scope) -> None:
        if isinstance(node, ast.Name):
            scope.add_binding(node.id, _pos(node))
        elif isinstance(node, ast.Starred):
            self._bind_target(node.value, scope)
        elif isinstance(node, (ast.Tuple, ast.List)):
            for elt in node.elts:
                self._bind_target(elt, scope)
        elif isinstance(node, (ast.Subscript, ast.Attribute)):
            # 属性/下标接收者是读取，不是绑定。
            self._build_expr(node, scope)

    def _bind_params(self, args: ast.arguments, scope: _Scope) -> None:
        params = list(args.posonlyargs) + list(args.args) + list(args.kwonlyargs)
        if args.vararg is not None:
            params.append(args.vararg)
        if args.kwarg is not None:
            params.append(args.kwarg)
        for param in params:
            scope.add_binding(param.arg, _pos(param))

    def _build_defaults(self, args: ast.arguments, scope: _Scope) -> None:
        for default in list(args.defaults) + [d for d in args.kw_defaults if d is not None]:
            self._build_expr(default, scope)

    def _new_scope(self, kind: str, parent: _Scope) -> _Scope:
        scope = _Scope(kind, parent)
        self.scopes.append(scope)
        return scope

    # ---- 位置计算 ----

    def _def_name_pos(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> Position:
        line = self.source.splitlines()[node.lineno - 1]
        match = re.search(r"\bdef\s+([A-Za-z_]\w*)", line[node.col_offset:])
        assert match is not None
        return (node.lineno, node.col_offset + match.start(1))

    def _record_decl(self, node: ast.Global | ast.Nonlocal, dest: dict[str, list[Position]]) -> None:
        segment = ast.get_source_segment(self.source, node) or ""
        offset = 0
        for name in node.names:
            match = re.search(r"\b" + re.escape(name) + r"\b", segment[offset:])
            assert match is not None
            start = offset + match.start()
            prefix = segment[:start]
            line = node.lineno + prefix.count("\n")
            if "\n" in prefix:
                col = len(prefix) - prefix.rindex("\n") - 1
            else:
                col = node.col_offset + start
            dest.setdefault(name, []).append((line, col))
            offset = start + len(name)


def _pos(node: ast.AST) -> Position:
    return (node.lineno, node.col_offset)


class _SymbolRecord:
    __slots__ = ("name", "definitions", "references", "_public")

    def __init__(self, name: str) -> None:
        self.name = name
        self.definitions: set[Position] = set()
        self.references: set[Position] = set()
        self._public: Symbol | None = None

    @property
    def public(self) -> Symbol:
        if self._public is None:
            self._public = Symbol(
                name=self.name,
                definitions=tuple(sorted(self.definitions)),
                references=tuple(sorted(self.references)),
            )
        return self._public
