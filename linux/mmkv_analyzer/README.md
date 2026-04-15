# MMKV Analyzer

Analyze an unencrypted Tencent MMKV data file plus its `.crc` metadata file.

分析未加密的 Tencent MMKV 数据文件，以及对应的 `.crc` 元数据文件。

Single file:

单文件分析：

```bash
python3 mmkv_analyzer.py path/to/mmkv_file path/to/mmkv_file.crc
```

Directory scan:

目录批量扫描：

```bash
python3 mmkv_analyzer.py path/to/mmkv_directory
```

Default reports are written to:

默认报告输出目录：

```text
D:/mmkv_analyzer_logs
```

Each run writes:

每次运行会输出：

- `*.json`: full structured report
- `*_keys.csv`: one row per final key
- `*_summary.txt`: compact human-readable summary
- `batch_*_index.json` and `batch_*_index.csv` in directory mode

- `*.json`：完整结构化报告
- `*_keys.csv`：每个最终 key 一行，方便表格查看
- `*_summary.txt`：简明文本摘要
- `batch_*_index.json` 和 `batch_*_index.csv`：目录模式下的批量索引

The key report includes:

key 报告包含：

- key name and raw key hex
- key length
- latest value length
- all historical value lengths for duplicate keys
- total bytes used by all occurrences of the key
- latest entry payload offset
- latest value SHA-256 prefix and hex preview
- CRC digest comparison with the `.crc` file when available

- key 名称和原始 key hex
- key 长度
- 最新 value 长度
- 重复 key 的所有历史 value 长度
- 该 key 所有历史记录占用的 value 字节数总和
- 最新 entry 在 payload 内的偏移
- 最新 value 的 SHA-256 前缀和 hex 预览
- 如果提供 `.crc` 文件，会对比 CRC digest

Directory mode:

目录模式：

- scans recursively
- skips `.crc`, `.lock`, `.tmp`, and `.bak` files as main-file candidates
- treats `file` plus `file.crc` as a matched MMKV pair
- also accepts `.mmkv` files without `.crc`
- records parse errors in the batch index and keeps analyzing the remaining files

- 递归扫描目录
- 跳过 `.crc`、`.lock`、`.tmp`、`.bak` 文件，不把它们当作主数据文件
- 自动把 `file` 和 `file.crc` 识别成一组 MMKV 文件
- 也接受没有 `.crc` 的 `.mmkv` 文件
- 单个文件解析失败时，会把错误写入 batch 索引，并继续分析剩余文件

Notes:

- MMKV keeps append-style historical entries. The last occurrence of the same key is treated as the current value.
- Encrypted MMKV files need the encryption key before key names can be decoded; this tool reports unencrypted files only.

说明：

- MMKV 是 append-style 存储，同一个 key 可能在文件里出现多次。工具会把最后一次出现的记录视为当前有效值。
- `occurrence_count` 表示同一个 key 在文件历史中出现过几次。
- `latest_value_length` 表示外层 value 的总字节数。
- `value_inner_length` 表示工具在 value 内部识别到的疑似内部 payload 长度，单位是字节；这是启发式判断，不等价于官方类型解析。
- 加密 MMKV 文件需要密钥才能解码 key 名称；这个工具目前只分析未加密文件。
