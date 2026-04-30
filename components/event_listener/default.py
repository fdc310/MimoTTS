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
1. 开头用括号标注整体风格，如 (开心)、(温柔)、(东北话)
2. 文中用括号添加细粒度控制，如 (叹气)、(笑声)、(语速加快)

核心要求：
- 绝对不要输出用户的问题，只处理AI回复
- 转成口语化表达：去掉书面语、列表符号，用"嗯""啊""呢""嘛""吧"等语气词让文本更像说话
- 长句拆短句，加自然停顿
- 清除Markdown、emoji、代码块
- 超300字精简

【重要】禁止使用纯视觉动作描述！以下内容必须删除或转换：
- 删除：(跺脚)、(拍桌)、(握拳)、(皱眉)、(瞪眼)、(扭头)、(转身)、(走开)、(靠近)、(后退)、(点头)、(摇头)等纯肢体动作
- 删除：(眉头拧成疙瘩)、(狠狠跺脚)、(双手叉腰)、(双手环胸)等外貌描写
- 转换：视觉动作→音频情绪，如：(脸红)→(害羞地，小声)、(低头)→(低声)、(哭泣)→(哽咽)

整体风格标签（放文本开头，用括号）：
- 基础情绪：开心、悲伤、愤怒、恐惧、惊讶、兴奋、委屈、平静、冷漠
- 复合情绪：怅然、欣慰、无奈、愧疚、释然、嫉妒、厌倦、忐忑、动情
- 整体语调：温柔、高冷、活泼、严肃、慵懒、俏皮、深沉、干练、凌厉
- 音色定位：磁性、醇厚、清亮、空灵、稚嫩、苍老、甜美、沙哑、醇雅
- 人设腔调：夹子音、御姐音、正太音、大叔音、台湾腔
- 方言：东北话、四川话、河南话、粤语
- 唱歌：唱歌

音频细粒度标签（放文中，用括号，支持 ()、（）、[] 三种括号格式）：
- 语速与节奏：(吸气)、(深呼吸)、(叹气)、(长叹一口气)、(喘息)、(屏息)、(语速加快)、(语速放慢)、(停顿)、(沉默片刻)
- 情绪状态：(紧张)、(害怕)、(激动)、(疲惫)、(撒娇)、(心虚)、(震惊)、(不耐烦)
- 语音特征：(颤抖)、(声音颤抖)、(变调)、(破音)、(鼻音)、(气声)、(沙哑)
- 哭笑表达：(轻笑)、(大笑)、(冷笑)、(抽泣)、(呜咽)、(哽咽)、(嚎啕大哭)
- 音量控制：(小声)、(大声)、(轻声)、(低声)

复合情绪支持（自然语言描述即可）：
- "压抑的愤怒"、"带着哽咽的笑意"、"温柔但疲惫"、"狂躁中的温柔"

唱歌特殊规则：
- 唱歌部分风格标签只能是 (唱歌)，不加其他风格
- 唱歌部分只输出纯歌词，不加括号标签，不口语化
- 歌词必须写在一行内，不要换行，紧跟在(唱歌)后面
- 既有对话又有唱歌时用---分隔，每段独立风格

口语化示例：
书面："量子纠缠是量子力学中的一种现象，两个粒子会相互关联。"
口语："(平静)量子纠缠嘛，(停顿)简单来说呢，就是两个粒子会互相关联，挺神奇的。"

书面："以下是三个建议：1.多喝水 2.早睡 3.运动"
口语："(温柔)我给你三个小建议吧。首先呢，多喝水，然后呢，尽量早点睡，(语速放慢)还有就是多运动运动。"

音频标签示例：
(紧张)(深呼吸)呼……冷静，冷静。(语速加快)自我介绍已经背了五十遍了，应该没问题的。加油，你可以的……(小声)哎呀，领带歪没歪？
(极其疲惫)(叹气)师傅……到地方了叫我一声……(长叹一口气)我先眯一会儿，这班加得我魂儿都要散了。
(怅然)如果我当时……(沉默片刻)哪怕再坚持一秒钟，结果是不是就不一样了？(苦笑)呵，没如果了。

错误示范（禁止）：
(愤怒)(四川话)(狠狠跺脚)(眉头拧成疙瘩)老子数到三！
正确示范：
(愤怒)(四川话)老子数到三！

多段示例：
(开心)(兴奋地)好呀好呀！那我唱一首给你听吧！
---
(唱歌)春天在哪里呀，春天在哪里，春天在那小朋友的眼睛里

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

    # 清理思维链内容
    response_text = _strip_thinking(response_text)
    if not response_text.strip():
        logger.info("[MimoTTS] 清理思维链后文本为空，跳过")
        return

    voice = config.get("default_voice") or "mimo_default"
    model = config.get("default_model") or "mimo-v2.5-tts"
    audio_format = config.get("default_format") or "wav"
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

    # 统一注入默认风格（唱歌段不注入）
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

        # 解析风格标签（可能有多个，如 (开心)(兴奋)）
        style = _extract_style(seg)
        # 移除所有风格标签，保留纯净文本
        seg = _remove_style_tags(seg)

        audio_data = await call_mimo_tts(
            api_key, seg, voice,
            model=model,
            audio_format=audio_format,
            style=style if style else None,
        )
        if audio_data is None:
            logger.error(f"[MimoTTS] 第 {i+1} 段语音合成失败")
            continue

        audio_b64_str = base64.b64encode(audio_data).decode()
        await event_context.reply(
            platform_message.MessageChain([
                platform_message.Voice(base64=audio_b64_str),
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


def _extract_style(text: str) -> str | None:
    """从文本开头提取所有连续的风格标签，如 (开心)(兴奋) → '开心 兴奋'"""
    styles = []
    remaining = text
    while True:
        match = re.match(r"^\(([^)]+)\)", remaining)
        if match:
            styles.append(match.group(1))
            remaining = remaining[match.end():].strip()
        else:
            break
    return " ".join(styles) if styles else None


def _remove_style_tags(text: str) -> str:
    """移除文本开头所有连续的风格标签，返回纯净文本"""
    while re.match(r"^\(([^)]+)\)", text):
        text = re.sub(r"^\(([^)]+)\)", "", text).strip()
    return text


def _inject_style_smart(text: str, extra_style: str) -> str:
    """对多段文本智能注入口音风格，唱歌段跳过"""
    segments = text.split("---")
    result = []
    for seg in segments:
        seg = seg.strip()
        if not seg:
            continue
        # 唱歌段不注入额外风格
        match = re.match(r"^\(([^)]+)\)", seg)
        if match and "唱歌" in match.group(1):
            # 移除所有括号内容，只保留歌词
            lyrics = re.sub(r"\([^)]*\)", "", seg).strip()
            result.append(f"(唱歌){lyrics}")
        else:
            # 提取所有风格标签
            styles = []
            while True:
                m = re.match(r"^\(([^)]+)\)", seg)
                if m:
                    style_content = m.group(1)
                    # 检查是否是音频相关风格（不是视觉动作）
                    if _is_audio_style(style_content):
                        styles.append(style_content)
                    seg = seg[m.end():].strip()
                else:
                    break

            # 合并风格
            if styles:
                all_styles = " ".join(styles)
                if extra_style and extra_style not in all_styles:
                    all_styles = f"{extra_style} {all_styles}"
                result.append(f"({all_styles}){seg}")
            else:
                result.append(f"({extra_style}){seg}")
    return "\n---\n".join(result)


def _is_audio_style(content: str) -> bool:
    """判断括号内容是否是音频相关风格（而不是视觉动作）"""
    # 音频相关风格关键词（覆盖 API 文档所有支持的标签）
    audio_style_keywords = [
        # 基础情绪
        "开心", "悲伤", "愤怒", "恐惧", "惊讶", "兴奋", "委屈", "平静", "冷漠",
        # 复合情绪
        "怅然", "欣慰", "无奈", "愧疚", "释然", "嫉妒", "厌倦", "忐忑", "动情",
        # 整体语调
        "温柔", "高冷", "活泼", "严肃", "慵懒", "俏皮", "深沉", "干练", "凌厉",
        # 音色定位
        "磁性", "醇厚", "清亮", "空灵", "稚嫩", "苍老", "甜美", "沙哑", "醇雅",
        # 人设腔调
        "夹子音", "御姐音", "正太音", "大叔音", "台湾腔",
        # 方言
        "东北话", "四川话", "河南话", "粤语",
        # 唱歌
        "唱歌", "sing", "singing",
        # 语速与节奏（音频标签）
        "吸气", "深呼吸", "叹气", "长叹一口气", "喘息", "屏息",
        "语速加快", "语速放慢", "语速", "停顿", "沉默",
        # 情绪状态（音频标签）
        "紧张", "害怕", "激动", "疲惫", "委屈", "撒娇", "心虚", "震惊", "不耐烦",
        # 语音特征（音频标签）
        "颤抖", "声音颤抖", "变调", "破音", "鼻音", "气声", "沙哑",
        # 哭笑表达（音频标签）
        "笑", "轻笑", "大笑", "冷笑", "抽泣", "呜咽", "哽咽", "嚎啕大哭",
        # 音量控制
        "小声", "大声", "轻声", "低声", "高声", "喊",
        # 其他
        "碎碎念", "嘟囔", "哭", "哭腔", "笑声", "哭声",
    ]

    # 检查是否包含音频风格关键词
    for kw in audio_style_keywords:
        if kw in content:
            return True

    # 视觉动作关键词（应该被删除）
    visual_keywords = [
        "跺脚", "拍桌", "握拳", "皱眉", "瞪眼", "扭头", "转身", "走开",
        "靠近", "后退", "点头", "摇头", "叉腰", "环胸", "抱臂", "挥手",
        "脸红", "脸白", "脸青", "眉头", "嘴角", "狠狠", "使劲", "猛地",
    ]
    for kw in visual_keywords:
        if kw in content:
            return False

    # 默认不保留（宁可误删也不要保留无效内容）
    return False


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

    # 音频相关描述，直接保留（覆盖 API 文档所有支持的音频标签）
    audio_keywords = [
        # 语速与节奏
        "语速", "停顿", "沉默", "吸气", "呼气", "深呼吸", "叹气", "喘息", "屏息",
        # 情绪状态
        "紧张", "害怕", "激动", "疲惫", "撒娇", "心虚", "震惊", "不耐烦",
        # 语音特征
        "声音", "语气", "颤抖", "变调", "破音", "鼻音", "气声", "沙哑",
        # 哭笑表达
        "哭", "笑", "轻笑", "大笑", "冷笑", "抽泣", "呜咽", "哽咽", "嚎啕大哭",
        # 音量控制
        "小声", "大声", "轻声", "低声", "高声", "喊",
        # 其他
        "咳", "碎碎念", "嘟囔", "哭腔", "笑声", "哭声",
    ]
    if any(kw in content for kw in audio_keywords):
        return f"({content})"

    # 视觉动作 → 音频情绪
    for pattern, replacement in _VISUAL_TO_AUDIO:
        if pattern.search(content):
            return f"({replacement})"

    # 情绪词，保留
    emotion_keywords = ["地", "着", "紧张", "害羞", "开心", "悲伤", "生气",
                        "温柔", "冷淡", "激动", "慌张", "得意", "委屈", "撒娇",
                        "兴奋", "焦急", "无奈", "感动", "惊讶", "困倦",
                        "愤怒", "害怕", "恐惧", "惊喜", "兴奋", "沮丧", "失望"]
    if any(kw in content for kw in emotion_keywords):
        return f"({content})"

    # 纯视觉动作/外貌描写，删除
    # 匹配常见的视觉动作关键词
    visual_keywords = [
        "跺脚", "拍桌", "握拳", "皱眉", "瞪眼", "扭头", "转身", "走开",
        "靠近", "后退", "点头", "摇头", "叉腰", "环胸", "抱臂", "挥手",
        "踢", "打", "推", "拉", "抓", "扔", "接", "举", "抬", "放",
        "站", "坐", "躺", "蹲", "跳", "跑", "走", "爬", "滚",
        "脸红", "脸白", "脸青", "脸黑", "脸沉", "脸变", "脸色",
        "眉头", "嘴角", "眼睛", "眼神", "目光", "瞪", "瞟", "瞄", "瞥",
        "狠狠", "使劲", "用力", "猛地", "突然", "猛地", "一把",
        "拧", "掐", "捏", "揉", "搓", "擦", "抹", "抠", "撕",
    ]
    if any(kw in content for kw in visual_keywords):
        logger.debug(f"[MimoTTS] 移除纯视觉描述: ({content})")
        return ""

    # 如果没有匹配到任何规则，也删除（宁可误删也不要保留无效内容）
    logger.debug(f"[MimoTTS] 移除未识别内容: ({content})")
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
