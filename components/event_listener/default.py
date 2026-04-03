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

# LLM 提示词
TTS_PROCESS_PROMPT = """你是一个 TTS 文本优化助手，负责将文本转换为 MiMo TTS 能生动朗读的格式。

## MiMo TTS 格式说明

1. 整体风格：文本最开头加 <style>标签</style>
   支持多风格组合，空格分隔：<style>温柔 害羞</style>
   常用风格：温柔/开心/悲伤/生气/严肃/俏皮/害羞/冷酷/热情/慵懒/撒娇/东北话/粤语/台湾腔

2. 细粒度音频标签：文中用英文半角括号 (描述) 控制局部语气
   示例：(小声)、(激动地)、(深呼吸)、(语速加快)、(哽咽)、(笑着说)、(叹气)

## 转换规则

### 角色扮演/场景对话（文中含括号动作描述）
- 声音相关描述 → 直接保留为 (描述) 格式
  (声音颤抖) → (声音颤抖)
  (小声嘟囔) → (小声嘟囔)
- 视觉动作 → 转为可听的情绪/语气描述
  (脸颊泛红地低着头) → (害羞地，小声)
  (猛地站起来指着对方) → (激动地，提高音量)
  (眼眶微红咬着嘴唇) → (哽咽，颤抖着)
  (双手环胸冷笑) → (冷淡地)
  (蹦蹦跳跳地跑过来) → (开心地，语速加快)
- 纯视觉无声动作 → 删除
  (转过身去)、(打开窗户) → 删除

### 普通对话
- 根据语义情感分析合适的整体 <style> 标签
- 按语境在关键句前插入 (描述) 标签增强表现力

### 通用
- 清除所有 Markdown 格式（加粗、斜体、标题、列表、代码块、链接、图片等）
- 数字和缩写转口语（如 3.14 → 三点一四，API → A P I）
- 超过 300 字适当精简，保留核心内容和情绪节奏
- 删除 emoji 表情符号

## 输出格式
严格只输出转换结果，不要解释、不要换行前缀：
<style>风格</style>正文内容

## 示例

输入：
用户：今天心情怎么样？
回复：(歪着头想了想，然后露出灿烂的笑容)嘿嘿～今天超开心的！(突然凑近小声说)因为…有人一直在陪我聊天嘛…(脸红地别过头)才、才不是因为你啦！

输出：
<style>开心 俏皮</style>(歪着头想了想，开心地)嘿嘿～今天超开心的！(突然凑近，小声说)因为…有人一直在陪我聊天嘛…(害羞地，小声)才、才不是因为你啦！

输入：
用户：帮我解释一下什么是量子纠缠
回复：量子纠缠是量子力学中一种**非常神奇**的现象。简单来说，两个粒子一旦发生"纠缠"，无论相隔多远，对其中一个粒子的测量会*瞬间*影响另一个粒子的状态。\n\n爱因斯坦称之为"幽灵般的超距作用"。

输出：
<style>严肃</style>量子纠缠是量子力学中一种非常神奇的现象。简单来说，两个粒子一旦发生纠缠，无论相隔多远，对其中一个粒子的测量会瞬间影响另一个粒子的状态。(稍作停顿)爱因斯坦称之为，幽灵般的超距作用。"""


class DefaultEventListener(EventListener):

    async def initialize(self):
        await super().initialize()

        @self.handler(events.PersonNormalMessageReceived)
        async def on_person_normal(event_context: context.EventContext):
            """保存用户原始消息"""
            await event_context.set_query_var(
                "mimo_tts_user_text", event_context.event.text_message
            )

        @self.handler(events.GroupNormalMessageReceived)
        async def on_group_normal(event_context: context.EventContext):
            """保存用户原始消息"""
            await event_context.set_query_var(
                "mimo_tts_user_text", event_context.event.text_message
            )

        @self.handler(events.NormalMessageResponded)
        async def on_responded(event_context: context.EventContext):
            """LLM 回复后，加工文本并合成语音"""
            response_text = event_context.event.response_text
            if not response_text or not response_text.strip():
                return
            await _do_tts(self, event_context, response_text)


async def _do_tts(
    listener: DefaultEventListener,
    event_context: context.EventContext,
    response_text: str,
):
    """核心 TTS 流程：配置检查 → 文本加工 → 合成 → 发送"""
    config = listener.plugin.get_config()

    enable_auto_tts = config.get("enable_auto_tts")
    if enable_auto_tts is None:
        enable_auto_tts = True
    if not enable_auto_tts:
        return

    # 拦截文字回复
    block_text = config.get("block_text_reply")
    if block_text is None:
        block_text = True
    if block_text:
        event_context.prevent_default()

    api_key = await get_api_key(listener.plugin)
    if not api_key:
        logger.warning("[MimoTTS] API Key 未配置，跳过语音合成")
        return

    # ★ 清理思维链内容（<think>、<thinking>、<reasoning> 等）
    response_text = _strip_thinking(response_text)
    if not response_text.strip():
        logger.info("[MimoTTS] 清理思维链后文本为空，跳过")
        return

    voice = config.get("default_voice") or "mimo_default"
    llm_model_uuid = config.get("llm_model") or ""

    # 获取用户原始消息
    user_text = ""
    try:
        user_text = await event_context.get_query_var("mimo_tts_user_text") or ""
    except BaseException:
        pass

    # 文本加工
    default_style = config.get("default_style") or ""
    if llm_model_uuid:
        tts_text = await _process_text_with_llm(
            listener.plugin, llm_model_uuid, user_text, response_text
        )
        # LLM 加工模型自身也可能输出思维链，再清理一次
        tts_text = _strip_thinking(tts_text)
    else:
        tts_text = _fallback_clean(response_text)

    # 统一注入语言口音/方言风格
    if default_style:
        tts_text = _inject_style(tts_text, default_style)

    if not tts_text.strip():
        return

    # ★ 完整日志输出最终推入 TTS 的文本
    logger.info(f"[MimoTTS] ===== 最终 TTS 文本 =====")
    logger.info(f"[MimoTTS] {tts_text}")
    logger.info(f"[MimoTTS] ===========================")

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
    logger.info(f"[MimoTTS] 语音发送成功，音频大小: {len(audio_data)} 字节")


def _strip_thinking(text: str) -> str:
    """清除思维链/推理过程标签及其内容"""
    # <think>...</think>、<thinking>...</thinking>、<reasoning>...</reasoning> 等
    text = re.sub(
        r"<(?:think|thinking|reasoning|thought|reflection)>[\s\S]*?"
        r"</(?:think|thinking|reasoning|thought|reflection)>",
        "", text, flags=re.IGNORECASE
    )
    # 有些模型只有开头 <think> 没有闭合，清理到第一个非思维内容
    text = re.sub(
        r"^[\s]*<(?:think|thinking|reasoning|thought|reflection)>[\s\S]*$",
        "", text, flags=re.IGNORECASE | re.MULTILINE
    )
    return text.strip()


def _inject_style(text: str, extra_style: str) -> str:
    """将口音/方言风格追加到已有的 <style> 标签中，如果没有则新建"""
    match = re.match(r"^<style>(.*?)</style>", text)
    if match:
        existing = match.group(1).strip()
        # 避免重复追加
        if extra_style not in existing:
            combined = f"{existing} {extra_style}"
        else:
            combined = existing
        return f"<style>{combined}</style>{text[match.end():]}"
    else:
        return f"<style>{extra_style}</style>{text}"


async def _process_text_with_llm(
    plugin: Any, llm_model_uuid: str, user_text: str, response_text: str
) -> str:
    """使用 LLM 加工文本"""
    try:
        prompt_content = f"用户：{user_text}\n回复：{response_text}"

        llm_response = await plugin.invoke_llm(
            llm_model_uuid=llm_model_uuid,
            messages=[
                provider_message.Message(role="system", content=TTS_PROCESS_PROMPT),
                provider_message.Message(role="user", content=prompt_content),
            ],
        )

        result = llm_response.content if hasattr(llm_response, "content") else str(llm_response)
        if result and result.strip():
            logger.info(f"[MimoTTS] LLM 加工完成，长度: {len(result)}")
            return result.strip()

    except BaseException as e:
        logger.warning(f"[MimoTTS] LLM 加工失败（{type(e).__name__}），使用规则引擎回退: {e}")

    return _fallback_clean(response_text)


# ─── 视觉动作 → 音频情绪 映射表 ───
_VISUAL_TO_AUDIO = [
    (re.compile(r"脸[颊红]|脸颊泛红|面红|红着脸|满脸通红"), "害羞地，小声"),
    (re.compile(r"低[着下]头|垂[下着]眼"), "低声"),
    (re.compile(r"捂[住着]?脸|遮[住着]?脸"), "害羞，闷闷地"),
    (re.compile(r"眼[眶圈][微泛]?红|泪[水光]|流泪|哭[泣了]"), "哽咽"),
    (re.compile(r"抹[去掉]?[了]?眼泪|擦[掉去]?泪"), "抽泣着"),
    (re.compile(r"猛[地的]?站|一拍桌|握紧拳"), "激动地"),
    (re.compile(r"跳[起了]来|蹦[起了]"), "兴奋地"),
    (re.compile(r"紧[紧握]?[握攥]|咬[着紧]?[着了]?[唇嘴]|吞[了口]"), "紧张地"),
    (re.compile(r"颤抖|发抖|哆嗦"), "颤抖着声音"),
    (re.compile(r"轻轻[地的]?[笑叹]|微微[笑叹]"), "轻声"),
    (re.compile(r"沉默|无言|不语|安静"), "沉默片刻"),
    (re.compile(r"笑[着了]|咧嘴|嘴角上扬|露出笑|灿烂"), "开心地"),
    (re.compile(r"眼睛[亮发]光|双眼放光|星星眼"), "兴奋地"),
    (re.compile(r"凑[近过]|靠[近过]"), "小声"),
    (re.compile(r"双手环胸|抱[着了]胳膊|冷笑"), "冷淡地"),
    (re.compile(r"蹦蹦跳跳|欢快|雀跃"), "开心地，语速加快"),
    (re.compile(r"别过[头脸]|扭[过开]头"), "害羞地"),
]


def _convert_action_bracket(match: re.Match) -> str:
    """将括号内的描述转换为 TTS 可用的音频标签"""
    content = match.group(1).strip()

    # 音频相关描述，直接保留
    audio_keywords = ["声音", "语速", "语气", "小声", "大声", "轻声", "低声",
                      "高声", "喊", "叹", "吸气", "呼气", "深呼吸", "咳",
                      "哭", "笑", "叹气", "沉默", "停顿", "碎碎念", "嘟囔",
                      "颤抖", "哽咽", "抽泣", "呜咽"]
    if any(kw in content for kw in audio_keywords):
        return f"({content})"

    # 视觉动作 → 音频情绪
    for pattern, replacement in _VISUAL_TO_AUDIO:
        if pattern.search(content):
            return f"({replacement})"

    # 情绪词，保留
    emotion_keywords = ["地", "着", "紧张", "害羞", "开心", "悲伤", "生气",
                        "温柔", "冷淡", "激动", "慌张", "得意", "委屈", "撒娇",
                        "兴奋", "焦急", "无奈", "感动", "惊讶", "困倦"]
    if any(kw in content for kw in emotion_keywords):
        return f"({content})"

    # 无法转换，删除
    logger.debug(f"[MimoTTS] 移除纯视觉描述: ({content})")
    return ""


def _fallback_clean(text: str) -> str:
    """无 LLM 时的规则引擎清理"""
    # 1. 括号动作描述 → TTS 音频标签
    text = re.sub(r"[（(]([^)）]+)[)）]", _convert_action_bracket, text)

    # 2. *动作描述* 格式
    def _handle_star_action(m: re.Match) -> str:
        content = m.group(1)
        if _is_action_desc(content):
            class FakeMatch:
                def group(self, n):
                    return content if n == 1 else m.group(0)
            return _convert_action_bracket(FakeMatch())
        return content

    text = re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", _handle_star_action, text)

    # 3. 清除 Markdown
    text = re.sub(r"```[\s\S]*?```", "", text)
    text = re.sub(r"`([^`]*)`", r"\1", text)
    text = re.sub(r"\*{2,3}(.*?)\*{2,3}", r"\1", text)
    text = re.sub(r"_{1,3}(.*?)_{1,3}", r"\1", text)
    text = re.sub(r"^#{1,6}\s*", "", text, flags=re.MULTILINE)
    text = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"!\[([^\]]*)\]\([^)]*\)", "", text)
    text = re.sub(r"^[\s]*[-*+]\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"^[\s]*\d+\.\s+", "", text, flags=re.MULTILINE)

    # 4. 清除 emoji
    text = re.sub(
        r"[\U0001F600-\U0001F64F\U0001F300-\U0001F5FF\U0001F680-\U0001F6FF"
        r"\U0001F1E0-\U0001F1FF\U00002702-\U000027B0\U0001F900-\U0001F9FF"
        r"\U0001FA00-\U0001FA6F\U0001FA70-\U0001FAFF\U00002600-\U000026FF]+",
        "", text
    )

    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _is_action_desc(text: str) -> bool:
    """判断星号内的文本是否为动作描述"""
    action_hints = ["地", "着", "了", "声", "语", "脸", "眼", "头", "手",
                    "身", "步", "起", "下", "过", "住"]
    return len(text) > 2 and any(h in text for h in action_hints)
