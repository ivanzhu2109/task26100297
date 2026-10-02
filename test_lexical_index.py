import unittest

from lexical_index import SymbolIndex


class BasicBindingsTest(unittest.TestCase):
    SRC = """\
x = 1
x = x + 1
for x in [x]:
    y = x
y, z = x, x
x += 1
print(x, y, z)
"""

    @classmethod
    def setUpClass(cls):
        cls.idx = SymbolIndex(cls.SRC)

    def test_same_scope_same_symbol_all_definitions(self):
        # 同一作用域的同名绑定属于同一个符号，定义为全部绑定位置。
        self.assertEqual(
            self.idx.definitions_at(1, 0),
            [(1, 0), (2, 0), (3, 4), (6, 0)],
        )

    def test_references_include_bindings_and_reads(self):
        self.assertEqual(
            self.idx.references_at(2, 4),
            [(1, 0), (2, 0), (2, 4), (3, 4), (3, 10), (4, 8),
             (5, 7), (5, 10), (6, 0), (7, 6)],
        )

    def test_augassign_position_counted_once(self):
        refs = self.idx.references_at(6, 0)
        self.assertEqual(refs.count((6, 0)), 1)

    def test_unpacking_targets(self):
        self.assertEqual(self.idx.definitions_at(4, 4), [(4, 4), (5, 0)])
        self.assertEqual(self.idx.references_at(5, 0), [(4, 4), (5, 0), (7, 9)])
        self.assertEqual(self.idx.references_at(5, 3), [(5, 3), (7, 12)])

    def test_unbound_global_empty_definitions(self):
        self.assertEqual(self.idx.definitions_at(7, 0), [])
        self.assertEqual(self.idx.references_at(7, 0), [(7, 0)])

    def test_non_name_position(self):
        self.assertIsNone(self.idx.symbol_at(1, 2))
        self.assertEqual(self.idx.definitions_at(1, 2), [])
        self.assertEqual(self.idx.references_at(1, 2), [])


class FunctionScopeTest(unittest.TestCase):
    SRC = """\
def outer(q, r=q_default):
    print(q)
    if False:
        s = 1
    s = q
    def inner():
        return s + q
    return inner
"""

    @classmethod
    def setUpClass(cls):
        cls.idx = SymbolIndex(cls.SRC)

    def test_function_name_bound_in_outer_scope(self):
        self.assertEqual(self.idx.references_at(1, 4), [(1, 4)])
        self.assertEqual(self.idx.references_at(6, 8), [(6, 8), (8, 11)])

    def test_param_references(self):
        self.assertEqual(self.idx.definitions_at(1, 10), [(1, 10)])
        self.assertEqual(
            self.idx.references_at(2, 10),
            [(1, 10), (2, 10), (5, 8), (7, 19)],
        )

    def test_default_resolves_in_outer_scope(self):
        # 默认值 q_default 是模块级未绑定全局名，不属于任何形参。
        self.assertEqual(self.idx.definitions_at(1, 15), [])
        self.assertEqual(self.idx.references_at(1, 15), [(1, 15)])
        self.assertEqual(self.idx.references_at(1, 13), [(1, 13)])

    def test_unreachable_branch_binding_is_local(self):
        # 不可达分支中的赋值仍使 s 成为整个函数的局部符号。
        self.assertEqual(self.idx.definitions_at(5, 4), [(4, 8), (5, 4)])
        self.assertEqual(
            self.idx.references_at(7, 15),
            [(4, 8), (5, 4), (7, 15)],
        )

    def test_inner_function_reads_outer_symbols(self):
        self.assertEqual(
            self.idx.references_at(7, 19),
            [(1, 10), (2, 10), (5, 8), (7, 19)],
        )


class ClosureTest(unittest.TestCase):
    # 多层闭包混合提前读取、global/nonlocal 与默认值。
    SRC = """\
g = 0
def level1():
    print(a)
    a = g
    def level2(b=a):
        global g
        nonlocal a
        a = b + g
        def level3():
            return a + g
        return level3
    return level2
"""

    @classmethod
    def setUpClass(cls):
        cls.idx = SymbolIndex(cls.SRC)

    def test_global_read_write_and_nested_read(self):
        self.assertEqual(self.idx.definitions_at(1, 0), [(1, 0)])
        self.assertEqual(
            self.idx.references_at(8, 16),
            [(1, 0), (4, 8), (8, 16), (10, 23)],
        )

    def test_global_declaration_is_queryable_but_not_counted(self):
        # global 声明中的名字可查询，但声明位置不计入定义或引用。
        self.assertEqual(
            self.idx.references_at(6, 15),
            [(1, 0), (4, 8), (8, 16), (10, 23)],
        )

    def test_early_read_binds_to_local_symbol(self):
        # 赋值前的读取仍归属本函数的局部符号 a。
        refs = self.idx.references_at(3, 10)
        self.assertEqual(refs, [(3, 10), (4, 4), (5, 17), (8, 8), (10, 19)])
        self.assertEqual(self.idx.definitions_at(3, 10), [(4, 4), (8, 8)])

    def test_nonlocal_write_targets_outer_function(self):
        # level2 中的 a = ... 经 nonlocal 归属 level1，定义合并为同一符号。
        self.assertEqual(self.idx.definitions_at(8, 8), [(4, 4), (8, 8)])
        self.assertEqual(
            self.idx.references_at(10, 19),
            [(3, 10), (4, 4), (5, 17), (8, 8), (10, 19)],
        )

    def test_nonlocal_declaration_is_queryable_but_not_counted(self):
        self.assertEqual(
            self.idx.references_at(7, 17),
            [(3, 10), (4, 4), (5, 17), (8, 8), (10, 19)],
        )

    def test_default_value_uses_outer_symbol_not_param(self):
        # 默认值 b=a 中的 a 归属 level1 的局部 a，不是 level2 的形参。
        refs = self.idx.references_at(5, 17)
        self.assertEqual(refs, [(3, 10), (4, 4), (5, 17), (8, 8), (10, 19)])
        self.assertEqual(self.idx.references_at(5, 15), [(5, 15), (8, 12)])

    def test_distinct_scopes_not_merged(self):
        self.assertEqual(self.idx.references_at(2, 4), [(2, 4)])
        self.assertEqual(self.idx.references_at(5, 8), [(5, 8), (12, 11)])
        self.assertEqual(self.idx.references_at(9, 12), [(9, 12), (11, 15)])


class ComprehensionTest(unittest.TestCase):
    # 嵌套推导式与生成器：同名目标、外层迭代源、赋值表达式。
    SRC = """\
data = [1, 2]
total = 0
squares = [x for x in data if x > 0]
gen = (x for x in [x for x in data])
pairs = [(x, y) for x in data for y in [x]]
def f():
    total = 1
    vals = [(total := total + x) for x in data]
    return vals
"""

    @classmethod
    def setUpClass(cls):
        cls.idx = SymbolIndex(cls.SRC)

    def test_module_symbols(self):
        self.assertEqual(
            self.idx.references_at(1, 0),
            [(1, 0), (3, 22), (4, 30), (5, 25), (8, 42)],
        )
        self.assertEqual(self.idx.references_at(2, 0), [(2, 0)])
        self.assertEqual(self.idx.references_at(3, 0), [(3, 0)])

    def test_comprehension_target_is_local_and_private(self):
        # 列表推导式的 x 独立成符号，不向外泄漏。
        self.assertEqual(self.idx.definitions_at(3, 17), [(3, 17)])
        self.assertEqual(
            self.idx.references_at(3, 17),
            [(3, 11), (3, 17), (3, 30)],
        )

    def test_nested_same_name_targets_are_distinct(self):
        # 生成器的 x 与内层列表推导式的 x 是不同符号。
        self.assertEqual(self.idx.references_at(4, 13), [(4, 7), (4, 13)])
        self.assertEqual(self.idx.references_at(4, 25), [(4, 19), (4, 25)])

    def test_leftmost_iter_resolves_in_outer_scope(self):
        # 内层列表推导式的最左迭代源 data 归模块符号。
        self.assertEqual(
            self.idx.references_at(4, 30),
            [(1, 0), (3, 22), (4, 30), (5, 25), (8, 42)],
        )

    def test_non_leftmost_iter_resolves_inside(self):
        # 第二个 for 子句的迭代源 [x] 中的 x 是本推导式的局部目标。
        self.assertEqual(
            self.idx.references_at(5, 40),
            [(5, 10), (5, 20), (5, 40)],
        )
        self.assertEqual(self.idx.references_at(5, 34), [(5, 13), (5, 34)])

    def test_walrus_in_comprehension_binds_function_scope(self):
        # 推导式中的赋值表达式绑定最近的非推导式作用域（函数 f）。
        self.assertEqual(self.idx.definitions_at(8, 13), [(7, 4), (8, 13)])
        self.assertEqual(
            self.idx.references_at(8, 22),
            [(7, 4), (8, 13), (8, 22)],
        )

    def test_walrus_does_not_leak_to_module(self):
        # 模块级 total 与函数内 total 是不同符号。
        self.assertEqual(self.idx.references_at(2, 0), [(2, 0)])

    def test_walrus_value_reads_and_comp_target(self):
        self.assertEqual(self.idx.references_at(8, 37), [(8, 30), (8, 37)])
        self.assertEqual(self.idx.references_at(8, 4), [(8, 4), (9, 11)])


class WalrusDeclarationTest(unittest.TestCase):
    SRC = """\
counter = 0
def bump():
    global counter
    vals = [(counter := counter + 1) for _ in range(3)]
    return vals
def outer():
    n = 0
    def inner():
        nonlocal n
        vals = [(n := n + 1) for _ in range(2)]
        return vals
    return inner
"""

    @classmethod
    def setUpClass(cls):
        cls.idx = SymbolIndex(cls.SRC)

    def test_walrus_obeys_global_declaration(self):
        # 推导式中的赋值表达式服从目标作用域的 global 声明。
        self.assertEqual(self.idx.definitions_at(4, 13), [(1, 0), (4, 13)])
        self.assertEqual(
            self.idx.references_at(4, 24),
            [(1, 0), (4, 13), (4, 24)],
        )

    def test_walrus_obeys_nonlocal_declaration(self):
        self.assertEqual(self.idx.definitions_at(10, 17), [(7, 4), (10, 17)])
        self.assertEqual(
            self.idx.references_at(10, 22),
            [(7, 4), (10, 17), (10, 22)],
        )


class WalrusLambdaBoundaryTest(unittest.TestCase):
    SRC = """\
def f():
    vals = [lambda: (w := 1) for _ in range(2)]
    return w
"""

    @classmethod
    def setUpClass(cls):
        cls.idx = SymbolIndex(cls.SRC)

    def test_walrus_inside_lambda_binds_lambda_scope(self):
        # 赋值表达式与目标作用域之间隔着 lambda 时，归 lambda 自己的作用域。
        self.assertEqual(self.idx.references_at(2, 21), [(2, 21)])

    def test_outer_read_is_unbound_global(self):
        self.assertEqual(self.idx.definitions_at(3, 11), [])
        self.assertEqual(self.idx.references_at(3, 11), [(3, 11)])


class LambdaAndDefaultTest(unittest.TestCase):
    SRC = """\
v = 10
f = lambda v=v: v + 1
p = 1
def h(p=p):
    return p
"""

    @classmethod
    def setUpClass(cls):
        cls.idx = SymbolIndex(cls.SRC)

    def test_lambda_default_resolves_outer(self):
        self.assertEqual(self.idx.references_at(1, 0), [(1, 0), (2, 13)])
        self.assertEqual(self.idx.references_at(2, 11), [(2, 11), (2, 16)])

    def test_def_default_same_name_as_param(self):
        # 默认值中的同名名字不能误归到该函数的形参。
        self.assertEqual(self.idx.references_at(3, 0), [(3, 0), (4, 8)])
        self.assertEqual(self.idx.references_at(4, 6), [(4, 6), (5, 11)])


class AttributeAndKeywordTest(unittest.TestCase):
    SRC = """\
obj.run(value=x)
obj.attr = x
y = obj.other
"""

    @classmethod
    def setUpClass(cls):
        cls.idx = SymbolIndex(cls.SRC)

    def test_attribute_and_keyword_label_are_not_references(self):
        self.assertEqual(self.idx.references_at(1, 0), [(1, 0), (2, 0), (3, 4)])
        self.assertIsNone(self.idx.symbol_at(1, 4))   # run
        self.assertIsNone(self.idx.symbol_at(1, 8))   # value
        self.assertIsNone(self.idx.symbol_at(2, 4))   # attr
        self.assertIsNone(self.idx.symbol_at(3, 8))   # other

    def test_receiver_and_argument_value_are_references(self):
        self.assertEqual(self.idx.references_at(1, 14), [(1, 14), (2, 11)])


class DictCompAndStarredTest(unittest.TestCase):
    SRC = """\
d = {k: v for k, v in items}
a, *b = [1, 2, 3]
"""

    @classmethod
    def setUpClass(cls):
        cls.idx = SymbolIndex(cls.SRC)

    def test_dict_comprehension_scope(self):
        self.assertEqual(self.idx.references_at(1, 14), [(1, 5), (1, 14)])
        self.assertEqual(self.idx.references_at(1, 17), [(1, 8), (1, 17)])
        self.assertEqual(self.idx.definitions_at(1, 22), [])
        self.assertEqual(self.idx.references_at(1, 22), [(1, 22)])

    def test_starred_unpacking(self):
        self.assertEqual(self.idx.references_at(2, 0), [(2, 0)])
        self.assertEqual(self.idx.references_at(2, 4), [(2, 4)])


if __name__ == "__main__":
    unittest.main()
