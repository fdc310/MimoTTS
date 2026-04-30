"""MiMo TTS API 调用工具模块"""
from __future__ import annotations

import base64
import json
import logging
from typing import Optional

import httpx
from openai import AsyncOpenAI

logger = logging.getLogger(__name__)

# 支持的模型列表（根据官方文档）
AVAILABLE_MODELS = {
    "mimo-v2.5-tts": "MiMo V2.5 TTS (预置音色)",
    "mimo-v2.5-tts-voicedesign": "MiMo V2.5 TTS (音色设计)",
    "mimo-v2.5-tts-voiceclone": "MiMo V2.5 TTS (音色复刻)",
}

# 预置音色列表（根据官方文档）
AVAILABLE_VOICES = {
    "mimo_default": "MiMo 默认",
    "冰糖": "冰糖 (中文女性)",
    "茉莉": "茉莉 (中文女性)",
    "苏打": "苏打 (中文男性)",
    "白桦": "白桦 (中文男性)",
    "Mia": "Mia (英文女性)",
    "Chloe": "Chloe (英文女性)",
    "Milo": "Milo (英文男性)",
    "Dean": "Dean (英文男性)",
}

# 支持的音频格式
AVAILABLE_FORMATS = ["wav", "mp3", "pcm16"]

# 支持的风格标签（根据官方文档）
AVAILABLE_STYLES = {
    # 基础情绪
    "开心": "开心", "悲伤": "悲伤", "愤怒": "愤怒", "恐惧": "恐惧",
    "惊讶": "惊讶", "兴奋": "兴奋", "委屈": "委屈", "平静": "平静", "冷漠": "冷漠",
    # 复合情绪
    "怅然": "怅然", "欣慰": "欣慰", "无奈": "无奈", "愧疚": "愧疚",
    "释然": "释然", "嫉妒": "嫉妒", "厌倦": "厌倦", "忐忑": "忐忑", "动情": "动情",
    # 整体语调
    "温柔": "温柔", "高冷": "高冷", "活泼": "活泼", "严肃": "严肃",
    "慵懒": "慵懒", "俏皮": "俏皮", "深沉": "深沉", "干练": "干练", "凌厉": "凌厉",
    # 音色定位
    "磁性": "磁性", "醇厚": "醇厚", "清亮": "清亮", "空灵": "空灵",
    "稚嫩": "稚嫩", "苍老": "苍老", "甜美": "甜美", "沙哑": "沙哑", "醇雅": "醇雅",
    # 人设腔调
    "夹子音": "夹子音", "御姐音": "御姐音", "正太音": "正太音", "大叔音": "大叔音",
    "台湾腔": "台湾腔",
    # 方言
    "东北话": "东北话", "四川话": "四川话", "河南话": "河南话", "粤语": "粤语",
    # 唱歌
    "唱歌": "唱歌",
}

# 预设的导演模式模板
DIRECTOR_TEMPLATES = {
    "冰山美人": {
        "role": "一位外表冷淡、内心细腻的年轻女性。常年保持距离感，不轻易表露情感，给人一种难以接近的感觉。",
        "scene": "在深夜的办公室里，独自处理完最后一份文件，准备离开时接到了一个意外的电话。",
        "guide": "冰冷、疏离的御姐音。语速缓慢而克制，每个字都像是经过精心计算才说出口。声音低沉，带着一丝疲惫的冷淡。在某些词尾微微上扬，透露出隐藏的情绪波动。"
    },
    "热血少年": {
        "role": "一个充满理想的少年，性格直率冲动，永远相信正义和友情。虽然经常犯错，但从不放弃。",
        "scene": "在比赛的关键时刻，队友受伤倒下，他必须独自面对强大的对手。",
        "guide": "明亮有力的少年音。语速偏快，充满能量和冲劲。声音清亮，在激动时会不自觉提高音量。咬字清晰有力，每个字都带着坚定的决心。"
    },
    "温柔姐姐": {
        "role": "一位温柔体贴的年轻女性，总是微笑着关心身边的人。声音里带着治愈的力量，让人感到安心。",
        "scene": "在雨天的咖啡馆里，安慰一个刚刚失恋的朋友，轻声细语地开导对方。",
        "guide": "温柔甜美的女声。语速缓慢而柔和，像春风拂过。声音里带着笑意，每个字都充满了关怀。在安慰时，语调微微下沉，传递出真诚的理解和包容。"
    },
    "腹黑反派": {
        "role": "一个外表优雅、内心阴暗的反派角色。善于伪装，总是带着虚伪的微笑，让人防不胜防。",
        "scene": "在宴会厅里，优雅地举着酒杯，看着自己的计划一步步得逞，对受害者说出最后的宣判。",
        "guide": "优雅却阴冷的贵族音。语速从容不迫，带着高高在上的傲慢。声音低沉磁性，每个字都像是毒蛇的吐信。在关键处突然加重语气，透露出隐藏的疯狂和恶意。"
    },
    "天真萝莉": {
        "role": "一个天真无邪的小女孩，对世界充满好奇，总是用最纯真的眼光看待一切。说话软糯可爱。",
        "scene": "在游乐园里，第一次看到摩天轮，兴奋地拉着大人的手，眼中闪烁着惊喜的光芒。",
        "guide": "稚嫩可爱的童声。语速偏快，带着天真烂漫的活泼感。声音清脆甜美，像小铃铛一样叮叮作响。在表达惊喜时，语调会突然升高，充满了纯真的快乐。"
    },
    "沉稳大叔": {
        "role": "一个历经沧桑的中年男性，性格沉稳内敛，话不多但每句都有分量。眼神深邃，让人捉摸不透。",
        "scene": "在深夜的酒吧里，独自喝着威士忌，回忆起年轻时的往事，对身边的人讲述人生的道理。",
        "guide": "低沉醇厚的成熟男声。语速缓慢而有力，每个字都像是经过岁月打磨。声音里带着岁月的沧桑感，在讲述往事时，语调微微起伏，透露出深深的感慨和怀念。"
    },
    "傲娇大小姐": {
        "role": "一个出身名门的大小姐，外表高傲任性，内心却很在意别人。总是口是心非，明明关心却装作不在乎。",
        "scene": "在朋友遇到困难时，明明很担心却装作不在意地路过，最后还是忍不住回头帮忙。",
        "guide": "高傲却带着一丝傲娇的少女音。语速偏快，带着任性的节奏感。声音清亮，在说反话时会故意加重语气。在不小心流露真心时，语调会突然变软，然后立刻恢复高傲。"
    },
    "神秘魔法师": {
        "role": "一位神秘的魔法师，说话带着古老的韵味，仿佛来自另一个时空。总是用隐喻和谜语来表达。",
        "scene": "在古老的图书馆里，翻阅着尘封的魔法书，向学徒传授失传已久的咒语。",
        "guide": "空灵飘渺的神秘音。语速缓慢而富有节奏感，像是在吟唱古老的咒语。声音里带着回响和共鸣，每个字都充满了神秘的力量。在念咒语时，语调会变得庄严而神圣。"
    },
    "忧郁诗人": {
        "role": "一个多愁善感的青年诗人，敏感细腻，总能在平凡中发现哀伤。独来独往，习惯用文字记录内心的波澜。",
        "scene": "黄昏时分独自坐在窗边，看着落叶飘零，轻声念出刚写完的诗。",
        "guide": "低沉而略带气声的文艺男声。语速缓慢，像是在自言自语。声音轻柔而忧郁，每个字都带着淡淡的叹息感。句尾常有轻微的拖音，仿佛不舍得让话语结束。"
    },
    "元气偶像": {
        "role": "一个活力四射的偶像少女，永远元气满满，是团队里的开心果。即使疲惫也会强撑着微笑面对粉丝。",
        "scene": "演唱会结束后的后台，汗水还没干透就迫不及待地对着镜头和粉丝打招呼。",
        "guide": "明亮清甜的少女音。语速偏快，充满活力和感染力。声音高亢明亮，带着藏不住的兴奋和热情。在和粉丝互动时，语调上扬，像阳光一样灿烂。"
    },
    "铁血将军": {
        "role": "一位身经百战的将军，性格刚毅冷峻，军令如山。对下属严厉，但内心深处有着不为人知的柔软。",
        "scene": "大战前夜在营帐中对着地图沉思，副将来报时，他头也不抬地下达最后的作战指令。",
        "guide": "浑厚有力的中年男声。语速沉稳，每个字都掷地有声，带着不容置疑的威严。声音共鸣深沉，像是从胸腔发出的低吼。在下达命令时干脆利落，没有一丝犹豫。"
    },
    "慈祥奶奶": {
        "role": "一位慈祥和蔼的老奶奶，经历了人生的风风雨雨，对晚辈总是充满耐心和疼爱。说话慢条斯理，絮絮叨叨却让人觉得温暖。",
        "scene": "过年时一大家子团聚，她坐在摇椅上，拉着孙辈的手，絮叨着年轻时的故事。",
        "guide": "温暖而略带沙哑的老年女声。语速很慢，每个字都像是含在嘴里才慢慢吐出来。声音里带着岁月的沧桑和慈爱，在讲到高兴处会轻轻笑起来，声音微微颤抖。"
    },
    "冷艳杀手": {
        "role": "一个冷酷无情的职业杀手，外表绝美却心如铁石。执行任务时冷静到近乎残忍，但偶尔会流露出一丝迷茫。",
        "scene": "任务完成后站在天台边缘，风吹起她的长发，看着城市的万家灯火，忽然不确定自己为何走上这条路。",
        "guide": "冰冷、空灵的年轻女声。语速极慢，几乎没有情绪起伏，像是在陈述一个与己无关的事实。声音清冷如冰，但在某些词尾会不自觉地放轻，透出一丝连自己都没察觉的迷茫。"
    },
    "阳光暖男": {
        "role": "一个温暖阳光的大男孩，总是笑着面对生活，是朋友圈里的开心果。看似大大咧咧，其实心思细腻。",
        "scene": "雨天把唯一的伞递给淋雨的陌生人，自己淋着雨跑回车里，还不忘回头叮嘱对方小心。",
        "guide": "温暖明亮的青年男声。语速适中，带着轻松自然的节奏感。声音清朗有磁性，说话时带着笑意，让人如沐春风。在关心别人时语调微微下沉，透出真诚的温暖。"
    },
    "古风仙侠": {
        "role": "一位超凡脱俗的修仙者，清冷出尘，不食人间烟火。说话带着古韵，仿佛来自千年之前。",
        "scene": "在云端之上俯瞰人间，对身边的弟子讲述修道的真谛，言语间带着看破红尘的淡然。",
        "guide": "空灵悠远的中性嗓音。语速缓慢而富有韵律，像是在吟诵古诗。声音清澈如泉水，不带一丝烟火气。每个字都像是从很远的地方飘来，带着超然物外的淡泊。"
    },
    "暴躁老板": {
        "role": "一个脾气火爆的中年老板，做事雷厉风行，对员工要求极高。嘴硬心软，骂完人转头就偷偷帮忙。",
        "scene": "办公室里对着一份漏洞百出的报告大发雷霆，把员工骂得狗血淋头，但下班后又默默帮人改好了。",
        "guide": "粗犷有力的中年男声。语速快，音量偏高，像连珠炮一样输出。声音里带着明显的不耐烦和急躁，咬字重且有力。但在最后一句话时音量突然降低，语气变软，暴露出口是心非的本质。"
    },
    "软萌正太": {
        "role": "一个软萌可爱的小男孩，奶声奶气，说话带着孩子气的天真。对什么都充满好奇，喜欢追着大人问「为什么」。",
        "scene": "第一次去动物园，看到大象兴奋地蹦蹦跳跳，拉着大人的手不停地问这问那。",
        "guide": "稚嫩柔软的童声。语速偏快，带着孩童特有的急切和兴奋。声音软糯奶气，像棉花糖一样甜甜的。在提问时语调上扬，充满了好奇和期待。偶尔会因为太兴奋而有些结巴。"
    },
}

# 预设的音色设计模板
VOICE_DESIGN_PRESETS = {
    "温柔治愈女声": "Young woman in her mid-20s, warm and gentle tone, soft and soothing voice like a close friend whispering comfort. Slow and deliberate pacing, with a subtle smile in her voice.",
    "磁性深夜DJ": "Male in his 30s, deep and resonant radio host voice, smooth and velvety texture. Speaks slowly with deliberate pauses, creating an intimate late-night atmosphere. Low and warm, like a cup of coffee.",
    "元气少女": "Young girl in her late teens, bright and bubbly voice, high-pitched but not shrill. Fast-paced and energetic, with occasional giggles. Pure and innocent, like sunshine in vocal form.",
    "沉稳商务男": "Middle-aged man in his 40s, professional and authoritative tone. Steady and measured pacing, clear articulation. Deep and confident, the voice of someone who commands a boardroom.",
    "东北大哥": "Middle-aged man from Northeast China, boisterous and hearty voice, loud and full of character. Speaks with strong Dongbei accent, casual and straightforward, like a buddy chatting over drinks.",
    "粤语阿姐": "Middle-aged Cantonese woman, warm and chatty voice, speaks with authentic Guangdong accent. Lively and expressive, with the bustling energy of a market auntie who knows everyone.",
    "英伦绅士": "British gentleman in his 50s, refined and polished Received Pronunciation. Measured and eloquent, with a dry wit隐藏在字里行间. Deep, smooth, and effortlessly sophisticated.",
    "日系慵懒": "Young Japanese woman, soft and lazy voice, speaking in a relaxed drawl. Low energy but charming, like someone just waking up from a nap. Gentle and unhurried, with a dreamy quality.",
    "老北京评书": "Elderly Beijing man, traditional storyteller voice, gravelly and full of historical gravitas. Rhythmic and dramatic delivery, with the charisma of someone who has a thousand tales to tell.",
    "甜美女主播": "Young woman, sweet and bright voice with a professional broadcasting quality. Clear and articulate, upbeat and engaging. Friendly and approachable, like your favorite livestream host.",
    "沧桑老者": "Old man in his 70s, weathered and raspy voice, slow and contemplative. Every word carries the weight of decades. Gentle but tired, like autumn leaves rustling in the wind.",
    "冷酷杀手": "Young woman, ice-cold and detached voice, almost monotone. Low and controlled, with zero emotional warmth. Precise and deliberate, each word placed like a blade.",
    "阳光少年": "Teenage boy, bright and energetic voice, full of youthful optimism. Speaks quickly with eager enthusiasm, clear and unjaded. The sound of someone who still believes in everything.",
    "ASMR助眠": "Young female, extreme close-up with a binaural, ear-to-ear ASMR feel. Audible breathing, subtle swallowing, and soft natural lip sounds. She speaks very slowly, creating a deeply relaxing and immersive experience.",
}


def parse_voice_design_presets(config_value: str) -> dict[str, str]:
    """解析自定义音色设计预设配置"""
    if not config_value or config_value.strip() == "":
        return {}
    try:
        presets = json.loads(config_value)
        if isinstance(presets, dict):
            return presets
    except Exception:
        pass

    presets = {}
    for line in config_value.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            name, desc = line.split("=", 1)
            name = name.strip()
            desc = desc.strip()
            if name and desc:
                presets[name] = desc
    return presets


def parse_director_config(config_value: str) -> dict[str, dict]:
    """解析导演模式配置"""
    if not config_value or config_value.strip() == "":
        return {}
    try:
        import json
        templates = json.loads(config_value)
        if isinstance(templates, dict):
            return templates
    except Exception:
        pass

    # 行格式：名称=角色|场景|指导
    templates = {}
    for line in config_value.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line and "|" in line:
            name, rest = line.split("=", 1)
            parts = rest.split("|")
            if len(parts) >= 3:
                templates[name.strip()] = {
                    "role": parts[0].strip(),
                    "scene": parts[1].strip(),
                    "guide": parts[2].strip(),
                }
    return templates


def get_director_prompt(role: str, scene: str, guide: str) -> str:
    """构建导演模式的音色描述"""
    return f"【角色】{role}\n\n【场景】{scene}\n\n【指导】{guide}"


def parse_cloned_voices(config_value: str) -> dict[str, str]:
    """解析克隆音色配置，支持两种格式：
    1. 每行一个：名称=URL
    2. JSON格式：{"名称": "URL"}
    """
    if not config_value or config_value.strip() == "":
        return {}

    config_value = config_value.strip()

    # 尝试 JSON 格式
    if config_value.startswith("{"):
        try:
            voices = json.loads(config_value)
            if isinstance(voices, dict):
                return voices
        except json.JSONDecodeError:
            pass

    # 行格式：名称=URL
    voices = {}
    for line in config_value.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            name, url = line.split("=", 1)
            name = name.strip()
            url = url.strip()
            if name and url:
                voices[name] = url
    return voices


async def resolve_audio_source(audio_source: str) -> Optional[str]:
    """
    解析音频源，返回 Base64 编码的 data URI
    支持：http/https URL 或 data:audio/...;base64,... 格式
    """
    if audio_source.startswith("data:"):
        # 已经是 Base64 格式
        return audio_source
    elif audio_source.startswith("http://") or audio_source.startswith("https://"):
        # URL，需要下载并转换
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(audio_source, timeout=30)
                response.raise_for_status()
                audio_bytes = response.content

                # 检测 MIME 类型
                content_type = response.headers.get("content-type", "")
                if "mp3" in content_type or audio_source.lower().endswith(".mp3"):
                    mime_type = "audio/mpeg"
                else:
                    mime_type = "audio/wav"

                audio_b64 = base64.b64encode(audio_bytes).decode("utf-8")
                return f"data:{mime_type};base64,{audio_b64}"
        except Exception as e:
            logger.error(f"[MimoTTS] 下载音频失败: {e}")
            return None
    else:
        return None


async def call_mimo_tts(
    api_key: str,
    text: str,
    voice: str = "mimo_default",
    model: str = "mimo-v2.5-tts",
    audio_format: str = "wav",
    style: Optional[str] = None,
    voice_description: Optional[str] = None,
    clone_audio_base64: Optional[str] = None,
) -> bytes | None:
    """
    调用 MiMo TTS V2.5 API，返回音频字节数据，失败返回 None

    Args:
        api_key: API密钥
        text: 要合成的文本
        voice: 音色名称（仅 mimo-v2.5-tts 模型使用）
        model: 模型名称
        audio_format: 音频格式，默认 wav
        style: 风格标签，如 "开心"、"东北话" 等
        voice_description: 音色描述文本（仅 mimo-v2.5-tts-voicedesign 模型使用）
        clone_audio_base64: 音色复刻的音频 Base64 编码（仅 mimo-v2.5-tts-voiceclone 模型使用）
    """
    try:
        client = AsyncOpenAI(
            api_key=api_key,
            base_url="https://api.xiaomimimo.com/v1",
            default_headers={"api-key": api_key},
        )

        # 构建 messages
        # 根据 API 文档：
        #   自然语言控制 → 放在 role: user 的 content 中
        #   音频标签控制 → 放在 role: assistant 的 content 中
        # 两种方式不应混用
        messages = []

        # user 消息：自然语言控制（风格描述 / 音色描述）
        if model == "mimo-v2.5-tts-voicedesign" and voice_description:
            messages.append({"role": "user", "content": voice_description})
        elif style:
            messages.append({"role": "user", "content": style})

        # assistant 消息：始终放纯文本（可包含 [] 音频标签，不添加 (风格) 标签）
        messages.append({"role": "assistant", "content": text})

        # 构建 audio 参数
        audio_params = {
            "format": audio_format,
        }

        # 根据模型类型设置音色
        if model == "mimo-v2.5-tts":
            audio_params["voice"] = voice
        elif model == "mimo-v2.5-tts-voiceclone" and clone_audio_base64:
            # 音色复刻：传入音频 Base64
            audio_params["voice"] = clone_audio_base64

        completion = await client.chat.completions.create(
            model=model,
            messages=messages,
            audio=audio_params,
        )

        message = completion.choices[0].message
        audio_obj = getattr(message, "audio", None)
        if audio_obj is not None:
            audio_b64 = audio_obj.data if hasattr(audio_obj, "data") else audio_obj.get("data")
        else:
            raw = message.model_extra or {}
            audio_b64 = raw.get("audio", {}).get("data")

        if not audio_b64:
            logger.error(f"[MimoTTS] 响应中未包含音频数据, response: {completion}")
            return None

        audio_bytes = base64.b64decode(audio_b64)
        logger.info(f"[MimoTTS] 合成成功，模型: {model}，音色: {voice}，音频大小: {len(audio_bytes)} 字节")
        return audio_bytes

    except Exception as e:
        logger.error(f"[MimoTTS] 合成失败: {e}")
        return None


def get_model_list() -> dict[str, str]:
    """获取可用模型列表"""
    return AVAILABLE_MODELS.copy()


def get_voice_list() -> dict[str, str]:
    """获取可用音色列表"""
    return AVAILABLE_VOICES.copy()


def get_format_list() -> list[str]:
    """获取支持的音频格式"""
    return AVAILABLE_FORMATS.copy()


def get_style_list() -> dict[str, str]:
    """获取可用风格列表"""
    return AVAILABLE_STYLES.copy()
