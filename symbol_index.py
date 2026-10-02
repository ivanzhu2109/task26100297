"""单文件词法符号索引库。

基于 Python 源码的静态作用域归属(不执行用户代码),为编辑器上层提供
跳转定义(find definitions)与查找引用(find references)能力。

位置约定:与 ast 模块一致,行号从 1 开始,列从 0 开始(UTF-8 字节偏移)。
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field

# (line, column),行从 1 开始,列从 0 开始。
Position = tuple[int, int]

_MODULE = "module"
_FUNCTION = "function"
_COMPREHENSION = "comprehension"

# 出现种类:读取、绑定、声明(global/nonlocal,不计入定义与引用)。
_LOAD = "load"
_STORE = "store"
_DECLARE = "declare"


@dataclass
class Symbol:
    """一个符号:同一作用域内同名绑定的集合。"""

    name: str
    scope_kind: str  # "module" | "function" | "comprehension"
    definitions: list[Position] = field(default_factory=list)
    references: list[Position] = field(default_factory=list)  # 含定义位置


class SymbolIndex:
    """一份源码的符号索引。源码与索引均只保存在内存中。"""

    def __init__(self, source: str) -> None:
        self._source = source
        collector = _Collector(source)
        symbols, occurrences = _resolve(collector)
        self._symbols: list[Symbol] = list(symbols.values())
        self._by_name: dict[str, list[Symbol]] = {}
        for symbol in self._symbols:
            self._by_name.setdefault(symbol.name, []).append(symbol)
        self._occurrences: dict[Position, Symbol] = occurrences

    def symbols_named(self, name: str) -> list[Symbol]:
        """按名字查询,返回该名字对应的所有符号(不同作用域不合并)。"""
        return list(self._by_name.get(name, ()))

    def symbol_at(self, line: int, column: int) -> Symbol | None:
        """按源码位置查询该处名字所属的符号。"""
        return self._occurrences.get((line, column))

    def definitions_at(self, line: int, column: int) -> list[Position]:
        """跳转定义:返回选中名字所属符号的全部绑定位置。"""
        symbol = self.symbol_at(line, column)
        return list(symbol.definitions) if symbol else []

    def references_at(self, line: int, column: int) -> list[Position]:
        """查找引用:返回选中名字所属符号的全部读取与绑定位置。"""
        symbol = self.symbol_at(line, column)
        return list(symbol.references) if symbol else []


def build_index(source: str) -> SymbolIndex:
    """为一份源码建立索引。"""
    return SymbolIndex(source)


def _resolve(collector: _Collector):
    """把名字出现解析为符号,返回 (符号表, 位置->符号 映射)。"""
    module_scope = collector.module_scope
    symbols: dict[tuple[int, str], Symbol] = {}
    definitions: dict[tuple[int, str], set[Position]] = {}
    references: dict[tuple[int, str], set[Position]] = {}
    occurrences: dict[Position, Symbol] = {}

    def get_symbol(scope: _Scope, name: str) -> Symbol:
        key = (id(scope), name)
        symbol = symbols.get(key)
        if symbol is None:
            symbol = Symbol(name=name, scope_kind=scope.kind)
            symbols[key] = symbol
            definitions[key] = set()
            references[key] = set()
        return symbol

    def nonlocal_target(scope: _Scope, name: str) -> _Scope:
        # 归属最近一个对它有局部绑定的外层函数;输入保证可编译,必然存在。
        enclosing = scope.parent
        while enclosing is not None:
            if enclosing.kind == _FUNCTION and name in enclosing.locals:
                return enclosing
            enclosing = enclosing.parent
        return module_scope

    def store_scope(scope: _Scope, name: str) -> _Scope:
        if name in scope.declared_global:
            return module_scope
        if name in scope.declared_nonlocal:
            return nonlocal_target(scope, name)
        return scope

    def load_scope(scope: _Scope, name: str) -> _Scope:
        current = scope
        while current is not None:
            if name in current.declared_global:
                return module_scope
            if name in current.declared_nonlocal:
                return nonlocal_target(current, name)
            if name in current.locals:
                return current
            current = current.parent
        # 没有本文件绑定的全局名字仍按模块级同名符号归组。
        return module_scope

    for occurrence in collector.occurrences:
        key_scope = None
        if occurrence.kind == _STORE:
            key_scope = store_scope(occurrence.scope, occurrence.name)
        else:
            key_scope = load_scope(occurrence.scope, occurrence.name)
        symbol = get_symbol(key_scope, occurrence.name)
        key = (id(key_scope), occurrence.name)
        if occurrence.kind == _STORE:
            definitions[key].add(occurrence.pos)
            references[key].add(occurrence.pos)
        elif occurrence.kind == _LOAD:
            references[key].add(occurrence.pos)
        # _DECLARE 只建立位置到符号的映射,不计入定义与引用。
        occurrences[occurrence.pos] = symbol

    for key, symbol in symbols.items():
        symbol.definitions = sorted(definitions[key])
        symbol.references = sorted(references[key])
    return symbols, occurrences


class _Scope:
    """静态作用域:模块、函数(含 lambda)、推导式。"""

    __slots__ = ("kind", "parent", "locals", "declared_global", "declared_nonlocal")

    def __init__(self, kind: str, parent: "_Scope | None") -> None:
        self.kind = kind
        self.parent = parent
        self.locals: set[str] = set()
        self.declared_global: set[str] = set()
        self.declared_nonlocal: set[str] = set()


@dataclass
class _Occurrence:
    name: str
    pos: Position
    kind: str  # _LOAD | _STORE | _DECLARE
    scope: _Scope  # 绑定类出现已按赋值表达式规则归到生效作用域


class _Collector:
    """单次遍历 AST:建立作用域树、各作用域局部名集合与名字出现列表。"""

    def __init__(self, source: str) -> None:
        self._lines = source.splitlines()
        self.module_scope = _Scope(_MODULE, None)
        self.occurrences: list[_Occurrence] = []
        tree = ast.parse(source)
        self._collect_declarations(tree.body, self.module_scope)
        for stmt in tree.body:
            self._visit(stmt, self.module_scope)

    # ---- 记录 ----

    def _load(self, scope: _Scope, name: str, pos: Position) -> None:
        self.occurrences.append(_Occurrence(name, pos, _LOAD, scope))

    def _store(self, scope: _Scope, name: str, pos: Position) -> None:
        # 同时维护作用域的局部名集合,供解析阶段判断归属。
        if name in scope.declared_global:
            self.module_scope.locals.add(name)
        elif name not in scope.declared_nonlocal:
            scope.locals.add(name)
        self.occurrences.append(_Occurrence(name, pos, _STORE, scope))

    def _declare(self, scope: _Scope, name: str, pos: Position) -> None:
        self.occurrences.append(_Occurrence(name, pos, _DECLARE, scope))

    # ---- 声明预收集:global/nonlocal 对整个作用域生效 ----

    def _collect_declarations(self, stmts: list[ast.stmt], scope: _Scope) -> None:
        stack = list(stmts)
        while stack:
            node = stack.pop()
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                continue  # 嵌套作用域的声明不属于本作用域
            if isinstance(node, ast.Global):
                scope.declared_global.update(node.names)
            elif isinstance(node, ast.Nonlocal):
                scope.declared_nonlocal.update(node.names)
            for child in ast.iter_child_nodes(node):
                if isinstance(child, ast.stmt):
                    stack.append(child)

    # ---- 遍历 ----

    def _visit(self, node: ast.AST, scope: _Scope) -> None:
        method = self._dispatch.get(type(node))
        if method is not None:
            method(self, node, scope)
        else:
            for child in ast.iter_child_nodes(node):
                self._visit(child, scope)

    def _visit_name(self, node: ast.Name, scope: _Scope) -> None:
        pos = (node.lineno, node.col_offset)
        if isinstance(node.ctx, ast.Load):
            self._load(scope, node.id, pos)
        elif isinstance(node.ctx, ast.Store):
            self._store(scope, node.id, pos)

    def _visit_function_def(self, node, scope: _Scope) -> None:
        # 函数名绑定在外层作用域。
        self._store(scope, node.name, self._def_name_position(node))
        for decorator in node.decorator_list:
            self._visit(decorator, scope)
        # 默认值在外层作用域解析。
        self._visit_defaults(node.args, scope)
        func_scope = _Scope(_FUNCTION, scope)
        self._collect_declarations(node.body, func_scope)
        self._bind_params(node.args, func_scope)
        for stmt in node.body:
            self._visit(stmt, func_scope)

    def _visit_lambda(self, node: ast.Lambda, scope: _Scope) -> None:
        self._visit_defaults(node.args, scope)
        func_scope = _Scope(_FUNCTION, scope)
        self._bind_params(node.args, func_scope)
        self._visit(node.body, func_scope)

    def _visit_defaults(self, args: ast.arguments, scope: _Scope) -> None:
        for default in args.defaults:
            self._visit(default, scope)
        for default in args.kw_defaults:
            if default is not None:
                self._visit(default, scope)

    def _bind_params(self, args: ast.arguments, scope: _Scope) -> None:
        params = list(args.posonlyargs) + list(args.args) + list(args.kwonlyargs)
        if args.vararg is not None:
            params.append(args.vararg)
        if args.kwarg is not None:
            params.append(args.kwarg)
        for param in params:
            self._store(scope, param.arg, (param.lineno, param.col_offset))

    def _visit_comprehension(self, node, scope: _Scope) -> None:
        comp_scope = _Scope(_COMPREHENSION, scope)
        for index, generator in enumerate(node.generators):
            # 最左生成子句的迭代源在外层解析,其余在内部解析。
            iter_scope = scope if index == 0 else comp_scope
            self._visit(generator.iter, iter_scope)
            self._bind_target(generator.target, comp_scope)
            for condition in generator.ifs:
                self._visit(condition, comp_scope)
        if isinstance(node, ast.DictComp):
            self._visit(node.key, comp_scope)
            self._visit(node.value, comp_scope)
        else:
            self._visit(node.elt, comp_scope)

    def _visit_named_expr(self, node: ast.NamedExpr, scope: _Scope) -> None:
        self._visit(node.value, scope)
        # 推导式中的赋值表达式绑定最近的非推导式作用域;
        # lambda 本身是函数作用域,自然截断,不会归到更外层。
        target_scope = scope
        while target_scope.kind == _COMPREHENSION:
            target_scope = target_scope.parent
        self._store(target_scope, node.target.id,
                    (node.target.lineno, node.target.col_offset))

    def _visit_assign(self, node: ast.Assign, scope: _Scope) -> None:
        self._visit(node.value, scope)
        for target in node.targets:
            self._bind_target(target, scope)

    def _visit_aug_assign(self, node: ast.AugAssign, scope: _Scope) -> None:
        if isinstance(node.target, ast.Name):
            # 增强赋值是绑定形式;同一位置只计一次,不再重复计读取。
            self._store(scope, node.target.id,
                        (node.target.lineno, node.target.col_offset))
        else:
            self._visit(node.target, scope)
        self._visit(node.value, scope)

    def _visit_for(self, node, scope: _Scope) -> None:
        self._visit(node.iter, scope)
        self._bind_target(node.target, scope)
        for stmt in node.body + node.orelse:
            self._visit(stmt, scope)

    def _visit_with(self, node, scope: _Scope) -> None:
        for item in node.items:
            self._visit(item.context_expr, scope)
            if item.optional_vars is not None:
                self._bind_target(item.optional_vars, scope)
        for stmt in node.body:
            self._visit(stmt, scope)

    def _visit_global(self, node, scope: _Scope) -> None:
        for name, pos in self._decl_positions(node):
            self._declare(scope, name, pos)

    # ---- 绑定目标与位置工具 ----

    def _bind_target(self, target: ast.AST, scope: _Scope) -> None:
        if isinstance(target, ast.Name):
            self._store(scope, target.id, (target.lineno, target.col_offset))
        elif isinstance(target, ast.Starred):
            self._bind_target(target.value, scope)
        elif isinstance(target, (ast.Tuple, ast.List)):
            for element in target.elts:
                self._bind_target(element, scope)
        else:
            # 属性/下标等目标:接收者与下标表达式仍按读取归属。
            self._visit(target, scope)

    def _def_name_position(self, node) -> Position:
        line = self._lines[node.lineno - 1]
        return (node.lineno, line.index(node.name, node.col_offset))

    def _decl_positions(self, node) -> list[tuple[str, Position]]:
        # global/nonlocal 语句中每个名字的位置;找不到时退化为语句位置。
        results = []
        for name in node.names:
            pos = None
            for lineno in range(node.lineno, node.end_lineno + 1):
                match = re.search(r"\b%s\b" % re.escape(name),
                                  self._lines[lineno - 1])
                if match:
                    pos = (lineno, match.start())
                    break
            results.append((name, pos or (node.lineno, node.col_offset)))
        return results


_Collector._dispatch = {
    ast.Name: _Collector._visit_name,
    ast.FunctionDef: _Collector._visit_function_def,
    ast.AsyncFunctionDef: _Collector._visit_function_def,
    ast.Lambda: _Collector._visit_lambda,
    ast.ListComp: _Collector._visit_comprehension,
    ast.SetComp: _Collector._visit_comprehension,
    ast.DictComp: _Collector._visit_comprehension,
    ast.GeneratorExp: _Collector._visit_comprehension,
    ast.NamedExpr: _Collector._visit_named_expr,
    ast.Assign: _Collector._visit_assign,
    ast.AugAssign: _Collector._visit_aug_assign,
    ast.For: _Collector._visit_for,
    ast.AsyncFor: _Collector._visit_for,
    ast.With: _Collector._visit_with,
    ast.AsyncWith: _Collector._visit_with,
    ast.Global: _Collector._visit_global,
    ast.Nonlocal: _Collector._visit_global,
}
