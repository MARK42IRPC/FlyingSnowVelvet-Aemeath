# LZMA SDK（裁剪版）

来源：LZMA SDK 18.05（Igor Pavlov），<https://7-zip.org/sdk.html>，Public domain。

原生安装器只需要 raw LZMA2 的**解码器**，所以这里只保留：

| 文件 | 用途 |
| --- | --- |
| `LzmaDec.c` / `LzmaDec.h` | LZMA 解码核心，LZMA2 分片内部使用 |
| `Lzma2Dec.c` / `Lzma2Dec.h` | LZMA2 流解码（`Lzma2Dec_DecodeToBuf`） |
| `7zTypes.h`、`Compiler.h`、`Precomp.h` | 上述源码需要的类型与编译器适配 |

上游的编码器、7z 容器、命令行工具都没有进仓库。原始 LZMA2 流不自带字典大小，
所以 `Lzma2Dec_Allocate` 的属性字节（64 MiB = `0x1C`，对应
`installer/windows/src/zip_extract.h` 的 `FSV_ZIP_SHARD_DICT_PROPERTY`）必须与
`scripts/build_offline_installer.py` 的 `PAYLOAD_SHARD_DICT_PROPERTY` 一致；
打包时会断言，`tests/test_offline_installer_archive.py` 也会核对两处。

回归工具在 `build/zip-bench/lzma-trial/`：用 Python `lzma` 生成 raw LZMA2 流，
再用这里的解码器解回来比对 SHA-256。
