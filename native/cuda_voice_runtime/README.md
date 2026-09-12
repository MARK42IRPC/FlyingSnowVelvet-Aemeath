# FSV CUDA 极简语音推理端

面向当前 `ONNX_aimisiV2` 语音包的自研推理器：自己解析 ONNX、自己执行算子、
自己管理显存，运行期只依赖 NVIDIA 显卡驱动。

## 目录

```
include/              对外 C 接口（算子接口 + 模型接口）
src/kernels/          只有设备代码，构建期编译为 PTX
src/host/             驱动加载、算子宿主、ONNX 解析、图执行、C ABI
                      fsv_json/fsv_unicode/fsv_tokenizer 是文本前端，编译为静态库
                      fsv_unicode_tables.cpp 由 tools/gen_unicode_tables.py 生成
cmake/embed_ptx.cmake 把 PTX 嵌入 DLL
tools/build_windows.cmd 构建脚本（仅开发机需要 nvcc 与 VS）
tools/fsv_engine_smoke.cpp 纯 C++ 自检，无 Python / ORT 依赖
tools/fsv_frontend_cli.cpp 文本前端开发驱动（分词等）
tools/dev_*.py         只在开发机跑的参考实现/夹具工具
tools/gen_unicode_tables.py 生成 src/host/fsv_unicode_tables.cpp
```

## 构建

```powershell
cmd /c native\cuda_voice_runtime\tools\build_windows.cmd
build\cuda_voice_runtime\Release\fsv_engine_smoke.exe
```

## 诊断开关

都是环境变量，默认全关，只影响诊断输出，不改变数值结果：

| 变量 | 作用 |
| --- | --- |
| `FSV_CUDA_STATS` | 进程退出时打印驱动统计：核函数启动、显存分配、上传、回读的次数与耗时 |
| `FSV_NATIVE_OPSTATS` | 每次图执行结束打印逐算子的耗时、调用数，以及该算子触发的降级、上传、回读次数 |
| `FSV_NATIVE_TRACE` | 逐节点打印形状与 dtype |
| `FSV_NATIVE_CAPTURE` | 保留全部中间结果供比对工具读取 |
| `FSV_NATIVE_CPU_ONLY` | 完全禁用设备路径，用于取 CPU 参考值 |

## 设备选择

- `fsv_cuda_active_device(name, size)` 返回本进程选中的卡，`name` 填成
  `GeForce RTX 3050 Laptop GPU (8.6, 4.0 GiB)`；`fsv_cuda_device_count()` 返回
  -1 表示没有可用设备，原因见 `fsv_cuda_last_error`。
- 默认只在显存 ≥ 3 GiB 的卡里挑（一次合成常驻约 2.7 GiB），先比算力、再比显存、
  最后比序号。2 GiB 卡在第一次大分配上就会失败，选它等于选了唯一必然失败的卡。
- 多卡机器用 `AEMEATH_CUDA_VOICE_DEVICE=<序号>` 指定，`none` / `-1` 把设备路径
  整个关掉。序号不存在或值不合法直接报错，指定小显存卡也照用。
- 显存被其它进程占满时不再逐节点回退：连续 8 次分配失败即整轮切到主机实现，
  `FSV_CUDA_STATS` 里记在 `alloc fail=` 与 `abandon=`，下一次图执行开始时自动复位重试。
  `fsv_cuda_device_abandoned()` 查询本轮是否已退避，`fsv_cuda_reset_device_abandoned()`
  手动复位。
- `CUDA_VISIBLE_DEVICES` 由驱动处理，本推理端枚举到的已经是过滤后的设备表，
  因此不再重复解析。
- `fsv_cuda_get_device_info(index)` 只加载驱动，没有可用卡时也能列出所有卡，
  是"为什么用不了"的诊断入口；拒绝信息会列出每张卡的名字、算力与显存。

读这些数字时注意两点。`FSV_NATIVE_OPSTATS` 的传输计数只覆盖图执行自己发起的
传输（`ensure_host` / `ensure_device`），`cuda_ops.cpp` 里 `*_host` 形式的算子在
内部做的上传与回读不在其中，两者相加才是驱动统计的总量。逐算子耗时是累计值，
跨图累计，做差分才能得到单张图的数字。

## 算子快路径

主机实现先要把手上的张量回读成字节，所以解码链路上的算子都配了设备路径，分派在
`src/host/graph_runtime.cpp`，每条路径的判据（也是正确性条件）列在
`doc/自研CUDA极简推理端.md` 的“设备快路径与前提”表里。已经进公开头文件
`include/fsv_cuda_voice_runtime.h` 的有：`GatherElements`、`ScatterElements`、
`Where`、`ArgMax`、`InstanceNormalization`、块归约、通道仿射、标量缩放与转置
快路径；`Pad` 的 constant 模式复用 `fsv_cuda_fill_f32` + `fsv_cuda_copy_nd_f32`，
不单独出接口。

加一个设备算子按“设计约束”第 2 条办：`fsv_engine_smoke` 里补 CPU/CUDA 对照用例，
再把接口、内核、分派与文档绑进 `tests/test_cuda_runtime_compat.py` 的断言里。

## 设计约束

1. 运行期不得引入 cudart、cuBLAS、ONNX Runtime 或任何 CUDA 工具链组件。
2. 新增算子必须同时给出 CUDA/CPU 对照，并纳入 `fsv_engine_smoke`。
3. 生产代码不直接依赖本目录；切换默认后端必须等真实语音包回归通过。
4. 内核改动后必须重新生成 PTX（构建脚本自动完成），不得手工编辑生成物。
5. 文本前端的码点表由生成器产出，也不得手工编辑；改表要改生成器再重跑。

## 图执行约定

`GraphRuntime` 把“值在哪里”当作执行的一部分，改算子时要照顾到：

- 每个 `Tensor` 的载荷是共享的；`ensure_host` 用 `resize_shared` 就地扩容，
  因为交给算子的是一份拷贝，而字节必须让这份拷贝和值表里的原对象都看得见。
  设备结果用 `device_tensor()` 建，载荷是这个张量独占的空槽（`reset_unique`），
  不能借用所有空张量共用的那份，否则某次回读会把字节写进别人的缓冲。
- 设备算子不要在 `make_tensor` 里先分配主机缓冲：那 12 GB 一次合成的分配与清零
  会立刻被丢弃，用 `device_tensor` 只声明形状与类型。
- `Constant` 节点在 `prepare()` 里求值并注册成 initializer（`fold_constants`），
  子图一起处理；`run()` 里 `values` 由 initializer 表播种，所以常量名照旧可见。
- `fsv_graph_set_resident` 声明“输出留给下一轮的某个输入”：这类输出以空载荷返回，
  调用方把句柄递回来即等价于喂缓存，`fsv_graph_read_resident` 是唯一把它物化成
  字节的入口。没有 `present_*`/`past_*` 配对的图完全不受影响。

## 与应用的关系

应用只通过 `lib/script/gsvmove/native_graph.py`（`ctypes` 绑定 `fsv_graph_*`）
和 `lib/core/voice_runtime_contract.py`（DLL 定位）使用本目录的产物，读取的是
`build/cuda_voice_runtime/Release/` 或发布包内的 DLL，不引用源码树里的任何文件。
应用侧只把六张声学图交给自研推理端，文本前端与采样循环仍由语音包的 Python 负责。
