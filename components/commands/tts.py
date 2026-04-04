from __future__ import annotations

import base64
import logging
import re
from typing import AsyncGenerator

from langbot_plugin.api.definition.components.command.command import Command
from langbot_plugin.api.entities.builtin.command.context import ExecuteContext, CommandReturn
from langbot_plugin.api.entities.builtin.platform import message as platform_message
from components.tts_utils import call_mimo_tts

logger = logging.getLogger(__name__)

VALID_VOICES = {
    "mimo_default": "MiMo 默认",
    "default_zh": "中文女声",
    "default_en": "英文女声",
}

HELP_TEXT = (
    "MiMo TTS 语音合成命令\n"
    "========================\n"
    "用法：\n"
    "  !tts <文本>                   - 默认音色合成\n"
    "  !tts voice <音色> <文本>      - 指定音色\n"
    "  !tts style <风格> <文本>      - 指定风格\n"
    "  !tts help                     - 显示帮助\n"
    "\n可用音色：\n"
    "  mimo_default  MiMo 默认\n"
    "  default_zh    中文女声\n"
    "  default_en    英文女声\n"
    "\n风格示例：开心、悲伤、生气、东北话、粤语、台湾腔\n"
    "也可在文本中使用标签：!tts <style>开心</style>你好"
)


def _apply_default_style(text: str, style: str) -> str:
    """智能注入默认风格：已有 <style> 或唱歌内容不再拼"""
    if not style:
        return text
    # 已有 <style> 标签，不重复添加
    if re.match(r"^<style>", text):
        return text
    return f"<style>{style}</style>{text}"


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
            style = config.get("default_style") or ""
            text = " ".join(context.crt_params)
            text = _apply_default_style(text, style)

            logger.info(f"[MimoTTS] 命令合成，音色: {voice}，文本: {text[:80]}...")
            audio_data = await call_mimo_tts(api_key, text, voice)
            if audio_data is None:
                yield CommandReturn(text="语音合成失败，请检查 API Key 和网络连接。")
                return

            data_url = f"data:audio/wav;base64,{base64.b64encode(audio_data).decode()}"
            await context.reply(
                platform_message.MessageChain([
                    platform_message.Voice(url=data_url),
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
                yield CommandReturn(
                    text=(
                        "用法：!tts voice <音色> <文本>\n"
                        "可用音色：mimo_default / default_zh / default_en"
                    )
                )
                return

            voice = context.crt_params[0]
            text = " ".join(context.crt_params[1:])

            if voice not in VALID_VOICES:
                yield CommandReturn(
                    text=f"未知音色：{voice}\n可用音色：{' / '.join(VALID_VOICES.keys())}"
                )
                return

            config = self.plugin.get_config()
            api_key = config.get("api_key") or ""
            if not api_key:
                yield CommandReturn(text="请先在插件配置中填写 MiMo API Key")
                return

            logger.info(f"[MimoTTS] 命令合成，音色: {voice}，文本: {text[:80]}...")
            audio_data = await call_mimo_tts(api_key, text, voice)
            if audio_data is None:
                yield CommandReturn(text="语音合成失败，请检查 API Key 和网络连接。")
                return

            data_url = f"data:audio/wav;base64,{base64.b64encode(audio_data).decode()}"
            await context.reply(
                platform_message.MessageChain([
                    platform_message.Voice(url=data_url),
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
                yield CommandReturn(
                    text="用法：!tts style <风格> <文本>\n风格示例：开心、悲伤、东北话、粤语、台湾腔"
                )
                return

            style = context.crt_params[0]
            text = " ".join(context.crt_params[1:])
            styled_text = f"<style>{style}</style>{text}"

            config = self.plugin.get_config()
            api_key = config.get("api_key") or ""
            if not api_key:
                yield CommandReturn(text="请先在插件配置中填写 MiMo API Key")
                return

            voice = config.get("default_voice") or "mimo_default"

            logger.info(f"[MimoTTS] 命令合成，风格: {style}，文本: {text[:80]}...")
            audio_data = await call_mimo_tts(api_key, styled_text, voice)
            if audio_data is None:
                yield CommandReturn(text="语音合成失败，请检查 API Key 和网络连接。")
                return

            data_url = f"data:audio/wav;base64,{base64.b64encode(audio_data).decode()}"
            await context.reply(
                platform_message.MessageChain([
                    platform_message.Voice(url=data_url),
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
