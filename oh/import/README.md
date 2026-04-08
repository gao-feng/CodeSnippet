# HarmonyOS ArkTS MMKV Demo

这是一个最小可用的 HarmonyOS NEXT Stage 工程示例：

- 首页只有一个按钮
- 点击按钮后调用 `@tencent/mmkv`
- 演示 `encodeString()` 和 `decodeString()`

## 关键实现

- `entry/src/main/ets/entryability/EntryAbility.ts`
  - 在 `onCreate()` 中执行 `MMKV.initialize(appContext)`
- `entry/src/main/ets/pages/Index.ets`
  - 点击按钮后写入 `demo_message`
  - 立刻再读取并显示结果

## 依赖

根据 Tencent MMKV 的 HarmonyOS 文档，模块依赖已配置为：

```json
"dependencies": {
  "@tencent/mmkv": "2.2.2"
}
```

## 使用方式

1. 用 DevEco Studio 打开当前目录。
2. 配置签名。
3. 执行工程同步，或在 `entry` 模块执行 `ohpm install`。
4. 运行到模拟器或真机。

## 说明

- 工程级 `build-profile.json5` 已开启 `useNormalizedOHMUrl: true`，便于兼容三方 HAR/Bytecode 包。
- 如果你的 DevEco Studio / SDK 版本更高，IDE 可能会提示同步 hvigor 版本，按提示升级即可。
