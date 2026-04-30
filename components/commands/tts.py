from __future__ import annotations

import base64
import json
import logging
import re
from typing import AsyncGenerator, Optional

from langbot_plugin.api.definition.components.command.command import Command
from langbot_plugin.api.entities.builtin.command.context import ExecuteContext, CommandReturn
from langbot_plugin.api.entities.builtin.platform import message as platform_message
from components.tts_utils import (
    call_mimo_tts, AVAILABLE_VOICES, AVAILABLE_MODELS, AVAILABLE_STYLES,
    parse_cloned_voices, resolve_audio_source, DIRECTOR_TEMPLATES,
    parse_director_config, get_director_prompt,
    VOICE_DESIGN_PRESETS, parse_voice_design_presets,
)

logger = logging.getLogger(__name__)

HELP_TEXT = (
    "MiMo TTS V2.5 语音合成命令\n"
    "========================\n"
    "用法：\n"
    "  !tts <文本>                           - 默认配置合成\n"
    "  !tts voice <音色> <文本>              - 指定预置音色合成\n"
    "  !tts clone <名称> <文本>              - 使用已保存的克隆音色合成\n"
    "  !tts clone add <名称> <URL>           - 保存克隆音色\n"
    "  !tts clone list                       - 显示已保存的克隆音色\n"
    "  !tts clone remove <名称>              - 删除克隆音色\n"
    "  !tts style <风格> <文本>              - 指定风格合成\n"
    "  !tts design <音色描述> <文本>         - 音色设计合成\n"
    "  !tts director <模板> <文本>           - 导演模式合成\n"
    "  !tts director list                    - 显示预设导演模板\n"
    "  !tts director custom <名> <角色> <场景> <指导> - 自定义导演模板\n"
    "  !tts voices                           - 显示预置音色\n"
    "  !tts styles                           - 显示可用风格\n"
    "  !tts help                             - 显示帮助\n"
    "\n风格控制（两种方式）：\n"
    "  自然语言控制：!tts style <风格描述> <文本>\n"
    "    示例：!tts style 用轻快上扬的语调，带着激动与小骄傲 你好呀\n"
    "  标签控制：在文本中用括号标注风格\n"
    "    示例：!tts (开心)我太高兴了！\n"
    "    多风格：!tts (磁性)(慵懒)夜已经深了\n"
    "    方言：!tts (东北话)哎呀妈呀，这也太冷了吧\n"
    "\n音频细粒度标签（文中用括号，支持 ()、（）、[] 三种格式）：\n"
    "  节奏：(叹气)、(深呼吸)、(语速加快)、(语速放慢)、(停顿)、(沉默片刻)\n"
    "  情绪：(紧张)、(激动)、(疲惫)、(撒娇)、(震惊)\n"
    "  声音：(颤抖)、(哽咽)、(轻笑)、(大笑)、(冷笑)、(小声)\n"
    "  示例：!tts (紧张)(深呼吸)呼……冷静，冷静。(语速加快)加油！\n"
    "\n唱歌：\n"
    "  !tts (唱歌)原谅我这一生不羁放纵爱自由\n"
    "\n导演模式（!tts director list 查看全部模板）：\n"
    "  !tts director 冰山美人 你好，好久不见\n"
    "  !tts director 热血少年 我绝不会放弃！\n"
    "  !tts director 忧郁诗人 落叶飘零的季节又到了\n"
    "\n音色设计（!tts design list 查看全部预设）：\n"
    "  !tts design 温柔治愈女声 今晚月色真美\n"
    "  !tts design 磁性深夜DJ 夜深了，早点休息\n"
    "  !tts design ASMR助眠 晚安，做个好梦\n"
    "  !tts design 自定义描述 文本内容"
)


class TTS(Command):

    async def initialize(self):
        await super().initialize()

        @self.subcommand(
            name="",
            help="显示帮助信息",
            usage="!tts",
            aliases=[],
        )
        async def tts_root(self, context: ExecuteContext) -> AsyncGenerator[CommandReturn, None]:
            yield CommandReturn(text=HELP_TEXT)

        @self.subcommand(
            name="*",
            help="将文本转换为语音（使用默认配置）",
            usage="!tts <文本>",
            aliases=["说", "say"],
        )
        async def tts_default(self, context: ExecuteContext) -> AsyncGenerator[CommandReturn, None]:
            config = self.plugin.get_config()
            api_key = config.get("api_key") or ""
            if not api_key:
                yield CommandReturn(text="请先在插件配置中填写 MiMo API Key")
                return

            voice = config.get("default_voice") or "mimo_default"
            model = config.get("default_model") or "mimo-v2.5-tts"
            style = config.get("default_style") or ""
            audio_format = config.get("default_format") or "wav"

            text = " ".join(context.crt_params)

            logger.info(f"[MimoTTS] 命令合成，模型: {model}，音色: {voice}，文本: {text[:80]}...")
            audio_data = await call_mimo_tts(
                api_key, text, voice,
                model=model,
                audio_format=audio_format,
                style=style if style else None,
            )
            if audio_data is None:
                yield CommandReturn(text="语音合成失败，请检查 API Key 和网络连接。")
                return

            audio_b64_str = base64.b64encode(audio_data).decode()
            await context.reply(
                platform_message.MessageChain([
                    platform_message.Voice(base64=audio_b64_str),
                ])
            )
            yield CommandReturn()

        @self.subcommand(
            name="voice",
            help="指定音色合成语音",
            usage="!tts voice <音色> <文本>",
            aliases=["v", "音色"],
        )
        async def tts_voice(self, context: ExecuteContext) -> AsyncGenerator[CommandReturn, None]:
            if len(context.crt_params) < 2:
                voice_list = "\n".join([f"  {k} - {v}" for k, v in AVAILABLE_VOICES.items()])
                yield CommandReturn(
                    text=f"用法：!tts voice <音色> <文本>\n\n可用音色：\n{voice_list}"
                )
                return

            voice = context.crt_params[0]
            text = " ".join(context.crt_params[1:])

            if voice not in AVAILABLE_VOICES:
                yield CommandReturn(
                    text=f"未知音色：{voice}\n可用音色：{' / '.join(AVAILABLE_VOICES.keys())}"
                )
                return

            config = self.plugin.get_config()
            api_key = config.get("api_key") or ""
            if not api_key:
                yield CommandReturn(text="请先在插件配置中填写 MiMo API Key")
                return

            model = config.get("default_model") or "mimo-v2.5-tts"
            style = config.get("default_style") or ""
            audio_format = config.get("default_format") or "wav"

            logger.info(f"[MimoTTS] 命令合成，模型: {model}，音色: {voice}，文本: {text[:80]}...")
            audio_data = await call_mimo_tts(
                api_key, text, voice,
                model=model,
                audio_format=audio_format,
                style=style if style else None,
            )
            if audio_data is None:
                yield CommandReturn(text="语音合成失败，请检查 API Key 和网络连接。")
                return

            audio_b64_str = base64.b64encode(audio_data).decode()
            await context.reply(
                platform_message.MessageChain([
                    platform_message.Voice(base64=audio_b64_str),
                ])
            )
            yield CommandReturn()

        @self.subcommand(
            name="style",
            help="指定风格合成语音",
            usage="!tts style <风格> <文本>",
            aliases=["s", "风格"],
        )
        async def tts_style(self, context: ExecuteContext) -> AsyncGenerator[CommandReturn, None]:
            if len(context.crt_params) < 2:
                style_list = "\n".join([f"  {k} - {v}" for k, v in list(AVAILABLE_STYLES.items())[:20]])
                yield CommandReturn(
                    text=f"用法：!tts style <风格> <文本>\n\n可用风格（部分）：\n{style_list}\n\n更多风格请使用 !tts styles 查看"
                )
                return

            style = context.crt_params[0]
            text = " ".join(context.crt_params[1:])

            config = self.plugin.get_config()
            api_key = config.get("api_key") or ""
            if not api_key:
                yield CommandReturn(text="请先在插件配置中填写 MiMo API Key")
                return

            voice = config.get("default_voice") or "mimo_default"
            model = config.get("default_model") or "mimo-v2.5-tts"
            audio_format = config.get("default_format") or "wav"

            logger.info(f"[MimoTTS] 命令合成，模型: {model}，风格: {style}，文本: {text[:80]}...")
            audio_data = await call_mimo_tts(
                api_key, text, voice,
                model=model,
                audio_format=audio_format,
                style=style,
            )
            if audio_data is None:
                yield CommandReturn(text="语音合成失败，请检查 API Key 和网络连接。")
                return

            audio_b64_str = base64.b64encode(audio_data).decode()
            await context.reply(
                platform_message.MessageChain([
                    platform_message.Voice(base64=audio_b64_str),
                ])
            )
            yield CommandReturn()

        @self.subcommand(
            name="help",
            help="显示帮助信息",
            usage="!tts help",
            aliases=["h", "帮助"],
        )
        async def tts_help(self, context: ExecuteContext) -> AsyncGenerator[CommandReturn, None]:
            yield CommandReturn(text=HELP_TEXT)

        @self.subcommand(
            name="design",
            help="使用音色设计合成语音",
            usage="!tts design <音色描述> <文本>",
            aliases=["d", "设计"],
        )
        async def tts_design(self, context: ExecuteContext) -> AsyncGenerator[CommandReturn, None]:
            # 加载所有预设
            config = self.plugin.get_config()
            custom_str = config.get("voice_design_presets") or ""
            custom_presets = parse_voice_design_presets(custom_str)
            all_presets = {**VOICE_DESIGN_PRESETS, **custom_presets}

            if len(context.crt_params) < 1:
                # 无参数：检查是否有默认音色设计预设
                default_design = config.get("default_voice_design") or ""
                if default_design == "none":
                    default_design = ""
                if default_design and default_design in all_presets:
                    yield CommandReturn(
                        text=(
                            f"当前默认音色：{default_design}\n"
                            f"描述：{all_presets[default_design][:80]}\n\n"
                            "用法：!tts design <文本>\n"
                            "或指定其他预设：!tts design <预设名> <文本>\n"
                            "查看全部预设：!tts design list"
                        )
                    )
                else:
                    yield CommandReturn(
                        text=(
                            "用法：\n"
                            "  !tts design <音色描述> <文本>    - 自定义音色描述\n"
                            "  !tts design <预设名> <文本>      - 使用预设音色\n"
                            "  !tts design list                  - 查看预设音色列表\n"
                            "\n音色设计示例：\n"
                            "  !tts design 一位年轻活泼的女性，声音甜美清亮 你好呀\n"
                            "  !tts design 温柔治愈女声 今晚月色真美\n"
                            "  !tts design ASMR助眠 晚安，做个好梦"
                        )
                    )
                return

            sub_cmd = context.crt_params[0]

            if sub_cmd == "list":
                result = "预设音色设计：\n"
                for name, desc in all_presets.items():
                    short_desc = desc[:60] + "..." if len(desc) > 60 else desc
                    result += f"\n  {name}\n    {short_desc}"
                result += "\n\n使用方式：!tts design <预设名> <文本>"
                result += "\n自定义预设可在插件配置 voice_design_presets 中添加"
                yield CommandReturn(text=result)
                return

            # 检查是否是预设名称
            if sub_cmd in all_presets and len(context.crt_params) >= 2:
                voice_description = all_presets[sub_cmd]
                text = " ".join(context.crt_params[1:])
            elif len(context.crt_params) >= 2:
                # 自定义描述：第一个参数是描述，后面是文本
                voice_description = context.crt_params[0]
                text = " ".join(context.crt_params[1:])
            elif len(context.crt_params) == 1:
                # 只有一个参数：当作文本，使用默认音色设计
                default_design = config.get("default_voice_design") or ""
                if default_design == "none":
                    default_design = ""
                if default_design and default_design in all_presets:
                    voice_description = all_presets[default_design]
                    text = context.crt_params[0]
                else:
                    yield CommandReturn(text="用法：!tts design <音色描述或预设名> <文本>")
                    return
            else:
                yield CommandReturn(text="用法：!tts design <音色描述或预设名> <文本>")
                return

            api_key = config.get("api_key") or ""
            if not api_key:
                yield CommandReturn(text="请先在插件配置中填写 MiMo API Key")
                return

            audio_format = config.get("default_format") or "wav"

            logger.info(f"[MimoTTS] 音色设计合成，描述: {voice_description[:50]}...，文本: {text[:80]}...")
            audio_data = await call_mimo_tts(
                api_key, text,
                model="mimo-v2.5-tts-voicedesign",
                audio_format=audio_format,
                voice_description=voice_description,
            )
            if audio_data is None:
                yield CommandReturn(text="语音合成失败，请检查 API Key 和网络连接。")
                return

            audio_b64_str = base64.b64encode(audio_data).decode()
            await context.reply(
                platform_message.MessageChain([
                    platform_message.Voice(base64=audio_b64_str),
                ])
            )
            yield CommandReturn()

        @self.subcommand(
            name="clone",
            help="使用克隆音色合成语音",
            usage="!tts clone <名称> <文本>",
            aliases=["c", "复刻"],
        )
        async def tts_clone(self, context: ExecuteContext) -> AsyncGenerator[CommandReturn, None]:
            if len(context.crt_params) < 1:
                yield CommandReturn(
                    text=(
                        "用法：\n"
                        "  !tts clone <名称> <文本>        - 使用已保存的克隆音色合成\n"
                        "  !tts clone add <名称> <URL>     - 保存克隆音色\n"
                        "  !tts clone list                  - 显示已保存的克隆音色\n"
                        "  !tts clone remove <名称>         - 删除克隆音色\n"
                        "\n示例：\n"
                        "  !tts clone add 妈妈 https://example.com/voice.mp3\n"
                        "  !tts clone 妈妈 你好呀"
                    )
                )
                return

            # 子命令处理
            sub_cmd = context.crt_params[0]

            if sub_cmd == "add":
                # 保存克隆音色
                if len(context.crt_params) < 3:
                    yield CommandReturn(text="用法：!tts clone add <名称> <URL或Base64>")
                    return

                name = context.crt_params[1]
                audio_source = context.crt_params[2]

                config = self.plugin.get_config()
                cloned_voices_str = config.get("cloned_voices") or ""
                cloned_voices = parse_cloned_voices(cloned_voices_str)

                # 解析音频源
                audio_data = await resolve_audio_source(audio_source)
                if audio_data is None:
                    yield CommandReturn(text="音频源格式错误或下载失败，请提供有效的 URL 或 Base64 编码")
                    return

                # 保存到配置（使用行格式）
                cloned_voices[name] = audio_data
                lines = [f"{k}={v}" for k, v in cloned_voices.items()]
                self.plugin.set_config("cloned_voices", "\n".join(lines))

                yield CommandReturn(text=f"克隆音色「{name}」保存成功！")
                return

            elif sub_cmd == "list":
                # 显示已保存的克隆音色
                config = self.plugin.get_config()
                cloned_voices_str = config.get("cloned_voices") or ""
                cloned_voices = parse_cloned_voices(cloned_voices_str)

                if not cloned_voices:
                    yield CommandReturn(text="暂无保存的克隆音色\n\n使用 !tts clone add <名称> <URL> 添加")
                    return

                voice_list = "\n".join([f"  {k}" for k in cloned_voices.keys()])
                yield CommandReturn(text=f"已保存的克隆音色：\n{voice_list}")
                return

            elif sub_cmd == "remove":
                # 删除克隆音色
                if len(context.crt_params) < 2:
                    yield CommandReturn(text="用法：!tts clone remove <名称>")
                    return

                name = context.crt_params[1]
                config = self.plugin.get_config()
                cloned_voices_str = config.get("cloned_voices") or ""
                cloned_voices = parse_cloned_voices(cloned_voices_str)

                if name not in cloned_voices:
                    yield CommandReturn(text=f"未找到克隆音色「{name}」")
                    return

                del cloned_voices[name]
                lines = [f"{k}={v}" for k, v in cloned_voices.items()]
                self.plugin.set_config("cloned_voices", "\n".join(lines))

                yield CommandReturn(text=f"克隆音色「{name}」已删除")
                return

            else:
                # 使用克隆音色合成
                if len(context.crt_params) < 2:
                    yield CommandReturn(text="用法：!tts clone <名称> <文本>")
                    return

                name = context.crt_params[0]
                text = " ".join(context.crt_params[1:])

                config = self.plugin.get_config()
                api_key = config.get("api_key") or ""
                if not api_key:
                    yield CommandReturn(text="请先在插件配置中填写 MiMo API Key")
                    return

                cloned_voices_str = config.get("cloned_voices") or ""
                cloned_voices = parse_cloned_voices(cloned_voices_str)

                if name not in cloned_voices:
                    available = ", ".join(cloned_voices.keys()) if cloned_voices else "无"
                    yield CommandReturn(text=f"未找到克隆音色「{name}」\n\n已保存的克隆音色：{available}")
                    return

                audio_format = config.get("default_format") or "wav"
                style = config.get("default_style") or ""
                clone_audio_base64 = cloned_voices[name]

                logger.info(f"[MimoTTS] 克隆音色合成，名称: {name}，文本: {text[:80]}...")
                audio_data = await call_mimo_tts(
                    api_key, text,
                    model="mimo-v2.5-tts-voiceclone",
                    audio_format=audio_format,
                    clone_audio_base64=clone_audio_base64,
                    style=style if style else None,
                )
                if audio_data is None:
                    yield CommandReturn(text="语音合成失败，请检查 API Key 和网络连接。")
                    return

                audio_b64_str = base64.b64encode(audio_data).decode()
                await context.reply(
                    platform_message.MessageChain([
                        platform_message.Voice(base64=audio_b64_str),
                    ])
                )
                yield CommandReturn()

        @self.subcommand(
            name="director",
            help="导演模式合成语音",
            usage="!tts director <模板> <文本>",
            aliases=["dr", "导演"],
        )
        async def tts_director(self, context: ExecuteContext) -> AsyncGenerator[CommandReturn, None]:
            if len(context.crt_params) < 1:
                yield CommandReturn(
                    text=(
                        "用法：\n"
                        "  !tts director <模板> <文本>              - 使用预设模板\n"
                        "  !tts director <文本>                    - 使用默认模板（需在插件配置中设置）\n"
                        "  !tts director list                       - 显示预设模板\n"
                        "  !tts director custom <名> <角色> <场景> <指导> <文本> - 自定义模板\n"
                        "\n预设模板：冰山美人、热血少年、温柔姐姐、腹黑反派、天真萝莉、沉稳大叔、傲娇大小姐、神秘魔法师、忧郁诗人、元气偶像、铁血将军、慈祥奶奶、冷艳杀手、阳光暖男、古风仙侠、暴躁老板、软萌正太"
                    )
                )
                return

            sub_cmd = context.crt_params[0]

            if sub_cmd == "list":
                # 显示预设模板
                result = "预设导演模板：\n"
                for name, template in DIRECTOR_TEMPLATES.items():
                    result += f"\n【{name}】"
                    result += f"\n  角色：{template['role'][:50]}..."
                    result += f"\n  场景：{template['scene'][:50]}..."
                    result += f"\n  指导：{template['guide'][:50]}..."
                result += "\n\n使用方式：!tts director <模板名> <文本>"
                yield CommandReturn(text=result)
                return

            elif sub_cmd == "custom":
                # 自定义导演模板
                if len(context.crt_params) < 6:
                    yield CommandReturn(text="用法：!tts director custom <名称> <角色> <场景> <指导> <文本>")
                    return

                name = context.crt_params[1]
                role = context.crt_params[2]
                scene = context.crt_params[3]
                guide = context.crt_params[4]
                text = " ".join(context.crt_params[5:])

                # 构建导演模式描述
                voice_description = get_director_prompt(role, scene, guide)

                config = self.plugin.get_config()
                api_key = config.get("api_key") or ""
                if not api_key:
                    yield CommandReturn(text="请先在插件配置中填写 MiMo API Key")
                    return

                audio_format = config.get("default_format") or "wav"

                logger.info(f"[MimoTTS] 导演模式合成，名称: {name}，文本: {text[:80]}...")
                audio_data = await call_mimo_tts(
                    api_key, text,
                    model="mimo-v2.5-tts-voicedesign",
                    audio_format=audio_format,
                    voice_description=voice_description,
                )
                if audio_data is None:
                    yield CommandReturn(text="语音合成失败，请检查 API Key 和网络连接。")
                    return

                audio_b64_str = base64.b64encode(audio_data).decode()
                await context.reply(
                    platform_message.MessageChain([
                        platform_message.Voice(base64=audio_b64_str),
                    ])
                )
                yield CommandReturn()

            else:
                # 获取所有可用模板
                config = self.plugin.get_config()
                custom_templates_str = config.get("director_templates") or ""
                custom_templates = parse_director_config(custom_templates_str)
                all_templates = {**DIRECTOR_TEMPLATES, **custom_templates}

                template_name = context.crt_params[0]

                # 判断第一个参数是模板名还是文本
                if template_name in all_templates:
                    # 明确指定了模板名
                    if len(context.crt_params) < 2:
                        yield CommandReturn(text=f"用法：!tts director {template_name} <文本>")
                        return
                    text = " ".join(context.crt_params[1:])
                else:
                    # 第一个参数不是模板名，当作文本内容，使用默认模板
                    default_director = config.get("default_director") or ""
                    if default_director == "none":
                        default_director = ""
                    if not default_director:
                        yield CommandReturn(text="未指定导演模板，且未配置默认模板。请在插件配置中设置默认导演模板，或使用 !tts director <模板名> <文本>")
                        return
                    template_name = default_director
                    text = " ".join(context.crt_params)

                if template_name not in all_templates:
                    available = "、".join(all_templates.keys())
                    yield CommandReturn(text=f"未找到导演模板「{template_name}」\n\n可用模板：{available}")
                    return

                template = all_templates[template_name]
                voice_description = get_director_prompt(template["role"], template["scene"], template["guide"])

                api_key = config.get("api_key") or ""
                if not api_key:
                    yield CommandReturn(text="请先在插件配置中填写 MiMo API Key")
                    return

                audio_format = config.get("default_format") or "wav"

                logger.info(f"[MimoTTS] 导演模式合成，模板: {template_name}，文本: {text[:80]}...")
                audio_data = await call_mimo_tts(
                    api_key, text,
                    model="mimo-v2.5-tts-voicedesign",
                    audio_format=audio_format,
                    voice_description=voice_description,
                )
                if audio_data is None:
                    yield CommandReturn(text="语音合成失败，请检查 API Key 和网络连接。")
                    return

                audio_b64_str = base64.b64encode(audio_data).decode()
                await context.reply(
                    platform_message.MessageChain([
                        platform_message.Voice(base64=audio_b64_str),
                    ])
                )
                yield CommandReturn()

        @self.subcommand(
            name="voices",
            help="显示可用音色列表",
            usage="!tts voices",
            aliases=["vl", "音色列表"],
        )
        async def tts_voices(self, context: ExecuteContext) -> AsyncGenerator[CommandReturn, None]:
            voice_list = "\n".join([f"  {k} - {v}" for k, v in AVAILABLE_VOICES.items()])
            yield CommandReturn(text=f"可用音色：\n{voice_list}")

        @self.subcommand(
            name="styles",
            help="显示可用风格列表",
            usage="!tts styles",
            aliases=["sl", "风格列表"],
        )
        async def tts_styles(self, context: ExecuteContext) -> AsyncGenerator[CommandReturn, None]:
            style_categories = {
                "基础情绪": ["开心", "悲伤", "愤怒", "恐惧", "惊讶", "兴奋", "委屈", "平静", "冷漠"],
                "复合情绪": ["怅然", "欣慰", "无奈", "愧疚", "释然", "嫉妒", "厌倦", "忐忑", "动情"],
                "整体语调": ["温柔", "高冷", "活泼", "严肃", "慵懒", "俏皮", "深沉", "干练", "凌厉"],
                "音色定位": ["磁性", "醇厚", "清亮", "空灵", "稚嫩", "苍老", "甜美", "沙哑", "醇雅"],
                "人设腔调": ["夹子音", "御姐音", "正太音", "大叔音", "台湾腔"],
                "方言": ["东北话", "四川话", "河南话", "粤语"],
                "其他": ["唱歌"],
            }
            result = "可用风格：\n"
            for category, styles in style_categories.items():
                result += f"\n{category}：{' / '.join(styles)}"
            result += "\n\n使用方式：!tts style <风格> <文本>"
            yield CommandReturn(text=result)

        @self.subcommand(
            name="models",
            help="显示可用模型列表",
            usage="!tts models",
            aliases=["ml", "模型列表"],
        )
        async def tts_models(self, context: ExecuteContext) -> AsyncGenerator[CommandReturn, None]:
            model_list = "\n".join([f"  {k} - {v}" for k, v in AVAILABLE_MODELS.items()])
            yield CommandReturn(text=f"可用模型：\n{model_list}")
