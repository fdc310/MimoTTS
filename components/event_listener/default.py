from __future__ import annotations

import base64
import logging
import re
from typing import Any

from langbot_plugin.api.definition.components.common.event_listener import EventListener
from langbot_plugin.api.entities import events, context
from langbot_plugin.api.entities.builtin.platform import message as platform_message
from langbot_plugin.api.entities.builtin.provider import message as provider_message
from components.tts_utils import call_mimo_tts, get_api_key

logger = logging.getLogger(__name__)

# LLM 提示词：分析文本并生成适合 TTS 的版本（支持角色扮演场景对话）
TTS_PROCESS_PROMPT = """你是一个语音合成文本优化助手。你需要将 AI 回复文本转换为 MiMo TTS 引擎能生动表达的格式。

## MiMo TTS 支持的能力

1. **整体风格标签**：在文本最开头使用 `<style>风格</style>`，如：
   - `<style>温柔</style>今天天气真好呢`
   - `<style>开心 俏皮</style>哇，太棒了！`

2. **细粒度音频标签**：在文本中间用英文半角括号 `(描述)` 控制语气变化，如：
   - `(紧张，深呼吸)呼……冷静，冷静。`
   - `(小声)哎呀，被发现了吗…`
   - `(语速加快，碎碎念)应该没问题的应该没问题的`
   - `(长叹一口气)算了吧…`
   - `(提高音量)喂！等等我！`

## 你的任务

根据用户消息和 AI 回复，完成以下转换：

### 1. 识别对话类型
- **普通对话**：正常的问答、闲聊
- **角色扮演/场景对话**：包含动作描述、情绪标注、场景描写（通常在括号内）

### 2. 角色扮演场景的处理规则
原文中的括号描述分为两类：
- **可转为音频的**（情绪、语气、声音变化）→ 保留并转为英文半角括号 `(描述)` 格式
  - 例：`(声音渐渐变小)` → `(声音渐渐变小)`
  - 例：`(紧张地)` → `(紧张地)`
  - 例：`(小声嘟囔)` → `(小声嘟囔)`
- **纯视觉动作**（无法用声音表达）→ 转换为对应的声音情绪描述
  - 例：`(脸颊泛红地低着头)` → `(害羞地，小声)`
  - 例：`(猛地站起来)` → `(激动地)`
  - 例：`(眼眶微红)` → `(哽咽)`
  - 例：`(双手捂脸)` → `(害羞，闷闷地)`
  - 如果视觉动作对语音没有意义，直接删除

### 3. 通用处理规则
- 去除 Markdown 格式（加粗、斜体、标题、列表、代码块、链接等）
- 将数字、缩写转为口语化表达
- 如果文本超过 300 字，适当精简但保留核心内容和情绪节奏
- 选择最合适的整体 `<style>` 标签放在最开头

### 4. 输出格式
严格按以下格式返回，不要包含任何解释：
<style>风格标签</style>处理后的文本

## 示例

输入：
用户消息：小白在干嘛呢？
AI回复：(脸颊泛红地低着头)那个…小白刚才进去的时候好像在偷偷练习新曲子呢…明明已经唱得那么好了，还要继续努力的样子…(声音渐渐变小，像蚊子一样轻柔)不过…要是能听到她用温柔的声音给我弹一首歌的话…应该会更开心吧…

输出：
<style>温柔 害羞</style>(害羞地，小声)那个…小白刚才进去的时候好像在偷偷练习新曲子呢…明明已经唱得那么好了，还要继续努力的样子…(声音渐渐变小，像蚊子一样轻柔)不过…要是能听到她用温柔的声音给我弹一首歌的话…应该会更开心吧…"""


class DefaultEventListener(EventListener):

    async def initialize(self):
        await super().initialize()

        @self.handler(events.PersonNormalMessageReceived)
        async def on_person_msg(event_context: context.EventContext):
            """保存用户原始消息到请求变量，供后续 TTS 处理使用"""
            user_text = event_context.event.text_message
            await event_context.set_query_var("mimo_tts_user_text", user_text)

        @self.handler(events.GroupNormalMessageReceived)
        async def on_group_msg(event_context: context.EventContext):
            """保存用户原始消息到请求变量，供后续 TTS 处理使用"""
            user_text = event_context.event.text_message
            await event_context.set_query_var("mimo_tts_user_text", user_text)

        @self.handler(events.NormalMessageResponded)
        async def on_responded(event_context: context.EventContext):
            """LLM 回复后，加工文本并合成语音"""
            config = self.plugin.get_config()

            if not config.get("enable_auto_tts", False):
                return

            api_key = await get_api_key(self.plugin)
            if not api_key:
                logger.warning("[MimoTTS] API Key 未配置，跳过语音合成")
                return

            response_text = event_context.event.response_text
            if not response_text or not response_text.strip():
                return

            voice = config.get("default_voice", "mimo_default")
            llm_model_uuid = config.get("llm_model", "")

            # 获取用户原始消息
            user_text = ""
            try:
                user_text = await event_context.get_query_var("mimo_tts_user_text") or ""
            except Exception:
                pass

            # 使用 LLM 加工文本（如果配置了模型）
            tts_text = response_text
            if llm_model_uuid:
                tts_text = await _process_text_with_llm(
                    self.plugin, llm_model_uuid, user_text, response_text
                )
            else:
                # 无 LLM 模型，使用规则引擎做基本转换
                tts_text = _fallback_clean(response_text)
                default_style = config.get("default_style", "")
                if default_style:
                    tts_text = f"<style>{default_style}</style>{tts_text}"

            if not tts_text.strip():
                return

            logger.info(f"[MimoTTS] 准备合成语音，文本: {tts_text[:80]}...")

            audio_data = await call_mimo_tts(api_key, tts_text, voice)
            if audio_data is None:
                logger.error("[MimoTTS] 语音合成失败")
                return

            data_url = f"data:audio/wav;base64,{base64.b64encode(audio_data).decode()}"
            await event_context.reply(
                platform_message.MessageChain([
                    platform_message.Voice(url=data_url),
                ])
            )
            logger.info(f"[MimoTTS] 语音回复发送成功，音频大小: {len(audio_data)} 字节")


async def _process_text_with_llm(
    plugin: Any, llm_model_uuid: str, user_text: str, response_text: str
) -> str:
    """使用 LLM 分析对话语境，生成带 style 标签和音频标注的 TTS 优化文本"""
    try:
        prompt_content = (
            f"用户消息：{user_text}\n\n"
            f"AI回复：{response_text}"
        )

        llm_response = await plugin.invoke_llm(
            llm_model_uuid=llm_model_uuid,
            messages=[
                provider_message.Message(role="system", content=TTS_PROCESS_PROMPT),
                provider_message.Message(role="user", content=prompt_content),
            ],
        )

        result = llm_response.content if hasattr(llm_response, "content") else str(llm_response)
        if result and result.strip():
            logger.info(f"[MimoTTS] LLM 加工结果: {result[:100]}...")
            return result.strip()

    except Exception as e:
        logger.error(f"[MimoTTS] LLM 加工文本失败: {e}")

    # 回退：规则引擎基本转换
    return _fallback_clean(response_text)


# ─── 视觉动作 → 音频情绪 映射表 ───
_VISUAL_TO_AUDIO = [
    # 害羞类
    (re.compile(r"脸[颊红]|脸颊泛红|面红|红着脸|满脸通红"), "害羞地，小声"),
    (re.compile(r"低[着下]头|垂[下着]眼"), "低声"),
    (re.compile(r"捂[住着]?脸|遮[住着]?脸"), "害羞，闷闷地"),
    # 悲伤类
    (re.compile(r"眼[眶圈][微泛]?红|泪[水光]|流泪|哭[泣了]"), "哽咽"),
    (re.compile(r"抹[去掉]?[了]?眼泪|擦[掉去]?泪"), "抽泣着"),
    # 激动类
    (re.compile(r"猛[地的]?站|一拍桌|握紧拳"), "激动地"),
    (re.compile(r"跳[起了]来|蹦[起了]"), "兴奋地"),
    # 紧张类
    (re.compile(r"紧[紧握]?[握攥]|咬[着紧]?[着了]?[唇嘴]|吞[了口]"), "紧张地"),
    (re.compile(r"颤抖|发抖|哆嗦"), "颤抖着声音"),
    # 安静类
    (re.compile(r"轻轻[地的]?[笑叹]|微微[笑叹]"), "轻声"),
    (re.compile(r"沉默|无言|不语|安静"), "沉默片刻"),
    # 开心类
    (re.compile(r"笑[着了]|咧嘴|嘴角上扬|露出笑"), "开心地"),
    (re.compile(r"眼睛[亮发]光|双眼放光|星星眼"), "兴奋地"),
]


def _convert_action_bracket(match: re.Match) -> str:
    """将括号内的描述转换为 TTS 可用的音频标签"""
    content = match.group(1).strip()

    # 已经是音频相关描述（声音、语速、语气等），直接保留
    audio_keywords = ["声音", "语速", "语气", "小声", "大声", "轻声", "低声",
                      "高声", "喊", "叹", "吸气", "呼气", "深呼吸", "咳",
                      "哭", "笑", "叹气", "沉默", "停顿", "碎碎念", "嘟囔"]
    if any(kw in content for kw in audio_keywords):
        return f"({content})"

    # 尝试用映射表转换视觉动作
    for pattern, replacement in _VISUAL_TO_AUDIO:
        if pattern.search(content):
            return f"({replacement})"

    # 包含情绪词的（紧张地、害羞地等），直接保留
    emotion_keywords = ["地", "着", "紧张", "害羞", "开心", "悲伤", "生气",
                        "温柔", "冷淡", "激动", "慌张", "得意", "委屈", "撒娇"]
    if any(kw in content for kw in emotion_keywords):
        return f"({content})"

    # 无法转换为音频的纯视觉动作，删除
    logger.debug(f"[MimoTTS] 移除纯视觉描述: ({content})")
    return ""


def _fallback_clean(text: str) -> str:
    """无 LLM 时的规则引擎清理：处理角色扮演标签 + 清理 Markdown"""
    # 1. 处理括号动作描述：(xxx) 和 （xxx）→ TTS 音频标签
    text = re.sub(r"[（(]([^)）]+)[)）]", _convert_action_bracket, text)

    # 2. 处理 *动作描述* 格式（部分角色扮演用单个星号包裹动作）
    def _handle_star_action(m: re.Match) -> str:
        content = m.group(1)
        if _is_action_desc(content):
            # 构造一个伪匹配对象给 _convert_action_bracket
            class FakeMatch:
                def group(self, n):
                    return content if n == 1 else m.group(0)
            return _convert_action_bracket(FakeMatch())
        return content  # 非动作描述，去掉星号保留文字

    text = re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", _handle_star_action, text)

    # 3. 去除 Markdown 格式
    text = re.sub(r"```[\s\S]*?```", "", text)
    text = re.sub(r"`([^`]*)`", r"\1", text)
    text = re.sub(r"\*{2,3}(.*?)\*{2,3}", r"\1", text)
    text = re.sub(r"_{1,3}(.*?)_{1,3}", r"\1", text)
    text = re.sub(r"^#{1,6}\s*", "", text, flags=re.MULTILINE)
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"!\[([^\]]*)\]\([^)]*\)", "", text)
    text = re.sub(r"^[\s]*[-*+]\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"^[\s]*\d+\.\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _is_action_desc(text: str) -> bool:
    """判断星号内的文本是否为动作描述（而非强调文本）"""
    action_hints = ["地", "着", "了", "声", "语", "脸", "眼", "头", "手",
                    "身", "步", "起", "下", "过", "住"]
    return len(text) > 2 and any(h in text for h in action_hints)
