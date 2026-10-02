import io
import tokenize
import unittest

from symbol_index import SymbolIndex, build_index


def token_positions(source, name):
    """返回源码中所有等于 name 的 NAME 记号位置 {(line, col)}。"""
    positions = set()
    tokens = tokenize.generate_tokens(io.StringIO(source).readline)
    for tok in tokens:
        if tok.type == tokenize.NAME and tok.string == name:
            positions.add((tok.start[0], tok.start[1]))
    return positions


def positions_on_lines(source, name, lines):
    """取 name 在指定行号集合上的记号位置。"""
    return {pos for pos in token_positions(source, name) if pos[0] in lines}


class BasicBindingTest(unittest.TestCase):
    def test_module_multiple_bindings_same_symbol(self):
        source = "value = 1\nvalue = value + 2\nprint(value)\n"
        index = build_index(source)
        symbols = index.symbols_named("value")
        self.assertEqual(len(symbols), 1)
        self.assertEqual(symbols[0].definitions, [(1, 0), (2, 0)])
        self.assertEqual(symbols[0].references, [(1, 0), (2, 0), (2, 8), (3, 6)])

    def test_unbound_global_groups_under_module_with_empty_defs(self):
        source = "print(missing)\nmissing_func(missing)\n"
        index = build_index(source)
        symbols = index.symbols_named("missing")
        self.assertEqual(len(symbols), 1)
        self.assertEqual(symbols[0].scope_kind, "module")
        self.assertEqual(symbols[0].definitions, [])
        self.assertEqual(symbols[0].references, [(1, 6), (2, 13)])

    def test_local_binding_makes_name_local_everywhere(self):
        # 提前读取与不可达分支中的赋值都属于同一个局部符号。
        source = (
            "def func(cond):\n"
            "    total = acc\n"
            "    if cond:\n"
            "        acc = 1\n"
            "    elif other:\n"
            "        acc = 2\n"
            "    else:\n"
            "        acc = 3\n"
            "    return acc\n"
        )
        index = build_index(source)
        symbols = index.symbols_named("acc")
        self.assertEqual(len(symbols), 1)
        self.assertEqual(symbols[0].scope_kind, "function")
        self.assertEqual(symbols[0].definitions, [(4, 8), (6, 8), (8, 8)])
        self.assertEqual(
            symbols[0].references,
            [(2, 12), (4, 8), (6, 8), (8, 8), (9, 11)],
        )

    def test_unpacking_and_for_target(self):
        source = (
            "pair = (1, 2)\n"
            "first, *rest = pair\n"
            "for item in rest:\n"
            "    total = item + first\n"
        )
        index = build_index(source)
        first = index.symbols_named("first")[0]
        self.assertEqual(first.definitions, [(2, 0)])
        self.assertEqual(first.references, [(2, 0), (4, 19)])
        rest = index.symbols_named("rest")[0]
        self.assertEqual(rest.definitions, [(2, 8)])
        self.assertEqual(rest.references, [(2, 8), (3, 12)])
        item = index.symbols_named("item")[0]
        self.assertEqual(item.definitions, [(3, 4)])
        self.assertEqual(item.references, [(3, 4), (4, 12)])

    def test_aug_assign_counts_once(self):
        source = "counter = 0\ncounter += 5\n"
        index = build_index(source)
        counter = index.symbols_named("counter")[0]
        self.assertEqual(counter.definitions, [(1, 0), (2, 0)])
        self.assertEqual(counter.references, [(1, 0), (2, 0)])

    def test_attribute_and_keyword_labels_are_not_references(self):
        source = "obj.run(value=x)\n"
        index = build_index(source)
        self.assertEqual(index.symbols_named("run"), [])
        self.assertEqual(index.symbols_named("value"), [])
        obj = index.symbols_named("obj")[0]
        self.assertEqual(obj.references, [(1, 0)])
        x = index.symbols_named("x")[0]
        self.assertEqual(x.references, [(1, 14)])

    def test_same_name_different_scopes_not_merged(self):
        source = (
            "temp = 1\n"
            "def func():\n"
            "    temp = 2\n"
            "    return temp\n"
        )
        index = build_index(source)
        symbols = index.symbols_named("temp")
        self.assertEqual(len(symbols), 2)
        by_kind = {s.scope_kind: s for s in symbols}
        self.assertEqual(by_kind["module"].definitions, [(1, 0)])
        self.assertEqual(by_kind["module"].references, [(1, 0)])
        self.assertEqual(by_kind["function"].definitions, [(3, 4)])
        self.assertEqual(by_kind["function"].references, [(3, 4), (4, 11)])

    def test_query_by_position(self):
        source = "alpha = 1\nbeta = alpha\n"
        index = build_index(source)
        self.assertEqual(index.definitions_at(2, 7), [(1, 0)])
        self.assertEqual(index.references_at(1, 0), [(1, 0), (2, 7)])
        self.assertIsNone(index.symbol_at(2, 0) and None)
        beta = index.symbol_at(2, 0)
        self.assertEqual(beta.name, "beta")


CLOSURE_SOURCE = (
    "val = 100\n"
    "def outer(val=val):\n"
    "    total = val\n"
    "    def middle():\n"
    "        nonlocal total\n"
    "        total = total + 1\n"
    "        def inner():\n"
    "            global val\n"
    "            val = val + total\n"
    "            return total\n"
    "        return inner\n"
    "    def early():\n"
    "        return read\n"
    "    read = 5\n"
    "    return middle, early\n"
)


class ClosureTest(unittest.TestCase):
    """多层闭包混合提前读取、global/nonlocal 与默认值。"""

    @classmethod
    def setUpClass(cls):
        cls.index = build_index(CLOSURE_SOURCE)

    def symbols(self, name):
        return self.index.symbols_named(name)

    def test_module_val_and_param_val_are_distinct(self):
        symbols = self.symbols("val")
        self.assertEqual(len(symbols), 2)
        by_kind = {s.scope_kind: s for s in symbols}
        module_val = by_kind["module"]
        # global 声明使 inner 中的赋值归属模块;默认值在外层解析。
        self.assertEqual(module_val.definitions, [(1, 0), (9, 12)])
        self.assertEqual(
            module_val.references, [(1, 0), (2, 14), (9, 12), (9, 18)]
        )
        param_val = by_kind["function"]
        self.assertEqual(param_val.definitions, [(2, 10)])
        self.assertEqual(param_val.references, [(2, 10), (3, 12)])

    def test_nonlocal_binds_nearest_outer_function(self):
        symbols = self.symbols("total")
        self.assertEqual(len(symbols), 1)
        total = symbols[0]
        self.assertEqual(total.scope_kind, "function")
        # nonlocal 声明本身不计入;middle 中的赋值与读取归属 outer 的 total。
        self.assertEqual(total.definitions, [(3, 4), (6, 8)])
        self.assertEqual(
            total.references, [(3, 4), (6, 8), (6, 16), (9, 24), (10, 19)]
        )

    def test_early_read_is_local_to_outer(self):
        symbols = self.symbols("read")
        self.assertEqual(len(symbols), 1)
        read = symbols[0]
        self.assertEqual(read.definitions, [(14, 4)])
        self.assertEqual(read.references, [(13, 15), (14, 4)])

    def test_function_names_bind_in_outer_scope(self):
        outer = self.symbols("outer")[0]
        self.assertEqual(outer.scope_kind, "module")
        self.assertEqual(outer.definitions, [(2, 4)])
        middle = self.symbols("middle")[0]
        self.assertEqual(middle.definitions, [(4, 8)])
        self.assertEqual(middle.references, [(4, 8), (15, 11)])
        inner = self.symbols("inner")[0]
        self.assertEqual(inner.definitions, [(7, 12)])
        self.assertEqual(inner.references, [(7, 12), (11, 15)])
        early = self.symbols("early")[0]
        self.assertEqual(early.definitions, [(12, 8)])
        self.assertEqual(early.references, [(12, 8), (15, 19)])

    def test_declarations_are_queryable_but_not_counted(self):
        # nonlocal total 声明位置可查询到符号,但不计入定义与引用。
        total = self.index.symbol_at(5, 17)
        self.assertIsNotNone(total)
        self.assertEqual(total.name, "total")
        self.assertNotIn((5, 17), total.definitions)
        self.assertNotIn((5, 17), total.references)
        # global val 声明位置可查询,解析到模块级 val。
        module_val = self.index.symbol_at(8, 19)
        self.assertIsNotNone(module_val)
        self.assertEqual(module_val.scope_kind, "module")
        self.assertEqual(self.index.definitions_at(8, 19), [(1, 0), (9, 12)])


COMPREHENSION_SOURCE = (
    "data = [1, 2, 3]\n"
    "def process(items):\n"
    "    result = [[y for y in row] for row in items]\n"
    "    gen = (row for row in data if row in result)\n"
    "    marked = [y for y in data if (tag := y)]\n"
    "    lazy = lambda: [w for w in data if (inner_tag := w)]\n"
    "    return result, gen, marked, lazy\n"
)


class ComprehensionTest(unittest.TestCase):
    """嵌套推导式与生成器:同名目标、外层迭代源与赋值表达式。"""

    @classmethod
    def setUpClass(cls):
        cls.index = build_index(COMPREHENSION_SOURCE)

    def symbols(self, name):
        return self.index.symbols_named(name)

    def only(self, name):
        symbols = self.symbols(name)
        self.assertEqual(len(symbols), 1, name)
        return symbols[0]

    def test_outermost_iterable_resolves_outside(self):
        # 最左迭代源 items 是函数形参的读取,不是推导式内部名字。
        items = self.only("items")
        self.assertEqual(items.scope_kind, "function")
        self.assertEqual(items.definitions, [(2, 12)])
        self.assertEqual(items.references, [(2, 12), (3, 42)])
        # data 在模块级绑定,各推导式的最左迭代源都读到模块符号。
        data = self.only("data")
        self.assertEqual(data.scope_kind, "module")
        self.assertEqual(data.definitions, [(1, 0)])
        self.assertEqual(data.references, [(1, 0), (4, 26), (5, 25), (6, 31)])

    def test_same_name_targets_in_nested_scopes_stay_separate(self):
        rows = self.symbols("row")
        self.assertEqual(len(rows), 2)
        for row in rows:
            self.assertEqual(row.scope_kind, "comprehension")
        by_defs = {r.definitions[0]: r for r in rows}
        outer = by_defs[(3, 35)]
        # 内层推导式的最左迭代源 row 在外层推导式作用域解析。
        self.assertEqual(outer.references, [(3, 26), (3, 35)])
        gen_row = by_defs[(4, 19)]
        self.assertEqual(gen_row.references, [(4, 11), (4, 19), (4, 34)])

    def test_comprehension_targets_do_not_leak(self):
        for name in ("row", "y", "w"):
            for symbol in self.symbols(name):
                self.assertEqual(symbol.scope_kind, "comprehension", name)
        ys = self.symbols("y")
        self.assertEqual(len(ys), 2)
        by_defs = {y.definitions[0]: y for y in ys}
        self.assertEqual(by_defs[(3, 21)].references, [(3, 15), (3, 21)])
        self.assertEqual(
            by_defs[(5, 20)].references, [(5, 14), (5, 20), (5, 41)]
        )
        w = self.only("w")
        self.assertEqual(w.definitions, [(6, 26)])
        self.assertEqual(w.references, [(6, 20), (6, 26), (6, 53)])

    def test_walrus_in_comprehension_binds_function_scope(self):
        tag = self.only("tag")
        self.assertEqual(tag.scope_kind, "function")
        self.assertEqual(tag.definitions, [(5, 34)])
        self.assertEqual(tag.references, [(5, 34)])

    def test_walrus_stops_at_lambda_scope(self):
        # 赋值表达式与推导式之间隔着 lambda 时,归 lambda 自己的作用域。
        inner_tag = self.only("inner_tag")
        self.assertEqual(inner_tag.scope_kind, "function")
        self.assertEqual(inner_tag.definitions, [(6, 40)])
        self.assertEqual(inner_tag.references, [(6, 40)])
        self.assertIsNot(inner_tag, self.only("tag"))

    def test_function_locals_around_comprehensions(self):
        result = self.only("result")
        self.assertEqual(result.scope_kind, "function")
        self.assertEqual(result.definitions, [(3, 4)])
        self.assertEqual(result.references, [(3, 4), (4, 41), (7, 11)])
        gen = self.only("gen")
        self.assertEqual(gen.references, [(4, 4), (7, 19)])
        marked = self.only("marked")
        self.assertEqual(marked.references, [(5, 4), (7, 24)])
        lazy = self.only("lazy")
        self.assertEqual(lazy.references, [(6, 4), (7, 32)])


class WalrusScopeTest(unittest.TestCase):
    """赋值表达式在推导式中归最近的非推导式作用域,并服从其声明。"""

    def test_module_level_genexp_walrus_binds_module(self):
        source = (
            "src = [1, 2]\n"
            "gen = (n for n in src if (count := n))\n"
            "total = count\n"
        )
        index = build_index(source)
        count = index.symbols_named("count")[0]
        self.assertEqual(count.scope_kind, "module")
        self.assertEqual(count.definitions, [(2, 26)])
        self.assertEqual(count.references, [(2, 26), (3, 8)])
        n = index.symbols_named("n")[0]
        self.assertEqual(n.scope_kind, "comprehension")
        self.assertEqual(n.references, [(2, 7), (2, 13), (2, 35)])

    def test_walrus_respects_global_declaration(self):
        source = (
            "gvar = 0\n"
            "src = [1]\n"
            "def func():\n"
            "    global gvar\n"
            "    vals = [gvar for _ in src if (gvar := 1)]\n"
            "    return vals\n"
        )
        index = build_index(source)
        symbols = index.symbols_named("gvar")
        self.assertEqual(len(symbols), 1)
        gvar = symbols[0]
        self.assertEqual(gvar.scope_kind, "module")
        self.assertEqual(gvar.definitions, [(1, 0), (5, 34)])
        self.assertEqual(gvar.references, [(1, 0), (5, 12), (5, 34)])
        # global 声明位置可查询但不计入结果。
        self.assertIs(index.symbol_at(4, 11), gvar)
        self.assertNotIn((4, 11), gvar.references)

    def test_walrus_respects_nonlocal_declaration(self):
        source = (
            "def outer():\n"
            "    acc = 0\n"
            "    def middle(src):\n"
            "        nonlocal acc\n"
            "        vals = [acc for x in src if (acc := x)]\n"
            "        return vals\n"
            "    return middle\n"
        )
        index = build_index(source)
        symbols = index.symbols_named("acc")
        self.assertEqual(len(symbols), 1)
        acc = symbols[0]
        self.assertEqual(acc.scope_kind, "function")
        self.assertEqual(acc.definitions, [(2, 4), (5, 37)])
        self.assertEqual(acc.references, [(2, 4), (5, 16), (5, 37)])
        self.assertIs(index.symbol_at(4, 17), acc)
        x = index.symbols_named("x")[0]
        self.assertEqual(x.scope_kind, "comprehension")
        self.assertEqual(x.references, [(5, 24), (5, 44)])


class LambdaAndDictCompTest(unittest.TestCase):
    SOURCE = (
        "base = 5\n"
        "offset = 1\n"
        "fn = lambda step=base, *rest: step + offset\n"
        "table = {k: k + base for k in rest}\n"
        "seen = {c for c in word}\n"
    )

    @classmethod
    def setUpClass(cls):
        cls.index = build_index(cls.SOURCE)

    def test_lambda_defaults_resolve_outside(self):
        base = self.index.symbols_named("base")[0]
        self.assertEqual(base.scope_kind, "module")
        self.assertEqual(base.definitions, [(1, 0)])
        self.assertEqual(base.references, [(1, 0), (3, 17), (4, 16)])
        step = self.index.symbols_named("step")[0]
        self.assertEqual(step.scope_kind, "function")
        self.assertEqual(step.definitions, [(3, 12)])
        self.assertEqual(step.references, [(3, 12), (3, 30)])
        offset = self.index.symbols_named("offset")[0]
        self.assertEqual(offset.references, [(2, 0), (3, 37)])

    def test_lambda_vararg_not_merged_with_module_name(self):
        rests = self.index.symbols_named("rest")
        self.assertEqual(len(rests), 2)
        by_kind = {s.scope_kind: s for s in rests}
        self.assertEqual(by_kind["function"].definitions, [(3, 24)])
        self.assertEqual(by_kind["function"].references, [(3, 24)])
        # 字典推导式最左迭代源 rest 在模块解析,无本文件绑定。
        self.assertEqual(by_kind["module"].definitions, [])
        self.assertEqual(by_kind["module"].references, [(4, 30)])

    def test_dict_and_set_comp_scopes(self):
        k = self.index.symbols_named("k")[0]
        self.assertEqual(k.scope_kind, "comprehension")
        self.assertEqual(k.definitions, [(4, 25)])
        self.assertEqual(k.references, [(4, 9), (4, 12), (4, 25)])
        c = self.index.symbols_named("c")[0]
        self.assertEqual(c.scope_kind, "comprehension")
        self.assertEqual(c.definitions, [(5, 14)])
        self.assertEqual(c.references, [(5, 8), (5, 14)])
        word = self.index.symbols_named("word")[0]
        self.assertEqual(word.scope_kind, "module")
        self.assertEqual(word.definitions, [])
        self.assertEqual(word.references, [(5, 19)])


if __name__ == "__main__":
    unittest.main()
