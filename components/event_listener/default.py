from __future__ import annotations

import base64
import logging
import re

from langbot_plugin.api.definition.components.common.event_listener import EventListener
from langbot_plugin.api.entities import events, context
from langbot_plugin.api.entities.builtin.platform import message as platform_message
from langbot_plugin.api.entities.builtin.provider import message as provider_message
from components.tts_utils import call_mimo_tts

logger = logging.getLogger(__name__)

# LLM 提示词
TTS_PROCESS_PROMPT = """你负责把AI回复转成自然口语化的TTS文本。只输出要朗读的内容。

格式：
1. 开头<style>风格</style>
2. 文中用英文括号(语气描述)

核心要求：
- 绝对不要输出用户的问题，只处理AI回复
- 转成口语化表达：去掉书面语、列表符号，用"嗯""啊""呢""嘛""吧"等语气词让文本更像说话
- 长句拆短句，加自然停顿
- 括号中声音描述保留，视觉动作转语气，无声动作删除
- 清除Markdown、emoji、代码块
- 超300字精简

唱歌特殊规则：
- 唱歌部分style只能是<style>唱歌</style>，不加其他风格
- 唱歌部分只输出纯歌词，不加括号标签，不口语化
- 歌词必须写在一行内，不要换行，紧跟在<style>唱歌</style>后面
- 既有对话又有唱歌时用---分隔，每段独立style

口语化示例：
书面："量子纠缠是量子力学中的一种现象，两个粒子会相互关联。"
口语："量子纠缠嘛，(稍作停顿)简单来说呢，就是两个粒子会互相关联，挺神奇的。"

书面："以下是三个建议：1.多喝水 2.早睡 3.运动"
口语："我给你三个小建议吧。首先呢，多喝水，然后呢，尽量早点睡，(语速放慢)还有就是多运动运动。"

多段示例：
<style>开心</style>(兴奋地)好呀好呀！那我唱一首给你听吧！
---
<style>唱歌</style>春天在哪里呀，春天在哪里，春天在那小朋友的眼睛里

只输出结果，不解释。"""


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

    api_key = config.get("api_key") or ""
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

    # 统一注入语言口音/方言风格（唱歌段不注入）
    if default_style:
        tts_text = _inject_style_smart(tts_text, default_style)

    if not tts_text.strip():
        return

    # 按 --- 分段，每段独立合成
    segments = [s.strip() for s in tts_text.split("---") if s.strip()]

    for i, seg in enumerate(segments):
        logger.info(f"[MimoTTS] ===== TTS 段 {i+1}/{len(segments)} =====")
        logger.info(f"[MimoTTS] {seg}")
        logger.info(f"[MimoTTS] ===========================")

        audio_data = await call_mimo_tts(api_key, seg, voice)
        if audio_data is None:
            logger.error(f"[MimoTTS] 第 {i+1} 段语音合成失败")
            continue

        data_url = f"data:audio/wav;base64,{base64.b64encode(audio_data).decode()}"
        await event_context.reply(
            platform_message.MessageChain([
                platform_message.Voice(url=data_url),
            ])
        )
        logger.info(f"[MimoTTS] 第 {i+1} 段发送成功，音频大小: {len(audio_data)} 字节")


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


def _inject_style_smart(text: str, extra_style: str) -> str:
    """对多段文本智能注入口音风格，唱歌段跳过"""
    segments = text.split("---")
    result = []
    for seg in segments:
        seg = seg.strip()
        if not seg:
            continue
        # 唱歌段不注入额外风格
        match = re.match(r"^<style>(.*?)</style>", seg)
        if match and "唱歌" in match.group(1):
            result.append(seg)
        else:
            result.append(_inject_style(seg, extra_style))
    return "\n---\n".join(result)


async def _process_text_with_llm(
    plugin, llm_model_uuid: str, user_text: str, response_text: str
) -> str:
    """使用 LangBot invoke_llm 加工文本"""
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
