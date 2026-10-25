---
title: "张量基础：形状、布局与数据类型"
type: guide
domain: inference
status: active
one_liner: "从轴与形状运算出发，理解张量的布局、视图与数据类型，追踪模型计算中的数据变化。"
prereq: [linear-algebra]
related: []
sources:
  - https://docs.pytorch.org/docs/2.14/tensor_attributes.html
  - https://docs.pytorch.org/docs/2.14/tensor_view.html
  - https://docs.pytorch.org/docs/2.14/notes/broadcasting.html
  - https://docs.pytorch.org/docs/2.14/generated/torch.matmul.html
  - https://docs.pytorch.org/docs/2.14/generated/torch.Tensor.view.html
  - https://docs.pytorch.org/docs/2.14/generated/torch.Tensor.element_size.html
version: "v0.1"
---

# 张量基础：形状、布局与数据类型

上一篇用二维矩阵解释了线性层：一组输入特征经过权重组合，变成另一组特征。但真实模型通常同时处理多条输入，每条输入又包含多个 token。只写一个矩阵，已经不方便表达这些分组关系。

于是我们会遇到 `[B,S,D]`、`reshape`、`transpose`、`contiguous`、`bfloat16`。它们分别在描述什么？为什么两个形状相同的张量，元素排列甚至内存占用还可能不同？

本篇沿着三条线展开：**形状描述逻辑组织，布局描述索引如何对应存储，数据类型描述每个元素怎样表示。** 读完之后，你应当能追踪常见运算的形状，手算简单的存储位置，并辨别一次变化是否可能涉及数据拷贝。

本文把深度学习工程中的张量作为多维数组来介绍，不展开数学上更一般的张量定义与坐标变换理论。布局部分限定为普通稠密 strided 张量，连续性默认指 PyTorch 的标准连续格式；稀疏、分布式和硬件专用分块格式留给后续章节。

## 1. 从一批 token 的表示出发

假设一次处理两条输入，每条输入按三个位置组织，每个 token 用四个特征数表示。我们可以记为：

```text
X.shape = [2, 3, 4] = [B, S, D]

B：batch size，批大小，即这批有多少条输入
S：sequence length，序列长度，即每条输入组织了多少个位置
D：特征维度，即每个位置有多少个数

X[b, s, d]：第 b 条输入、第 s 个位置、第 d 个特征的值
```

下标从 0 开始。`X[1,2,3]` 就是第二条输入、第三个位置的第四个特征。

可以先把每条输入看成一个 `3 × 4` 的矩阵，再把两个矩阵沿批轴组织起来：

```text
                   特征 d →
第 0 条输入：   [ 0   1   2   3 ]  ← 位置 0
               [ 4   5   6   7 ]  ← 位置 1
               [ 8   9  10  11 ]  ← 位置 2

第 1 条输入：   [12  13  14  15 ]
               [16  17  18  19 ]
               [20  21  22  23 ]
```

数字只是方便追踪位置的编号，不是真实模型的激活值。实际批次的输入长度可以不同；这里假设已经补齐为统一长度，哪些位置有效需要另外记录，不能只靠 shape 判断。

本篇沿用 `B/S/D` 表示批、序列和特征，后面增加 `H` 表示头数、`d` 表示每头维度。符号是我们赋予各轴的含义，框架看到的通常只是轴的位置与长度。

## 2. 张量、形状与轴

### 2.1 有多少个轴，每个轴有多长

形状是各轴长度组成的有序序列。轴的数量叫作阶数，也常称维数或 `ndim`。

| 对象 | 形状示例 | 阶数 | 含义示例 |
|---|---|---|---|
| 标量 | `[]` | 0 | 一个分数 |
| 向量 | `[4]` | 1 | 一个 token 的四个特征 |
| 矩阵 | `[3,4]` | 2 | 三个位置的特征 |
| 三阶张量 | `[2,3,4]` | 3 | 两条输入的特征 |
| 四阶张量 | `[2,2,3,2]` | 4 | 按批、头、位置、每头特征组织的数据 |

标量张量没有轴，但包含一个元素；`[1]` 有一个长度为 1 的轴，同样包含一个元素。二者的阶数不同。

“维度”在口语里容易产生歧义：“三维张量”通常指三个轴，“特征维度为四”则指某一个轴的长度。写作或排障时，直接说明“轴数”或“第几轴的长度”会更清楚。

还要与上一篇的矩阵秩区分：一个形状为 `[3,4]` 的矩阵始终是二阶张量，但它的矩阵秩由数值决定，可以是 0、1、2 或 3。轴数不等于线性无关方向的数量。

### 2.2 元素总数与轴语义

普通稠密张量的逻辑元素数等于各轴长度的乘积：

```text
numel = B × S × D = 2 × 3 × 4 = 24
```

元素总数相同不表示组织方式相同。`[2,3,4]`、`[6,4]` 和 `[24]` 都有 24 个元素，但它们保留了不同的轴边界。

形状相同也不表示含义相同。`[2,3,4]` 可以表示批、序列、特征，也可以表示另外三种量。尤其当两条轴恰好等长时，把轴弄反往往不会触发形状错误。读公式和源码时，最好同时写出形状与轴名。

## 3. 索引、切片与维度增减

先用形状追踪，不急着讨论底层存储。对于 `X.shape = [2,3,4]`：

| 表达式 | 输出形状 | 在做什么 |
|---|---|---|
| `X[0]` | `[3,4]` | 取第一条输入，批轴消失 |
| `X[0:1]` | `[1,3,4]` | 取第一条输入，保留批轴 |
| `X[:,1,:]` | `[2,4]` | 每条输入取第二个位置，序列轴消失 |
| `X[:,1:2,:]` | `[2,1,4]` | 同样取第二个位置，保留序列轴 |
| `X[:,:2,:]` | `[2,2,4]` | 每条输入取前两个位置 |
| `X[...,0]` | `[2,3]` | 每个位置取第一个特征 |

这里 `:` 表示保留该轴的全部范围，`...` 表示补齐未显式写出的轴。切片采用左闭右开区间，所以 `1:2` 只取下标 1。

关键区别是：**用整数选定一个位置，会移除对应轴；用切片选一个范围，会保留该轴。** 这些例子属于基本索引；列表索引、布尔索引等还有其他规则，不在本节逐项展开。

如果下游操作需要显式的长度为 1 的轴，可以使用 `unsqueeze`：

```text
[3,4] --unsqueeze(0)--> [1,3,4]
```

反过来，`squeeze(0)` 可以移除第 0 轴，但只在该轴长度为 1 时生效。无参数的 `squeeze()` 会移除所有长度为 1 的轴；当批大小有时为 1 时，这可能让批轴意外消失，因此示例优先显式指定轴。

下面是一段可独立运行的 CPU PyTorch 代码。后续各 Python 代码块也可以独立执行。

```python
import torch

x = torch.arange(24).reshape(2, 3, 4)
assert tuple(x.shape) == (2, 3, 4)
assert x.ndim == 3 and x.numel() == 24
assert x[1, 2, 3].item() == 23
assert tuple(x[0].shape) == (3, 4)
assert tuple(x[0:1].shape) == (1, 3, 4)
assert tuple(x[:, 1, :].shape) == (2, 4)
assert tuple(x[:, 1:2, :].shape) == (2, 1, 4)
assert torch.equal(x[0].unsqueeze(0), x[0:1])
assert torch.equal(x[0:1].squeeze(0), x[0])
```

## 4. 运算如何决定输出形状

### 4.1 逐元素运算与广播

两个形状均为 `[B,S,D]` 的张量相加，是把相同索引处的数相加，结果仍为 `[B,S,D]`。这里的 `*` 通常也表示逐元素相乘，矩阵乘法使用 `@` 或 `matmul`。

如果要给每个 token 加上同一个偏置向量 `bias.shape = [D]`，不必手动生成 `B × S` 份偏置。支持广播的运算可以把它与 `[B,S,D]` 对齐。

广播从最右侧对齐各轴。缺失的前置轴按长度 1 理解；对齐后，每对轴的长度要么相等，要么其中一个为 1。本文例子各轴长度均为正数，输出在每对轴上取较大的长度。长度为 1 的轴在计算中沿另一侧的对应轴复用。[广播规则见官方说明](https://docs.pytorch.org/docs/2.14/notes/broadcasting.html)。

```text
输入：   [B, S, D]
偏置：         [D]
对齐：   [1, 1, D]
输出：   [B, S, D]
```

这是一条逻辑复用规则，不要求先把较小输入完整复制成大数组；运算输出本身是否分配存储则是另一个问题。

再看一个容易出错的例子。我们想给每条输入加一个不同的数，手中有 `[B]` 的权重：

```text
目标输入： [B, S, D]
错误对齐：       [B]    ← 对齐的是最后的特征轴，而不是批轴
正确形状： [B, 1, 1]    ← 一个值对应一条输入，沿 S、D 复用
```

如果 `B` 恰好等于 `D`，错误写法也能运行，却会按特征加值。形状兼容只说明运算合法，不能保证轴的语义正确。

```python
import torch

x = torch.zeros(2, 3, 2)  # 故意让 B == D
weight = torch.tensor([10.0, 20.0])
wrong = x + weight
right = x + weight[:, None, None]  # None 在该处插入长度为 1 的轴

assert wrong[0, 0].tolist() == [10.0, 20.0]
assert right[0, 0].tolist() == [10.0, 10.0]
assert right[1, 0].tolist() == [20.0, 20.0]
```

### 4.2 归约：哪些轴被合并成一个结果

求和、均值、最大值等操作，可以沿指定轴把多个元素合成一个结果。对 `[B,S,D]` 沿特征轴求和：

```text
X.sum(dim=-1)                → [B,S]
X.sum(dim=-1, keepdim=True)  → [B,S,1]
```

负轴号从后往前数，`-1` 表示最后一轴。第一种写法移除归约轴，第二种把它保留为长度 1。比如要让每个 token 的特征都减去该 token 的特征均值，`[B,S,1]` 就能直接沿特征轴广播回去。

```python
import torch

x = torch.arange(24, dtype=torch.float32).reshape(2, 3, 4)
mean = x.mean(dim=-1, keepdim=True)
centered = x - mean
assert tuple(mean.shape) == (2, 3, 1)
assert tuple(centered.shape) == (2, 3, 4)
assert torch.allclose(centered.mean(dim=-1), torch.zeros(2, 3))
```

这只是沿特征轴减均值，用来说明形状关系，不是完整的 LayerNorm。

### 4.3 批量矩阵乘法：最后两轴负责矩阵计算

上一篇的规则是 `[M,K] @ [K,N] → [M,N]`。当两侧都至少是二维张量时，`torch.matmul` 把最后两轴作为矩阵轴，前面的轴作为批轴，按广播规则对齐。[完整定义还包含一维输入的特殊处理](https://docs.pytorch.org/docs/2.14/generated/torch.matmul.html)。

```text
[B,S,D]   @ [D,O]      → [B,S,O]
[B,H,M,K] @ [B,H,K,N]  → [B,H,M,N]
```

第一行表示每条输入的 `S × D` 矩阵共用同一个 `D × O` 权重。第二行则是在每个批、每个头上，各做一次矩阵乘法。相乘的内侧长度 `K` 必须相等；这些矩阵轴不靠广播来补齐。

后面阅读注意力时，会见到类似形状：

```text
Q：                     [B,H,Sq,d]
K：                     [B,H,Sk,d]
K.transpose(-2,-1)：     [B,H,d,Sk]
Q @ K.transpose(-2,-1)： [B,H,Sq,Sk]
```

`Sq` 和 `Sk` 分别表示查询位置数和键位置数。这一步为每个查询位置与每个键位置产生一个点积；本篇只检查形状，注意力的缩放、掩码和归一化在模型结构篇展开。

## 5. 改形状与换轴有什么区别

### 5.1 reshape：按逻辑顺序重新划分轴边界

从一个简单矩阵开始：

```text
A.shape = [2,3]
A = [0  1  2]
    [3  4  5]
```

`A.reshape(3,2)` 按原来的逻辑顺序读取元素，再按新形状组织：

```text
[0  1]
[2  3]
[4  5]
```

元素总数仍为 6。`reshape` 中最多允许一个 `-1`，表示用元素总数推断该轴长度，例如 `A.reshape(-1)` 得到 `[6]`。

### 5.2 transpose / permute：改变轴的次序

`A.transpose(0,1)` 交换行轴与列轴，满足 `T[j,i] = A[i,j]`：

```text
[0  3]
[1  4]
[2  5]
```

两个结果的形状都是 `[3,2]`，但元素对应关系不同。reshape 保留逻辑遍历顺序来重分组，转置则让原来的行列索引交换角色。

`transpose(a,b)` 交换两条轴；`permute(...)` 按给出的原轴编号重排所有轴。例如：

```text
原轴编号：       0  1  2  3
原形状：       [B, S, H, d]
permute(0,2,1,3)
新形状：       [B, H, S, d]
```

做多头拆分时，通常先把特征轴 `D` 拆成 `H × d`，再交换序列轴与头轴。假设 `D = H × d`：

```text
[B,S,D] --reshape--> [B,S,H,d] --permute--> [B,H,S,d]
```

仅写 `reshape(B,H,S,d)`，虽然元素数相符，却没有表达所需的轴交换。两个写法在某些退化尺寸或相同数值下可能碰巧一致，不能据此当作通用替代。

```python
import torch

a = torch.arange(6).reshape(2, 3)
r = a.reshape(3, 2)
t = a.transpose(0, 1)
assert r.tolist() == [[0, 1], [2, 3], [4, 5]]
assert t.tolist() == [[0, 3], [1, 4], [2, 5]]
assert r.shape == t.shape and not torch.equal(r, t)
```

## 6. 从逻辑张量到底层存储

### 6.1 shape 以外，还需要什么

上节描述的是元素的逻辑对应关系。实现这些关系，不一定要立刻搬动数据。

对普通稠密 strided 张量，可以把它理解为“一块存储，加上一份解释这块存储的元数据”。元数据至少需要说明形状、步长和起始偏移；数据类型决定如何解释元素，设备决定数据在哪里。[PyTorch 的 strided 张量以 storage 和 stride 表达这种关系](https://docs.pytorch.org/docs/2.14/tensor_attributes.html)。

| 信息 | 回答的问题 |
|---|---|
| storage | 底层数据放在哪块存储中 |
| shape | 每条逻辑轴有多长 |
| stride | 某条轴的下标增加 1，需要在存储中移动多少个元素位置 |
| storage offset | 第一个逻辑元素相对底层存储起点偏移多少个元素位置 |
| dtype | 一个元素用什么类型、占多少字节 |
| device | 数据位于 CPU 还是某个加速设备 |

本文的“布局”指上述索引与存储的组织关系，不要把它等同于单个 `torch.layout` 属性。原矩阵与转置矩阵都可以是 `torch.strided`，却有不同的 stride；它们的数据类型也可以完全相同。

### 6.2 手算一个元素在哪里

用刚才的 `A`，假设底层存储如下：

```text
存储位置：  0   1   2   3   4   5
元素值：   [0] [1] [2] [3] [4] [5]

A.shape  = [2,3]
A.stride = [3,1]
A.offset = 0
```

沿行轴向后走一行，跳过 3 个元素位置；沿列轴走一列，只需跳过 1 个位置。因此：

```text
A[i,j] 对应的存储位置 = offset + i × stride[0] + j × stride[1]
A[1,2] 的位置        = 0 + 1 × 3 + 2 × 1 = 5
```

高阶张量同理：把每个下标乘以对应 stride，再加上 offset。这里 stride 和 offset 的单位都是元素位置；要算相对存储起点的字节偏移，还需乘以每元素字节数。

转置之后 `T = A.transpose(0,1)`：

```text
T.shape  = [3,2]
T.stride = [1,3]
T.offset = 0

T[2,1] 的位置 = 0 + 2 × 1 + 1 × 3 = 5
```

从 `A[1,2]` 变成 `T[2,1]`，访问的仍是同一个存储位置。改变 shape 和 stride，就能表达转置，无须先把六个元素重新排列。

切片也可能改变偏移。例如 `A[1:,:]` 从第二行开始，shape 为 `[1,3]`，stride 仍为 `[3,1]`，offset 则变为 3。

### 6.3 连续性描述逻辑顺序与存储顺序的关系

对于所有轴长度大于 1 的普通标准连续张量，最后一轴 stride 为 1，向前每一轴的 stride 等于后面各轴长度的乘积。比如 `[2,3,4]` 对应 `[12,4,1]`。

它意味着：按最后一轴最快变化的逻辑顺序遍历，正好顺着存储依次读取。长度为 1 或 0 的轴有特殊情况，不必套用这个简化条件，代码里可以直接问 `is_contiguous()`。

转置后的 `T` 按行读取时，依次访问位置 `0,3,1,4,2,5`，所以它不是标准连续张量。底层存储依然是那一块；“非连续”描述的是当前张量的逻辑顺序与存储的对应关系。

这也说明连续性不要求 offset 为 0。前面的整行切片 `A[1:,:]` 虽然从位置 3 起步，仍可连续读取这一行的三个元素。

### 6.4 视图、拷贝与 contiguous

共享底层存储、采用不同元数据解释数据的张量，称为视图。共享区域中的原地修改可能相互可见。PyTorch 的基本切片、`transpose` 和 `permute` 可以产生视图；不能把所有索引方式都当成视图，进阶索引的取值行为不同。[官方 Tensor Views 文档列出了具体操作及边界](https://docs.pytorch.org/docs/2.14/tensor_view.html)。

几个常用操作需要分清：

| 操作 | 应当怎样理解 |
|---|---|
| `view(shape)` | 请求共享存储的新形状；shape 与 stride 不兼容时会报错 |
| `reshape(shape)` | 请求新形状；能共享存储时返回视图，否则可能拷贝 |
| `transpose` / `permute` | 改变轴的对应次序，得到视图；不保证结果连续 |
| `contiguous()` | 已满足目标连续格式时返回自身，否则按当前逻辑顺序拷贝为连续张量 |
| `clone()` | 显式取得数据拷贝；不要用 `contiguous()` 代替“必须独立存储”的要求 |

`view` 也不是“只有连续张量才能用”。某些非连续张量的目标形状仍与原 stride 兼容，可以成功；准确条件见 [view 的官方定义](https://docs.pytorch.org/docs/2.14/generated/torch.Tensor.view.html)。初学时先判断是否需要换轴，再决定怎样改形状；不要把一次报错直接归因于元素总数。

`contiguous()` 保持当前逻辑上的值与形状。对转置后的 `T` 调用它，会按 `0,3,1,4,2,5` 的顺序形成新的连续排列，不会把逻辑矩阵变回原来的 `A`。

```python
import torch

a = torch.arange(6).reshape(2, 3)
t = a.transpose(0, 1)
assert a.stride() == (3, 1)
assert t.stride() == (1, 3)
assert a.is_contiguous() and not t.is_contiguous()
assert a[1:, :].storage_offset() == 3
assert a[1:, :].is_contiguous()

t[0, 1] = 30
assert a[1, 0].item() == 30  # 转置视图共享该元素

c = t.contiguous()
assert torch.equal(c, t)
assert c.stride() == (2, 1) and c.is_contiguous()
c[0, 1] = -1
assert a[1, 0].item() == 30  # 此处 c 已有独立的数据拷贝

try:
    t.view(-1)
except RuntimeError:
    pass
else:
    raise AssertionError("本例转置后的 stride 不支持直接 view(-1)")

flat = t.reshape(-1)
assert flat.tolist() == [0, 30, 1, 4, 2, 5]
```

视图能省去一次立即拷贝，但不代表后续算子一定更快。具体算子是否支持某种 stride、内部是否重新整理数据、访问模式怎样影响速度，需要在对应实现和硬件上验证。本篇先建立判断语言，不给出“非连续一定慢”或“连续化一定提速”的结论。

## 7. 数据类型与存储大小

### 7.1 同一个形状，可以装不同类型的数

`shape` 只描述元素的组织方式。每个元素如何编码，由 dtype 决定。常见的普通 PyTorch 类型包括：

| dtype | 每元素字节数 | 常见用途示例 |
|---|---|---|
| `torch.bool` | 1 | 有效位置、条件和布尔掩码 |
| `torch.int8` / `torch.uint8` | 1 | 小整数或部分量化表示中的存储 |
| `torch.int32` | 4 | 整数计数、部分索引接口 |
| `torch.int64` | 8 | 常见 token ID 与索引张量 |
| `torch.float16` | 2 | 模型权重与激活的一种浮点表示 |
| `torch.bfloat16` | 2 | 模型权重与激活的另一种浮点表示 |
| `torch.float32` | 4 | 浮点计算、对照实验和部分中间量 |
| `torch.float64` | 8 | 需要更高浮点精度的部分数值计算 |

用途取决于接口要求，不能仅凭本表决定转换。这里的普通 bool 张量按每元素 1 字节存储，不应自行按 1 bit 估算。某些压缩或打包格式有另外的存储规则。

token ID 是离散编号；浮点向量才是模型随后使用的一类表示。把整数 ID 转成浮点数，并不等于得到 embedding。类似地，把浮点权重简单转成 int8，不构成一个完整的模型量化方案。

FP16 与 BF16 每元素同为 2 字节，但能表示的数值范围与精度不同。本节只记住“占用相同不代表数值行为相同”，具体位编码、舍入、溢出和低比特格式留给《数值格式》。[类型定义见 PyTorch Tensor Attributes](https://docs.pytorch.org/docs/2.14/tensor_attributes.html)。

### 7.2 类型转换会改变什么

显式的 `.to(dtype=...)` 按目标类型表示各元素，保持逻辑形状，但不保证所有原值都能被精确保留。例如把小数转成整数会丢掉小数部分；更小的浮点格式也可能引入舍入。

这里不要混淆 `view(shape)`：它是在兼容条件下改变形状的解释方式，不是在做数值类型转换。PyTorch 另有 `view(dtype)` 重解释接口，本篇不使用。

混合类型运算可能发生类型提升，但规则并非简单地“谁字节数大就用谁”，有些操作还要求输入类型一致。示例显式指定 dtype，阅读实际模型时也应查看关键输入和输出，不能只从一个权重张量推断所有计算类型。输入、输出的存储 dtype 与算子内部累加精度也可能不同。

### 7.3 逻辑字节数不等于进程总占用

对本篇这类普通稠密、固定每元素大小的类型，可以计算逻辑数据字节数：

```text
逻辑字节数 = numel × element_size
```

`element_size()` 返回单个元素占用的字节数，见[官方接口定义](https://docs.pytorch.org/docs/2.14/generated/torch.Tensor.element_size.html)。例如 `[2,3,4]` 有 24 个元素，用 FP32 表示是 96 字节，用 BF16 表示是 48 字节。

```python
import torch

x = torch.arange(24, dtype=torch.float32).reshape(2, 3, 4)
y = x.to(dtype=torch.bfloat16)
assert x.shape == y.shape
assert x.numel() * x.element_size() == 96
assert y.numel() * y.element_size() == 48
assert torch.equal(y.float(), x)  # 本例小整数可精确表示，不是对任意值的保证

f = torch.tensor([1.75, -2.75], dtype=torch.float32)
assert f.to(torch.int64).tolist() == [1, -2]
assert torch.tensor([True, False]).element_size() == 1
```

这个公式计算的是当前张量的逻辑元素量。它不包含 Python 对象、张量元数据、分配器缓存、算子工作区等，也不保证等于为它独占分配的存储：

- 一个很小的切片可能仍持有原来的大块存储。
- 多个视图可能共享同一块存储，把各自逻辑字节数相加会重复计数。
- 广播视图可以让多个逻辑位置引用同一个元素，逻辑元素数也不等于独立存储元素数。

因此，用 shape 和 dtype 可以先建立容量账本，但估算整个模型或设备峰值占用时，还需要分析共享关系与存活时间。后续代价模型和显存管理篇会继续展开。

## 8. 串起来：一次多头形状变换

现在把前面的概念放进同一个小实验。取 `B=2`、`S=3`、`D=4`，把四个特征分成 `H=2` 个头，每头 `d=2` 个特征。

```text
操作                       形状            stride（本例）
初始连续张量              [2,3,4]         [12,4,1]
拆分特征轴                [2,3,2,2]       [12,4,2,1]
交换序列轴与头轴          [2,2,3,2]       [12,2,4,1]
按当前逻辑顺序连续化      [2,2,3,2]       [12,6,2,1]
```

拆轴让 `d_total = h × d + j`，其中 `j` 是头内特征下标；换轴让我们可以按“第几个头”来访问整段序列。因此：

```text
heads[b,h,s,j] = X[b,s,h×d+j]
```

这是应当验证的元素对应关系。只看目标 shape 正确，还不够。

```python
import torch

B, S, H, d = 2, 3, 2, 2
D = H * d
x = torch.arange(B * S * D, dtype=torch.float32).reshape(B, S, D)
split = x.reshape(B, S, H, d)
heads = split.permute(0, 2, 1, 3)
packed = heads.contiguous()

assert split.stride() == (12, 4, 2, 1)
assert heads.stride() == (12, 2, 4, 1)
assert packed.stride() == (12, 6, 2, 1)
assert not heads.is_contiguous() and packed.is_contiguous()
assert torch.equal(heads, packed)

for b in range(B):
    for h in range(H):
        for s in range(S):
            for j in range(d):
                assert heads[b, h, s, j] == x[b, s, h * d + j]

wrong = x.reshape(B, H, S, d)
assert wrong.shape == heads.shape
assert not torch.equal(wrong, heads)

restored = packed.permute(0, 2, 1, 3).reshape(B, S, D)
assert torch.equal(restored, x)

# 只验证上一节预告的矩阵乘法形状；不实现完整 Attention。
scores = heads @ heads.transpose(-2, -1)
assert tuple(scores.shape) == (B, H, S, S)
assert tuple((x @ torch.ones(D, 5)).shape) == (B, S, 5)
print("形状、stride、元素映射与恢复检查通过")
```

这里最后的 `reshape` 可以按需要拷贝；不要仅凭代码没有写 `contiguous()` 就认定整条路径没有数据搬运。实验验证的是数值与形状关系，不是性能结论。

### 自测

1. `[2,3,4]` 的张量有多少个轴、多少个元素？如果某个 `3 × 4` 切片的矩阵秩是 1，会改变张量的阶数吗？
2. 想从 `[B,S,D]` 中取第二个位置并保留序列轴，应当写 `X[:,1,:]` 还是 `X[:,1:2,:]`？
3. 要给每个 token 加一个不同的标量，手中有 `[B,S]` 的值，应如何调整形状？
4. 为什么 `[2,3]` reshape 成 `[3,2]`，不等于转置？
5. 二维张量的 stride 为 `[1,3]`、offset 为 0，下标 `[2,1]` 对应第几个存储位置？如果 dtype 为 FP32，字节偏移是多少？
6. 转置后的张量调用 `contiguous()`，会恢复成转置之前的值和形状吗？
7. `[2,3,4]` 的 BF16 张量逻辑数据量是多少字节？若两个变量只是它的视图，可以直接按两份数据计算新增存储吗？
8. 为什么 `reshape(B,H,S,d)` 的输出形状正确，也可能没有正确完成多头拆分？

### 答案与提示

1. 三个轴，24 个元素。矩阵秩描述数值的线性关系，不改变轴数。
2. `X[:,1:2,:]`，形状为 `[B,1,D]`。
3. 增加最后一轴，得到 `[B,S,1]`，例如 `values.unsqueeze(-1)`，然后沿特征轴广播。
4. reshape 按原逻辑顺序重分组；转置满足 `T[j,i]=A[i,j]`，改变的是索引对应关系。两者元素排列通常不同。
5. 从 0 开始计数的位置 `2×1+1×3=5`，相对底层存储起点为 `5×4=20` 字节。
6. 不会。它保持转置后的逻辑值与形状，必要时改变物理排列，使当前逻辑顺序连续。
7. `2×3×4×2=48` 字节。视图共享存储，不能直接按变量个数重复计算新增数据存储。
8. 它没有表达序列轴与头轴的交换；应检查 `heads[b,h,s,j]=X[b,s,h×d+j]`，而不只检查 shape。

## 承接后续章节

读模型公式时，可以先给每个轴标明含义，按运算规则推导输出形状，再检查轴的拆分与交换，最后查看 stride 和 dtype。这能把“形状不对”“元素对应错误”“发生了拷贝”“数值表示变化”分开定位。

接下来的概率与信息论为分布、采样和精度比较建立语言；《数值格式》进一步解释同样的数如何被不同类型编码。进入模型结构后，本篇的形状追踪方法会用于 embedding、多头注意力和 FFN；进入算子与系统后，布局和存储关系会帮助解释数据搬运与显存占用。

## 参考与修订

本文的概念讲解以普通多维数组为起点，API 行为核对 PyTorch 2.14 官方文档。本文七段 Python 示例已在 Python 3.12.14、PyTorch 2.14.1 的 CPU 环境逐段执行，全部断言通过。验证未使用 GPU，也未测量性能；版本不同的环境应再次执行示例，不将某个 API 行为直接推广到所有框架。

- [Tensor Attributes：dtype、device、layout 与 stride](https://docs.pytorch.org/docs/2.14/tensor_attributes.html)
- [Tensor Views：共享存储、视图与拷贝](https://docs.pytorch.org/docs/2.14/tensor_view.html)
- [Broadcasting semantics：广播对齐规则](https://docs.pytorch.org/docs/2.14/notes/broadcasting.html)
- [torch.matmul：批量矩阵乘法](https://docs.pytorch.org/docs/2.14/generated/torch.matmul.html)
- [Tensor.view：形状与 stride 的兼容条件](https://docs.pytorch.org/docs/2.14/generated/torch.Tensor.view.html)
- [Tensor.element_size：每元素字节数](https://docs.pytorch.org/docs/2.14/generated/torch.Tensor.element_size.html)

| 日期 | 版本 | 改动 |
|---|---|---|
| 2026-10-24 | v0.1 | 首稿：轴与形状、索引与广播、reshape 与换轴、stride 与视图、dtype 与逻辑字节数；附 CPU PyTorch 实验和自测 |
