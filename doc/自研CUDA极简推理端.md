# 自研 CUDA 极简推理端

本专项的源码在 `native/cuda_voice_runtime/`。目标是用自研推理器完成语音包
从模型解析到音频合成的完整生命周期，且对用户机器的唯一要求是**已安装 NVIDIA
显卡驱动**：不安装 CUDA 工具链、不安装 ONNX Runtime、不需要 Python。

## 依赖边界

- 运行期：`nvcuda.dll`（驱动自带）+ Windows 系统 DLL。DLL 只静态链接 MSVC 运行库，
  外部依赖仅 `KERNEL32.dll`。
- 内核以 PTX 形式内嵌在 DLL 中，由驱动首次加载时 JIT 编译，因此同一份产物可以
  覆盖不同架构的 N 卡。目标 PTX 为 `compute_61`（Pascal / GTX 10 系及以上）：
  `compute_75` 的 PTX 驱动无法 JIT 到更老的卡上，Pascal 用户会直接拿不到设备。
  实测（RTX 3050，`fsv_kernel_bench`）`compute_61` 与 `compute_75` 无差别
  （`matmul_f32 m=1 k=2048 n=512` 28.65 / 28.82 µs，INT4 同形状 39.3 / 38.0 µs），
  而 `compute_50` 在同一批内核上慢 12%–22%，所以 61 是"白拿兼容性"的那一档。
  要改在 `native/cuda_voice_runtime/CMakeLists.txt` 的 `FSV_PTX_ARCH`
  （或 `tools/build_windows.cmd` 顶部）。
- PTX ISA 版本与目标架构是两件事：CUDA 12.6 不论目标为何都输出 `.version 8.5`，
  所以驱动仍需 555 系（2024 年）及以上，这一点不因把架构降到 `compute_61` 而改变。
  驱动过旧时加载 PTX 会失败，错误里带卡名、算力、驱动 CUDA 版本与内核 PTX 目标。
- 构建期：需要 `nvcc`（生成 PTX）与 Visual Studio（编译宿主 C++）。工具链只出现在
  开发机，不进入发行产物。

## 分层

| 层 | 文件 | 职责 |
| --- | --- | --- |
| 内核 | `src/kernels/fsv_kernels.cu` | 只含 `__global__` 设备代码，`extern "C"` 稳定符号名 |
| PTX 嵌入 | `cmake/embed_ptx.cmake` | 把 PTX 文本转成 DLL 内的字节精确字符串 |
| 驱动加载 | `src/host/nv_runtime.{h,cpp}` | `LoadLibrary` 取驱动入口、建上下文、JIT 模块、显存与核函数启动 |
| 算子宿主 | `src/host/cuda_ops.cpp` | 实现 `fsv_cuda_*` 对外接口 |
| 模型解析 | `src/host/onnx_graph_parser.cpp` | 自写 ONNX proto 解析与 external data 读取 |
| 图执行 | `src/host/graph_runtime.cpp` | ONNX 语义解释器，按需调用自研 CUDA 算子 |
| C ABI | `src/host/native_graph_bridge.cpp` | 导出 `fsv_graph_*` 模型级接口 |
| 文本前端 | `src/host/fsv_json.cpp`、`src/host/fsv_unicode.cpp`、`src/host/fsv_tokenizer.cpp` | 读取 tokenizer.json、BertNormalizer 语义、WordPiece 分词 |
| 码点表 | `src/host/fsv_unicode_tables.cpp` | 由 `tools/gen_unicode_tables.py` 生成，见“文本前端”一节 |

文本前端不依赖 CUDA，单独编译成静态库 `fsv_text_frontend`，运行时 DLL 与开发用
命令行工具都链接它。

对外头文件：`include/fsv_cuda_voice_runtime.h`（算子接口）与
`include/fsv_native_graph.h`（模型接口）。

## 构建与自检

```powershell
cmd /c native\cuda_voice_runtime\tools\build_windows.cmd
build\cuda_voice_runtime\Release\fsv_engine_smoke.exe
```

构建脚本会进入 VS x64 环境、把 `TEMP`/`TMP` 固定到 ASCII 目录并临时映射 `F:` 盘，
以规避 CUDA 12.6 对非 ASCII 路径的处理问题。

`fsv_engine_smoke.exe` 不依赖 Python 与 ONNX Runtime，直接比对自研内核与标量 CPU
参考实现，覆盖 MatMul（含 `m == 1` 的 GEMV 快路径与三种批量/广播形状）、
MatMulNBits(INT4)、elementwise、LayerNorm、Softmax、Conv2D 与 ConvTranspose1D，
以及只有设备入口的几个算子（标量广播、`scale_into`、`[outer][mid][inner]` 块归约、
GatherElements、`Where` 的连续/条件广播/左标量/右标量四种形状）。当前在 RTX 3050 上
38 例全部通过，实测最大绝对误差 3.1e-05，都在各自容差内。

`tools/fsv_kernel_bench.cpp`（目标 `fsv_kernel_bench`）是内核侧的单点计时工具：对
同一形状连续入队 200 次、末尾同步一次，报每次启动的微秒数与等效带宽。图级基准
分辨不出单个内核的改动（几千次启动里的一次变化会淹没在墙钟噪声里），判断内核是否
真的变快用它；输入形状用 `--package` 那套真实解码形状，不要用随手编的形状。

## 设备选择与不同 N 卡的兼容性

`fsv_cuda_active_device(char* name, size_t size)` 返回本进程选中的卡并把 `name`
填成 `GeForce RTX 3050 Laptop GPU (8.6, 4.0 GiB)`，没有可用卡时返回 -1，
`fsv_cuda_last_error` 给出原因。选址规则按优先级：

| 顺序 | 规则 | 理由 |
| --- | --- | --- |
| 1 | `AEMEATH_CUDA_VOICE_DEVICE=<序号>` 指定卡 | 双 N 卡机器（含"小显存独显 + 大显存独显"）必须能指定；`none` / `-1` 等于把设备路径整个关掉 |
| 2 | 只在显存 ≥ 3 GiB 的卡里比算力，算力相同比显存，再相同比序号 | 一次合成常驻约 2.7 GiB，2 GiB 卡第一次大分配就失败，选它等于选了唯一必然失败的卡 |
| 3 | 所有卡都 < 3 GiB 时判定"没有可用设备" | 与其让"N卡加速"开着却逐节点回退，不如让上层明确回退到 DirectML / CPU |

`AEMEATH_CUDA_VOICE_DEVICE` 按指令执行：序号不存在、值不是序号都直接报错，指定
小显存卡也照用（"强制"正是它的用途），并且照样绕过显存下限。`CUDA_VISIBLE_DEVICES`
不由本推理端解析——**驱动自己就认它**，本推理端枚举到的设备表已经是过滤后的结果；
实测在这台机器上（单卡）`CUDA_VISIBLE_DEVICES=7` 会让 `cuInit` 直接返回
`CUDA_ERROR_NO_DEVICE`，`CUDA_VISIBLE_DEVICES=0` 与不设该变量完全一致，所以再解析
一次等于对已经重映射过的表再做一次映射。

拒绝时的错误列出**每张卡**的名字、算力与显存，并给出"用
`AEMEATH_CUDA_VOICE_DEVICE=<显存最大的那张>` 强制使用"的建议——"没有 N 卡"和
"你的卡太小"对用户是两件事，旧版把两者都报成"无法打开 NVIDIA 设备"。

回收池的上限同样按选中卡的显存推算（`总显存/5`，4 GiB 卡上正好等于原来的
768 MiB），首次分配失败后的回落上限也一样（`总显存/40`），所以小显存卡不会先被
缓存占掉一截。3 GiB 卡上池上限降到 614 MiB。

`fsv_cuda_get_device_info` 与 `fsv_cuda_device_count` 的分工：前者只加载驱动、
不建上下文，因此没有可用卡时也能列出机器上的所有卡（诊断入口）；后者要完成选址，
返回 -1 的含义是"不可用"。

温度/驱动之外还有一条硬约束：PTX 由 `FSV_PTX_ARCH` 决定最低架构，`compute_61`
覆盖 Pascal 及以后（GTX 10 系、16 系、20/30/40 系与对应专业卡）。Maxwell
（GTX 9 系）需要 `compute_50`，而实测它会慢 12%–22%，因此不做默认；Kepler 及更老
的卡 CUDA 12.6 本身已不支持。

## 显存驻留

图执行把张量留在显存里，主机内存不再参与算子之间的数据传递：

- `fsv_launch_*` 只入队不同步，后续拷贝与核函数在默认流上自然排序，错误由下一次
  回读或同步报出。
- 常量（initializer）在图准备阶段注册进显存常量表，权重只上传一次。
- `Constant` 节点在图准备阶段求值一次并并入 initializer 表：导出的图每步带 461 个
  常量节点，61 步就是 28121 次重复物化，占一次合成节点数的四分之一。折叠之后它们
  同时走权重路径，取显存副本而不是每次上传。
- 算子结果默认留在显存；只有 CPU 兜底算子需要某个张量时才回读主机。
- 自回归缓存不进主机内存：`present_k/v_layer_*` 与同名 `past_k/v_layer_*` 在图加载
  时配对（`fsv_graph_set_resident`），输出以空载荷回给调用方，调用方把句柄原样递回
  就等价于喂缓存。回读次数从 7812 次降到 1956 次，上传字节从 3.24 GB 降到 0.92 GB。
- 图执行前统计每个名字被读取的次数，最后一次读取之后就交还显存缓冲，
  缓冲回到驱动池供后续算子复用；驱动池按大小取“最小的够用缓冲”，因为解码每步的
  序列都比上一步长，逐字节精确匹配会让几乎每个张量都新分配一次显存，
  而 `cuMemAlloc`/`cuMemFree` 会掐断上下文。改成向上取整复用后，
  一次合成的真实分配次数从 12379 降到 5002。
- 写入走页锁定暂存池（`cuMemHostAlloc` + 异步 `cuMemcpyHtoDAsync`），回读走阻塞
  `cuMemcpyDtoH`：回读本来就在等队列排空，异步形式要多花两次驱动调用和一次搬运，
  成对比较稳定快约 3%。
- 核函数入口按名字字面量的地址缓存，启动路径从每次的字符串哈希加锁降为一次查表。
- 显存被别的进程占满时不再逐节点回退：连续 8 次分配失败就把本轮整体判给主机
  （见“当前状态与后续门槛”），下一次图执行开始时自动复位重试。

第 4 条是必需的：小显存卡上如果让所有中间结果常驻，显存会被占满，WDDM 开始
把显存换出到内存，GPU 利用率掉到个位数而宿主反而满载。实测 RTX 3050 Laptop
  （4 GB）上，加上按需释放后整条 `synth` 链路的显存峰值从 3831 MiB 降到
  1891 MiB，`--mode replay` 总耗时从 >180 s（换页停顿）降到 6.6 s。

## 标准任务与基准

性能与一致性数字统一用同一句固定输入、固定 `seed`，任何后端之间都可以直接对比：

    帮我 check 今天 schedule，eight 点叫我。

31 字，中英混排，走 `text_lang="auto"` 的混排前端（G2PW + RoBERTa + g2p_en），
`seed=20260910`。测量入口是 `tools/dev_voice_benchmark.py`：只对
`synthesize_to_file` 计时，模型载入单独报告，并顺带打印两个后端的采样点一致性与
最大绝对误差，是改动图执行后的第一道回归门。

    py -3 native\cuda_voice_runtime\tools\dev_voice_benchmark.py --providers cpu,cuda --runs 3

判读只看同一次进程内的 cpu/cuda 比值。这台笔记本上后台进程（音乐播放器、聊天工具）
能把 CPU 侧的 `best` 从 2.9 s 抬到 14 s 以上，跨时段比较绝对数会得出完全相反的结论。

### 当前耗时（RTX 3050 Laptop 4 GB，安静机器）

| 后端 | 载入 | best | median | RTF |
| --- | --- | --- | --- | --- |
| ORT CPU | 10.4 s | 2.69 s | 2.75 s | 0.99 |
| 自研 CUDA | 6.9 s | 2.32 s | 2.34 s | 0.84 |

采样点 88960/88960 完全一致，最大绝对误差 3.1e-05。`runs=4` 的同进程比值 0.87×，
自研后端第一次稳定快过 CPU 基线。用户给出的目标是比 CPU 快一倍，即比值 0.5，
尚未达到。

历次优化的同进程比值：起点 3.29× → KV 显存驻留 2.64× → 设备结果不再分配主机
缓冲 2.19× → 常量折叠与驱动池复用 2.05× → Transpose 重写 1.44× → 连续布局
float4 快路径与 INT4 累加器拆分 1.28× → `scale`/归约设备化、GatherElements 与
Split 设备化、LayerNorm/Softmax 整行并行 1.00× → INT4 每列切分提到 32 路 1.00× →
`Where` 设备化 0.98× → 内核侧一轮（INT4 `uint4` 权重读取、conv2d 通道 tile、
`m == 1` GEMV）0.87×。

### 三档语音包（同一次会话，`--runs 3`）

| 包 | ORT CPU best/median | 自研 CUDA best/median | 比值 CUDA÷CPU | 音频 |
| --- | --- | --- | --- | --- |
| fp32 完整包 | 2.91 / 2.97 | 5.68 / 5.71 | 1.95× 更慢 | 90240 点一致，3.1e-05 |
| fp16 中等包 | 2.88 / 2.96 | 5.94 / 5.99 | 2.06× 更慢 | 90240 点一致，3.1e-05 |
| int8 节约包 | 2.81 / 2.87 | 2.39 / 2.55 | 1.18× 更快 | 88960 点一致，3.1e-05 |

三档的差别只在权重的量化格式：fp32 与 fp16 包的全部矩阵乘走 f32 batched MatMul，
int8 包把四个最大的形状换成 INT4，因此只有 int8 包稳过 CPU 基线。fp32/fp16 包一次
合成的驱动侧开销（`FSV_CUDA_STATS`，含载入）是 launch 60931 次 0.56 s、真实显存分配
19947 次 0.53 s、上传 9356 次 1.46 GB 0.63 s、回读 672 次 0.10 GB 0.72 s，合计
2.45 s。上传里 1380 MB 是 6 次图载入的权重驻留（`(weights)` 行 6 calls），真正落在
一次合成里的只有约 113 MB，所以上传不是这里的瓶颈；占时间的是每个节点一次启动、
每个中间张量一次显存分配，加上解释器自身的分派（见“开销分解”）。内核侧按
`fsv_kernel_bench` 的单价折算已只剩 1 s 以内，这两个包的 GPU 大部分时间在等主机。

本节的 int8 行与上表不是同一批测量（上表 `runs=4`），两者差 0.1 s 量级，在机器
抖动范围内。`runs=3` 的第一次 CUDA 调用含驱动 JIT 与显存首次落位，`median` 会被它
抬高（int8 包一次实测为 `[3.55, 2.55, 2.39]`）；判断内核改动有没有用，用 `best`
或者 `fsv_kernel_bench` 的逐内核数字。

### 开销分解

一次合成约 2.8 s。`FSV_NATIVE_OPSTATS` 给逐算子耗时与传输计数，`FSV_CUDA_STATS`
给驱动统计，`FSV_NV_GPU_TIMER` 给逐内核 GPU 时间，`FSV_NATIVE_SLOW_MS` 打印超过
阈值的单个节点——阈值设成 2 ms 时一次合成一条都不打印，说明耗时均匀摊在五万多个
节点上，没有单点。

**可靠的部分是驱动计数。** 它不依赖任何计时器，是唯一可以直接引用的口径。整个
进程（含载入与三次合成）：核函数 163559 次 1.50 s、真实显存分配 11402 次 0.23 s
（池命中 157566 次）、上传 14690 次 0.87 GB 0.72 s、回读 1983 次 0.23 GB 1.40 s，
驱动侧合计 3.85 s；显存 footprint 1969.4 MB、峰值 2701.5 MB。折算到一次合成：
约 54500 次启动（入队 0.50 s）、3800 次真实分配、4900 次上传 0.29 GB、661 次回读
0.077 GB。回读的 `busy` 列用**墙钟**减去事件内的真实拷贝耗时得到：1983 次阻塞拷贝
里 1.28 s 是队列排空、0.12 s 才是搬运，所以“一次合成里至少 0.4 s 是主机在等 GPU”
是可信的下界，不是估计。

**不可靠的部分是逐内核计时。** `FSV_NV_GPU_TIMER` 把每次启动的两个事件之间的墙钟
当内核时间，而 WDDM 按批次提交，批次边界上 GPU 会空转，空转被记进内核时间。它的
症状是：`fsv_where_contiguous_f32`、`fsv_reduce_block_f32`、`fsv_conv2d_f32_flat`
这些规模差几个数量级的内核都报 8 µs – 73 µs，`fsv_matmul_nbits4_f32` 在
`m=1 k=2048 n=512` 上报 0.203 ms/次，而那个形状每线程约 64 条 FMA、512 KB 权重
全命中 L2，指令数折算只有 1 µs 量级。**逐内核排序可以参考，绝对值和占比不要引用。**

**节点数**：一次合成约 89917 个节点，其中约 13000 个因 dtype 不是 float32 留在
主机（几乎全是形状链上的 int64 小张量），其余约 54000 个各对应一次核函数启动。
`Transpose` 有 19425 次调用而只有约 5000 次真正启动：布局恒等的转置已直接别名
输入，不产生内核。

**限速环节按包分叉：int8 包仍是 GPU，fp32/fp16 包已经变成主机。** `Where` 设备化
把回读从 951 次/合成降到 661 次/合成，但同期 `busy` 几乎没变，一次合成也只快了
约 2%；那些回读点上的等待不是流水线气泡，而是主机在等 GPU 做完本来就该做的工作。
内核侧那一轮之后，int8 包把四个最大的矩阵乘换成 INT4、GPU 侧反而松了，而
fp32/fp16 包的驱动侧一次合成就占 2.45 s（见“三档语音包”），GPU 在等人。所以：

1. 主机侧：一次合成有 54500 次启动、3800 次真实显存分配、90000 个节点的解释器分派
   （其中约 13000 个留在主机），fp32/fp16 包的 MatMul 分派一项就是 1.99 s / 7974 次；
   把只在形状上做算术的 int64 子图在准备阶段折掉能同时打这几项，这是现在最大的一块。
2. GPU 内核时间：INT4 矩阵乘与 `m == 1` 的 f32 batched MatMul 已经做完一轮
   （见下文第 2、5 条），剩下的是 1×1 卷积、约 3.6 万次逐元素/拷贝/转置启动，
   以及零散形状。

### 工具本身的开销（这一轮实测）

- 打开 `FSV_NATIVE_OPSTATS` 会把一次合成从 2.33 s 抬到 4.80 s（每个节点一次加锁的
  字符串查表加一次时钟读取）。**op-stats 的墙钟列只能用来排序，不能按百分比引用**，
  更不能拿它估算某个算子改完能省多少。
- 一个进程里第一次合成比之后每次慢约 2.4 s（cuda `[3.61, 2.34, 2.33]`，cpu 同样
  `[5.25, 2.73, 2.74]`）：那是 PTX 的首次 JIT 与显存、主机缓冲的首次落位。只比较
  `best`，`median` 会被它抬高。
- 上传的暂存池不是隐藏的同步点：整个进程约 14000 次上传只触发约 15 次
  `cuStreamSynchronize`（入口的 `stream_idle` 命中约 2000 次，其余都命中复用）。
  怀疑"上传在偷偷排空队列"时先量这个，不要凭感觉改。
- 阻塞回读：三次合成 1605 次、`busy` 1.25 s、真实搬运 0.11 s。`busy` 是墙钟减去
  搬运，也就是主机等队列排空的时间，**它可以在节点之间挪动**：`[op-slow]` 报某个
  节点 149 ms，通常指的是"它排到队尾时前面还压着 149 ms 的核函数"，不是那个算子
  自己花了 149 ms。这一轮把该节点的回读整段拿掉之后，端到端没有变化，正是这个原因。

**判据只有一个：改一处、看同进程 `best` 比值动不动**，并且改之前先确认机器是安静的
（见上一节）。不要用计时器去选优化点。

一个已经验证过的反例记在这里：让“小于 4096 元素的逐元素算子”回到主机执行
（`prefer_host`）会让一次合成慢 0.3 s。少数几百个元素的张量在卡上跑，
比主机实现里的 broadcast 展开加逐元素读写更快，所以判断“小算子放 CPU”不能只比
算术量。

一个不能再踩的坑：**不要用两个进程同时跑合成本测 GPU 争用**。4 GB 卡上单进程显存
峰值 2.6 GB，两个进程会把显存吃满、算子开始回退 CPU，一次合成从 2.8 s 涨到 100 s
以上，而且之后一段时间测量都不可信。要判断卡是不是饱和，用单进程的 `busy` 列。

一个已经验证过的反例记在这里：让“小于 4096 元素的逐元素算子”回到主机执行
（`prefer_host`）会让一次合成慢 0.3 s。少数几百个元素的张量在卡上跑，
比主机实现里的 broadcast 展开加逐元素读写更快，所以判断“小算子放 CPU”不能只比
算术量。

### batched MatMul 的 4 行分块（本轮）

`fsv_matmul_f32_batched` 原来一个线程只负责一行里的 4 个相邻列：每一行都要把同一份
k-major 权重重新读一遍，权重流量随行数线性增长，而算术量不变。实测它是一张 L1 带宽
受限的内核——`fsv_kernel_bench` 上这些形状按权重流量折算只有 2.2–14.5 GB/s、
约 400–600 GFLOPS，不到这张卡 fp32 峰值的 10%。

现在一个线程负责 4 个连续行、每行 4 个相邻列：权重 quad 在 4 行之间复用，权重流量
降到 1/4。每个输出仍然在同一个 slot 上累加，规约顺序没有改变。`fsv_kernel_bench`
（`--iters 50`，同机同驱动，`sum` 校验与前一版逐位相同）：

| 形状 | 改前 | 改后 | 加速 |
| --- | --- | --- | --- |
| m=212 k=512 n=512 | 225.3 µs | 95.6 µs | 2.36× |
| m=512 k=512 n=512 | 450.8 µs | 215.4 µs | 2.09× |
| m=212 k=2048 n=512 | 814.7 µs | 313.5 µs | 2.60× |
| m=212 k=512 n=1536 | 639.1 µs | 364.1 µs | 1.76× |
| m=212 k=512 n=2048 | 1126.2 µs | 521.9 µs | 2.16× |
| m=31 k=768 n=768 | 151.3 µs | 104.2 µs | 1.45× |
| m=212 k=212 n=32 batch=16 | 71.0 µs | 51.5 µs | 1.38× |

这些形状是该内核在语音包里的主力（非量化包里的 `MatMulNBits` 会整体变成这里的 f32
batched MatMul），所以 fp32/fp16 包是直接受益方；`m == 1` 仍走 GEMV 快路径，int8 包
几乎不碰这张内核。`fsv_engine_smoke` 的 13 个 matmul 用例（含 broadcast、split-k、
split-batch）改动后全部 PASS，int8 包端到端音频与改前一致（`best` 2.04 s 对 2.05 s，
最大绝对误差 6.1e-05 不变）。

下一步是共享内存分块：把一个 block 的 A/B tile 先落 smem，再让 4×4 的线程 tile 从
smem 取值，可以把“每 16 字节换 8 个 FMA”提到“每字节换 4 个 FMA”的量级。本轮没有做，
因为寄存器分块之后瓶颈已经离开 L1，收益要重新用同一套 bench 量。

### 长时间占用（连续 24 次合成实测）

既然确认“可以长时间占用”，这里直接量一次长时间占用下的稳定性：`tools/dev_voice_soak.py`
在同一个进程里连续合成，逐次打印耗时、`nvidia-smi` 的显存占用和与第一次的采样点最大差。
RTX 3050 Laptop 4 GB、int8 包、24 次：

- 耗时 `best 2.02 s / median 2.09 s / max 4.93 s`（第一次含 PTX 首次 JIT 与显存落位），
  前 5 次中位 2.07 s、后 5 次中位 2.19 s，漂移 +6.1%，在机器抖动范围内；
- 显存从 3186 MiB 降到 2857 MiB（−329 MiB）：池在复用后收敛，没有增长；
- 24 次音频与第一次逐位一致（最大绝对差 `0.000e+00`）；
- 驱动计数 24 次合计 `alloc=87297 (pool hit 983880) 1.20 s`、`alloc fail=0 abandon=0`、
  `device bytes owned=2501.8 MB peak=3245.8 MB`：真实分配每轮均值 3637 次，与单次基准的
  3800 次同量级，没有随轮次累积。

结论：自研后端可以长时间占用这张卡——显存不涨、结果不漂、性能不衰减；第一次合成的额外
约 2.4 s 是 PTX 首次 JIT 与显存首次落位，所以只比较 `best`。

## 设备快路径与前提

这些路径在 `src/host/graph_runtime.cpp` 的算子分派里按形状判定，判据本身就是正确性
条件：写错了不会报错，只会算错。改动前先看这一张表。

| 入口 | 触发条件 | 省掉的工作 |
| --- | --- | --- |
| `device_transpose` 布局恒等 | `perm` 重排后各轴 stride 不变 | 不建缓冲、不启动内核，直接把输入的显存句柄与形状交给输出 |
| `fsv_cuda_transpose_tile_f32` | 纯两轴交换、两轴的内侧连续段为 1 | 坐标遍历 |
| `fsv_cuda_transpose_swap_f32` | 纯两轴交换、内侧连续段 `inner` 满足 `inner % 4 == 0` | 坐标遍历，改走 float4 |
| `fsv_binary_contiguous_f32` | 两个操作数都按结果布局连续（`contiguous_strides`） | 广播规则与坐标遍历 |
| `fsv_binary_scalar_f32` | 一个操作数所有 stride 都是 0（单元素），另一个连续 | 每元素回读同一个标量 |
| `fsv_copy_contiguous_f32` | Slice/Concat/Split 的框两端都连续且偏移是 4 的倍数 | 坐标遍历 |
| `fsv_cuda_scale_into_f32` | Gemm 的 `alpha`、`beta` | 不再构造单元素张量、上传、再广播 |
| `fsv_cuda_reduce_block_f32` | 被归约的轴是连续的一段（`[outer][mid][inner]`） | 坐标遍历；累加用 double，与主机路径同精度 |
| `fsv_cuda_gather_elements_f32` | 数据是 float32、索引是 int64，且除被索引轴外形状一致 | 每步一次整张量回读 |
| `fsv_cuda_where_f32` | 条件是 bool(9)、两个操作数是 float32；三操作数全连续 / 一个操作数是单元素 / 通用坐标遍历三条子路径 | 主机实现要回读全部三个操作数，解码循环里每个生成帧排空一次队列 |
| `fsv_cuda_instance_norm_f32` | 输入 float32、rank ≥ 3，scale/bias 是逐通道的 float32 | 主机实现对整张激活逐元素回读并逐元素走两遍（hubert 的特征提取器一次合成 36 MB） |
| `fsv_cuda_argmax_i64` | 输入 float32，目标轴折成 `[outer][mid][inner]` | 解码每步为取一个下标回读整份 logits；扫描顺序与主机一致，相等取最小下标 |
| `fsv_cuda_fill_f32` + `fsv_cuda_copy_nd_f32` | Pad 的 `mode` 是 `constant`（或空）、pads 全为非负、数据 float32 | 主机实现回读操作数后逐元素走一遍输出；`edge`/`wrap`/`reflect` 仍走主机 |

`fsv_cuda_transpose_tile_f32`、`fsv_cuda_transpose_swap_f32`、
`fsv_cuda_reduce_block_f32`、`fsv_cuda_scale_into_f32`、`fsv_cuda_gather_elements_f32`
、`fsv_cuda_where_f32`（配 `fsv_cuda_where_index`）是公开 C 接口；改签名要同步
`include/fsv_cuda_voice_runtime.h`、`tools/fsv_engine_smoke.exe` 的用例和本文档。
`fsv_cuda_argmax_i64`、`fsv_cuda_instance_norm_f32` 与 `fsv_cuda_scatter_elements_f32`
同样在公开头文件里，加算子时一并补上自检用例。

`Where` 的三条子路径由 host 侧按 stride 选，判据本身就是正确性条件：连续路径要求
三个操作数都按结果布局连续，标量路径要求条件与非标量操作数连续且另一个操作数
全零 stride（`scalar_is_left` 区分除法一类的非交换情形），其余走通用坐标遍历。
注意 `all_zero_strides` 对 rank 0 恒真，所以 rank 0 必须先被连续路径接住。

`[op-stats]` 的 `busy` 列是该算子在阻塞回读里等 GPU 排空的时间。解释“回读墙钟远大于
字节数”时看这一列，不要按带宽估算。

## 接入程序

应用侧只有三个接缝：

| 位置 | 作用 |
| --- | --- |
| `lib/core/voice_runtime_contract.py` | 定位 DLL：`AEMEATH_CUDA_VOICE_RUNTIME` → 包内 `runtime/cuda-voice/` → `build/cuda_voice_runtime/Release/` → 共享根 `voice/runtimes/cuda-voice/` |
| `lib/script/gsvmove/native_graph.py` | `ctypes` 绑定 `fsv_graph_*`，对外是 ORT 同形的 Session |
| `lib/script/gsvmove/onnx_runtime.py` | `provider="cuda"` 时替换 `load_optional_external_session` |

`lib/script/gsvmove/hybrid_worker.py` 的 `CudaVoiceWorkerRuntime` 继承同一个低优先级
隔离 Worker，只是 `--provider cuda` 且不挂载 DirectML 覆盖层；主进程只校验 DLL 是否
存在，驱动可用性由 Worker 在启动握手中确认，不可用时按 DirectML → CPU 回退。

自研推理端没有 DirectML 那样的版本化虚拟环境，只有一个 DLL，因此不存在安装步骤；
开发机上直接使用 `build/cuda_voice_runtime/Release/` 的构建产物。

## 当前状态与后续门槛

- 已完成：driver-only 运行链路、PTX 内嵌、算子自检、DLL 零 CUDA 依赖、
  显存驻留与按需释放。
- 已完成：`ONNX_aimisiV2` 中语音合成链路的六张图（hubert、speaker、encoder、
  first/stage decoder、vits）在自研后端上跑通 `--mode replay` 与 `--mode synth`。
  RTX 3050 Laptop 上 `synth` 全链路 7.2 s，GPU 利用率均值 54%、峰值 98%，
  产出音频与 ORT 基线的最大绝对误差 2.2e-05。
- 已完成：`common/RoBERTa/RoBERTa.onnx` 与 `common/G2P/G2PW/g2pW_int4_fp16mix.onnx`
  两张文本前端图在自研后端上跑通 `--mode replay`，语音包内的 8 张图（6 张声学 +
  RoBERTa + G2PW）全部由自研推理器解析和执行，不需要 ONNX Runtime 与 Python。
- 已完成：语音包 RoBERTa 的 `tokenizer.json`（BertNormalizer + BertPreTokenizer +
  WordPiece + TemplateProcessing）在 `fsv_tokenizer.cpp` 中重写，逐 token 与
  Hugging Face `tokenizers` 完全一致。
- 已完成：接入程序。`lib/script/gsvmove/native_graph.py` 用 `ctypes` 绑定
  `fsv_graph_*`，对外呈现与 `onnxruntime.InferenceSession` 同形的
  `run`/`get_inputs`/`get_outputs`；`OnnxVoiceRuntime(provider="cuda")` 只替换
  `load_optional_external_session` 这一个接缝，六张声学图即全部改由自研推理端执行，
  语音包 `infer.py` 与上层 `GsvmoveService`、播放链路都不需要改动。
  “N卡加速”开关打开的端到端结果（RTX 3050 Laptop，`你好，我是爱弥斯。`，seed 12345）：
  与 ORT 基线的波形逐样本最大绝对误差 `6.1e-05`、RMS `5e-06`，采样点数完全一致；
  显存峰值 2250 MiB，GPU 利用率均值 41%、峰值 83%。
- 未完成：中文文本前端的规则层（GSV 文本归一化、jieba 分词与词性、
  pypinyin 兜底、OpenCC 简繁转换、变调与儿化）仍在 Python 侧，见下一节。
- 未完成：算子性能优化，见“标准任务与基准”一节的开销分解与下面的下一步清单。
- 未完成：生产默认后端切换。
- 已完成：**显存耗尽的整体退避**（曾经的风险：显存不足时逐节点回退到主机实现）。
  `fsv_cuda_device_alloc` 失败 → `ensure_device` 返回 0 → 设备算子 `device_decline`
  → 走主机实现，单进程显存峰值 2.6 GB，可用显存被别的程序占掉一块就会大面积回退：
  实测两个进程同时合成把 4 GB 吃满后，一次合成从 2.8 s 涨到 100 s 以上。现在
  `NvRuntime::acquire` 用 `g_alloc_streak` 记账，连续 `kAllocFailureLimit`（8）次分配
  失败即置 `g_device_abandoned`，此后 `acquire` 不再碰驱动，`cuda_host_enabled()`
  把本轮剩下的每个节点直接判给主机，`GraphRuntime::run()` 在开图前调用
  `fsv_cuda_reset_device_abandoned()` 复位。退避是“整轮”而不是“永久”：上一轮被拒的
  显存可能已经被释放，所以下一轮从零再试一次。设备路径整体关闭后，上层拿到的是 CPU
  基线的耗时，而不是逐节点回退的 100 s；`FSV_CUDA_STATS` 的 `[nv-stats]` 行新增
  `alloc fail=` 与 `abandon=` 计数，`fsv_cuda_device_abandoned()` /
  `fsv_cuda_reset_device_abandoned()` 是宿主查询与手动复位的公开入口。

性能现状：int8 包上自研后端已经快过 ORT CPU（同进程 `best` 比值 0.87×），
fp32/fp16 包仍是 1.95×–2.06× 更慢，离用户要求的 0.5× 都还差得远。已经消掉的几块
是逐张量的主机缓冲、每步重复物化的常量节点、
KV 缓存的往返、布局恒等/纯两轴交换的转置、连续布局的向量化拷贝与广播、
Gemm 标量、块归约，以及 GatherElements/Split/LayerNorm/Softmax/Where 的主机往返。
RoBERTa 与 G2PW 这两张小批量前端图继续刻意留在 ORT CPU 上。

本节的数字都来自一次安静机器上的会话；判断某次改动是否有效，只看同进程
cpu/cuda 的 `best` 比值。GPU 是当前的限速环节（依据见“开销分解”），所以剩下的
收益要同时从主机侧和内核侧拿，按下面的顺序试：

1. 形状子图折叠。一次合成 89917 个节点里约 13000 个是形状链上的 int64 小张量，
   另有 16780 次 `Reshape`、12056 次 `Unsqueeze`、6992 次 `Shape`、141 次
   `Squeeze` 是纯元数据操作。`Shape`/`Gather`/`Concat`/`Reshape` 链在准备阶段
   求值（有动态维则整链不折）能把这一步的分派开销全部拿掉，并把每步的节点数
   砍掉一大截。静态分析已确认 `vits_v2pro` 3193/6302、`RoBERTa` 984/2626、
   `t2s_stage_decoder` 471/1754 的节点可折。这是目前最大的单块剩余收益。
2. ~~INT4 矩阵乘~~（本轮已完成）。全部是 `m == 1` 的步进形状（`k=2048 n=512`、
   `k=512 n=1536`、`k=512 n=2048`）。每个 lane 现在按 `uint4` 读打包权重
   （16 字节对齐的组喂 8 次展开乘加），int8 包上该内核快 12%。剩下的空间在
   `k=512 n=1025` 这类零散形状上。
3. 主机侧启动次数。约 54300 次启动、每次入队 7.7 µs。合并逐元素算子链、
   把 Concat 的多操作数合成一次启动、把 Gemm 的 transpose 折进矩阵乘，
   都是几万个内核里成百上千次的量级；`Split`/`Slice` 的连续拷贝仍是一次启动
   一个框。
4. ~~设备化的回读点~~（本轮已完成 `ArgMax`/`Pad`/`InstanceNormalization`，其中
   `(graph-output)` 与 `TopK` 仍未设备化）。实测一次合成的阻塞回读从 539 次 /
   103 MB 降到 442 次 / 61 MB：`ArgMax` 的 126 次、`InstanceNormalization` 的
   36 MB 整张回读、`Pad` 的 98 次（只剩 1 次非 constant 模式）全部归零，新增的
   只有 `Pad` 每次两发启动。同进程 `best` 比值 0.85×–0.88×，与改动前持平——
   **回读的字节从来不是成本，成本是那一次排空**，去掉一个同步点只会把等待挪到
   下一个同步点，所以这一项按收益仍然排在形状子图折叠与内核之后。
5. 1×1 卷积与 batched MatMul。`m == 1` 的 batched MatMul 本轮换成了 GEMV 快路径：
   一个 256 线程的块负责 32 个输出列，块内 8 个 warp 各走 k 的一段（切分只在 warp
   之间，warp 内部仍是连续 32 列，否则 k-major 权重的访存会散成 32 条缓存行），
   8 份部分和经共享内存折叠一次。`m=1 k=2048 n=512` 单次启动 372.8 µs → 31.0 µs，
   同轮 fp16 包端到端 −14%（A/B：关掉该分支 6.75 s 中位，开启 5.68 s 中位）。
   `conv2d` 的输出通道 tile 也从固定 4 改成按 `out_channels % tile == 0` 在 16/8/4
   里挑最宽的一档，输入平面重读次数从 `out_channels/4` 降到 `out_channels/16`，
  但这一项还没有单独测量。`m > 1` 的 batched MatMul 本轮改成了 4 行 × 4 列的寄存器
  分块（见下文“batched MatMul 的 4 行分块”），`fsv_kernel_bench` 上这些形状快
  1.4×–2.6×。
6. 再决定前端图与默认后端的切换。

## 文本前端

`fsv_frontend_cli --tokenizer <tokenizer.json> tokenize <text-file>` 是分词器的
开发驱动：文本文件每行一例，逐例输出归一化文本、WordPiece 词元和 input ids。
`tools/dev_tokenizer_diff.py` 用 Hugging Face `tokenizers` 作参考实现，对同一批
语料逐项比对，是分词器改动的回归入口。

归一化表的来源是 `tools/gen_unicode_tables.py`：BertNormalizer 的行为是逐字符的，
所以整套语义可以归约为“控制字符”“空白”“CJK 加空格”“标点独立成词”四张码点区间表
外加一张“小写 + 去组合符”的展开表。生成器只在开发机运行（只依赖 CPython 标准库），
产物 `src/host/fsv_unicode_tables.cpp` 随源码提交，运行期不读任何数据文件。
Hangul 音节按 Unicode 算法展开，不进表。

已知差异：`tokenizers` 的码点分类表使用比 CPython `unicodedata` 更新的 Unicode
版本，阿拉伯文附加符、蒙古文自由变体选择符等少数冷门字符的分类会相差
（简体中文、英文、数字与常用标点不受影响）。

规则的文本前端（分词、拼音、简繁、归一化）尚未移植。实测影响：
把 jieba 分词换成逐字切分，20 句回归里有 7 句的音素序列发生变化（“朋友”
`peng2 you5` 变 `peng2 you3`、“试一试” `yi5` 变 `yi2` 等）；把 OpenCC `s2tw`
换成恒等映射，有 2 句变化（“银行”里 `行` 的读音、“胡同儿”的儿化）。这些数据
来自 `tools/dev_tokenizer_diff.py` 同目录的消融脚本思路，移植前需要先决定
jieba 词表与 posseg 概率表（约 11 MB）的落盘位置。离线包里这两份数据只由编译版
`jieba_fast` 携带（`dict.txt` 与 `posseg/prob_*.py`），纯 Python `jieba` 已经剪掉。
