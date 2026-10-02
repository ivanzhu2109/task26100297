# 单文件词法符号索引库

基于 Python 源码的静态作用域归属(不执行用户代码),为编辑器上层提供
跳转定义与查找引用。源码与索引只保存在内存中,无任何外部服务依赖。

## 文件

- `symbol_index.py` — 库本体,仅依赖标准库 `ast`。
- `test_symbol_index.py` — 自动化测试(标准库 `unittest`)。

## 调用说明

```python
from symbol_index import build_index

index = build_index(source)          # source: 可被 Python 3.11 编译的源码字符串

# 按选中的名字查询(同名不同符号不合并,逐个返回)
for symbol in index.symbols_named("total"):
    print(symbol.scope_kind, symbol.definitions, symbol.references)

# 按光标位置查询(行从 1 开始,列从 0 开始,与 ast 模块一致)
symbol = index.symbol_at(line, column)        # 该处名字所属符号,无则 None
defs = index.definitions_at(line, column)     # 跳转定义:全部实际绑定位置
refs = index.references_at(line, column)      # 查找引用:绑定 + 读取位置
```

- `Symbol.definitions`:该符号的全部绑定位置,升序;无本文件绑定的全局
  名字仍归组为模块级符号,其定义集合为空。
- `Symbol.references`:含定义位置与读取位置,同一位置只计一次。
- `global`/`nonlocal` 声明中的名字可以用 `symbol_at` 查询,但声明本身
  不计入定义或引用结果。

## 运行测试

```sh
python3 -m unittest test_symbol_index -v
```

## 支持范围

- 绑定形式:函数名、形参、普通赋值、解包赋值、增强赋值、for 目标、
  推导式目标、赋值表达式;支持同步/异步函数、lambda 及普通表达式与控制流。
- 作用域:模块、函数(含 lambda)、推导式/生成器表达式(各自独立);
  普通控制流块不创建作用域。
- 不处理:类、导入、注解、装饰器、删除绑定及其他绑定形式;不做跨文件解析。
