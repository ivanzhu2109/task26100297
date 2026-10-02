# lexical_index — 单文件词法符号索引库

基于源码静态归属（`ast` 分析，不执行用户代码）的符号索引，供编辑器上层
实现"跳转定义"与"查找引用"。源码与索引只保存在内存中。

## 调用方式

```python
from lexical_index import SymbolIndex

idx = SymbolIndex(source)            # source: 可被 Python 3.11 编译的源码字符串

idx.symbol_at(line, col)             # -> Symbol | None，查询某个名字出现位置
idx.definitions_at(line, col)        # -> [(line, col), ...]，跳转定义
idx.references_at(line, col)         # -> [(line, col), ...]，查找引用
```

- 位置约定：行号从 1 开始、列偏移从 0 开始，与 `ast` 节点一致。
- 查询参数是编辑器选中的某个名字出现的位置（绑定、读取或
  global/nonlocal 声明中的名字均可）；非名字位置返回 `None` / 空列表。
- `Symbol.definitions`：该符号的全部实际绑定位置（同一作用域的同名绑定
  属于同一符号，不只最近一次赋值）。
- `Symbol.references`：绑定位置 + 读取位置，同一位置只计一次。
- 两者均按位置升序；无本文件绑定的全局名字定义集合为空。

## 作用域规则摘要

- 模块、函数/lambda、各类推导式与生成器表达式各自成作用域；普通控制流
  块不创建作用域。
- 函数内某名字只要有局部绑定（含不可达分支），整个函数体内均为局部。
- global 归模块；nonlocal 归最近一个有局部绑定的外层函数。
- 函数名绑在外层；默认值在外层解析；形参与函数体在新作用域。
- 推导式最左迭代源在外层解析，其余在内部；目标不外泄。
- 赋值表达式在推导式中时绑定最近的非推导式作用域，服从其
  global/nonlocal 声明；中间隔着 lambda 时归 lambda 自己。
- 属性名与调用关键字标签不是引用，接收者与参数值正常归属。

## 不处理

类、导入、注解、装饰器、删除绑定及 `with as`/`except as` 等其他绑定形式；
不做跨文件解析。

## 运行测试

```sh
python3 -m unittest test_lexical_index -v
```
