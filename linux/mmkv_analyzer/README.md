# MMKV Analyzer

Analyze an unencrypted Tencent MMKV data file plus its `.crc` metadata file.

Single file:

```bash
python3 mmkv_analyzer.py path/to/mmkv_file path/to/mmkv_file.crc
```

Directory scan:

```bash
python3 mmkv_analyzer.py path/to/mmkv_directory
```

Default reports are written to:

```text
D:/mmkv_analyzer_logs
```

Each run writes:

- `*.json`: full structured report
- `*_keys.csv`: one row per final key
- `*_summary.txt`: compact human-readable summary
- `batch_*_index.json` and `batch_*_index.csv` in directory mode

The key report includes:

- key name and raw key hex
- key length
- latest value length
- all historical value lengths for duplicate keys
- total bytes used by all occurrences of the key
- latest entry payload offset
- latest value SHA-256 prefix and hex preview
- CRC digest comparison with the `.crc` file when available

Directory mode:

- scans recursively
- skips `.crc`, `.lock`, `.tmp`, and `.bak` files as main-file candidates
- treats `file` plus `file.crc` as a matched MMKV pair
- also accepts `.mmkv` files without `.crc`
- records parse errors in the batch index and keeps analyzing the remaining files

Notes:

- MMKV keeps append-style historical entries. The last occurrence of the same key is treated as the current value.
- Encrypted MMKV files need the encryption key before key names can be decoded; this tool reports unencrypted files only.
