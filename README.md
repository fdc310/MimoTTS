# MimoTTS

LangBot 插件，调用小米 MiMo TTS API 将文本转换为语音。

## 功能

- **自动语音合成**：LLM 回复自动转为语音发送
- **命令语音合成**：通过 `!tts` 命令手动合成语音
- **LLM 文本加工**：可配置 LLM 模型对回复进行口语化处理，提升语音自然度
- **多音色支持**：MiMo 默认 / 中文女声 / 英文女声
- **风格与口音**：支持设置方言口音（东北话、台湾腔、四川话、粤语等）
- **唱歌模式**：自动识别唱歌内容，使用专用风格合成

## 安装

1. 将本插件放入 LangBot 的插件目录
2. 在 LangBot 管理面板中启用插件
3. 前往 [api.xiaomimimo.com](https://api.xiaomimimo.com) 获取 API Key
4. 在插件配置中填入 API Key

## 配置项

| 配置项 | 类型 | 默认值 | 说明 |
|--------|------|--------|------|
| `api_key` | string | 空 | MiMo TTS API Key |
| `enable_auto_tts` | boolean | true | 自动将 LLM 回复转为语音 |
| `block_text_reply` | boolean | true | 拦截文字回复，仅发送语音。关闭则同时发送文字和语音 |
| `llm_model` | llm-model-selector | 空 | 文本加工 LLM 模型，留空则跳过加工直接合成 |
| `default_voice` | select | mimo_default | 默认音色（mimo_default / default_zh / default_en） |
| `default_style` | string | 空 | 语言口音/方言风格，如：东北话、台湾腔、粤语 |

## 命令

| 命令 | 说明 |
|------|------|
| `!tts <文本>` | 使用默认配置合成语音 |
| `!tts voice <音色> <文本>` | 指定音色合成 |
| `!tts style <风格> <文本>` | 指定风格合成 |
| `!tts help` | 显示帮助信息 |

### 示例

```
!tts 你好世界
!tts voice default_zh 今天天气真好
!tts style 开心 我太高兴了
!tts style 东北话 你这人可太有意思了
```

## 工作流程

1. 用户发送消息 -> LLM 生成回复
2. 插件拦截回复文本，清理思维链内容
3. 若配置了 LLM 加工模型，将文本转为口语化表达并添加风格标签
4. 若未配置加工模型，使用内置规则引擎清理 Markdown、emoji 等
5. 注入默认口音/方言风格
6. 按段落分段，逐段调用 MiMo TTS API 合成语音
7. 将语音以 Voice 消息发送

## 依赖

- `langbot-plugin`
- `openai>=1.0.0`
