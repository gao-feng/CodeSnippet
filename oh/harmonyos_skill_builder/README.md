# HarmonyOS Skill Builder

从任意华为开发者 HarmonyOS 文档页生成一个 Codex skill 目录。华为文档中心入口页是 SPA，工具默认使用文档站点的 JSON 接口下载目录树和正文：

- `SKILL.md`：skill 入口说明和完整页面清单
- `references/index.json`：下载页面索引
- `references/pages/*.md`：由文档页面转换出的 Markdown 参考资料

如果传入 `--summarize-skill`，工具还会调用 OpenAI 兼容的 Chat Completions API，根据已下载页面总结出更具体的 `SKILL.md` 描述、工作流和重点主题。

## 使用

在仓库根目录运行：

```bash
python -m oh.harmonyos_skill_builder.harmonyos_skill_builder \
  --start-url https://developer.huawei.com/consumer/cn/doc/harmonyos-guides/arkts \
  --output-dir oh/harmonyos_skill_builder/build/arkts \
  --overwrite
```

也可以在项目目录内运行：

```bash
cd oh/harmonyos_skill_builder
python -m harmonyos_skill_builder \
  --start-url https://developer.huawei.com/consumer/cn/doc/harmonyos-guides/arkts \
  --overwrite
```

如果不传 `--skill-name`，会从 URL 最后一段自动推导；如果不传 `--output-dir`，会输出到 `build/<skill-name>`。

## 任意 HarmonyOS 文档页

把 `--start-url` 换成目标 HarmonyOS 文档页即可。默认会下载该页面在华为文档目录树中的节点及其所有子文档。

```bash
python -m harmonyos_skill_builder \
  --start-url https://developer.huawei.com/consumer/cn/doc/harmonyos-guides/arkts-ui-development \
  --overwrite
```

对于上面的 URL，默认会生成：

```text
build/arkts-ui-development/SKILL.md
build/arkts-ui-development/references/index.json
build/arkts-ui-development/references/pages/*.md
```

## 常用参数

```bash
python -m harmonyos_skill_builder \
  --start-url https://developer.huawei.com/consumer/cn/doc/harmonyos-guides/arkts \
  --source api \
  --language cn \
  --output-dir build/arkts \
  --skill-name arkts \
  --max-pages 0 \
  --delay 0.2 \
  --overwrite
```

参数说明：

- `--start-url`：HarmonyOS 文档页 URL，格式通常为 `/consumer/cn/doc/<catalog>/<document-slug>`
- `--source`：默认 `api`，通过华为文档 JSON 接口抓取；可设为 `html` 做普通 HTML 链接爬取
- `--language`：接口抓取语言，默认 `cn`
- `--scope-prefix`：仅 `--source html` 使用，限制爬取范围的 URL path 前缀；不传则使用 `--start-url` 的 path
- `--output-dir`：生成的 skill 目录；不传则为 `build/<skill-name>`
- `--skill-name`：写入 `SKILL.md` frontmatter 的 skill 名称；不传则从文档 URL 自动生成
- `--max-pages`：最多下载页面数；默认 `0` 表示下载入口链接对应目录树下的全部文档
- `--delay`：请求间隔，默认 0.2 秒
- `--overwrite`：允许覆盖输出目录内的 `SKILL.md` 和 `references`

## 使用 LLM 总结 SKILL.md

开启总结需要 OpenAI 兼容接口。默认读取：

- `OPENAI_API_KEY`：API key
- `OPENAI_BASE_URL`：接口地址，默认 `https://api.openai.com/v1`
- `OPENAI_MODEL`：模型名，默认 `gpt-4o-mini`

示例：

```bash
python -m harmonyos_skill_builder \
  --start-url https://developer.huawei.com/consumer/cn/doc/harmonyos-guides/deveco-testing \
  --output-dir build/deveco-testing \
  --summarize-skill \
  --llm-model gpt-4o-mini \
  --overwrite
```

也可以显式传参：

```bash
python -m harmonyos_skill_builder \
  --start-url https://developer.huawei.com/consumer/cn/doc/harmonyos-guides/deveco-testing \
  --summarize-skill \
  --llm-api-key "$OPENAI_API_KEY" \
  --llm-base-url https://api.openai.com/v1 \
  --llm-model gpt-4o-mini \
  --llm-max-input-chars 60000 \
  --overwrite
```

未传 `--summarize-skill` 时不会调用模型，仍生成原来的模板版 `SKILL.md`。

## 安装为命令

```bash
cd oh/harmonyos_skill_builder
python -m pip install -e .
harmonyos-skill-builder \
  --start-url https://developer.huawei.com/consumer/cn/doc/harmonyos-guides/arkts \
  --overwrite
```

生成后，可以把对应的 `build/<skill-name>` 复制或同步到 Codex skills 目录使用。
