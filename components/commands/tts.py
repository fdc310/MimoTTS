from __future__ import annotations

import base64
import logging
from typing import AsyncGenerator

from langbot_plugin.api.definition.components.command.command import Command
from langbot_plugin.api.entities.builtin.command.context import ExecuteContext, CommandReturn
from langbot_plugin.api.entities.builtin.platform import message as platform_message
from components.tts_utils import call_mimo_tts, get_api_key, STORAGE_KEY_API

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
    "  !tts key <API Key>            - 设置 MiMo API Key\n"
    "  !tts key clear                - 清除命令设置的 Key\n"
    "  !tts help                     - 显示帮助\n"
    "\n可用音色：\n"
    "  mimo_default  MiMo 默认\n"
    "  default_zh    中文女声\n"
    "  default_en    英文女声\n"
    "\n风格示例：开心、悲伤、生气、东北话、粤语、台湾腔\n"
    "也可在文本中使用标签：!tts <style>开心</style>你好"
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
            name="key",
            help="设置或清除 MiMo API Key",
            usage="!tts key <API Key>",
            aliases=["k"],
        )
        async def tts_key(self, context: ExecuteContext) -> AsyncGenerator[CommandReturn, None]:
            if not context.crt_params:
                # 查看当前 key 状态
                api_key = await get_api_key(self.plugin)
                if api_key:
                    masked = api_key[:4] + "****" + api_key[-4:] if len(api_key) > 8 else "****"
                    yield CommandReturn(text=f"当前 API Key：{masked}\n用法：!tts key <新Key> 或 !tts key clear")
                else:
                    yield CommandReturn(text="API Key 未设置\n用法：!tts key <API Key>")
                return

            param = context.crt_params[0]

            if param == "clear":
                try:
                    await self.plugin.delete_plugin_storage(STORAGE_KEY_API)
                    logger.info("[MimoTTS] 已清除命令设置的 API Key")
                    yield CommandReturn(text="已清除命令设置的 API Key，将使用 WebUI 配置的 Key")
                except Exception:
                    yield CommandReturn(text="清除失败或本就未设置")
                return

            # 设置新 key
            new_key = param.strip()
            await self.plugin.set_plugin_storage(STORAGE_KEY_API, new_key.encode("utf-8"))
            masked = new_key[:4] + "****" + new_key[-4:] if len(new_key) > 8 else "****"
            logger.info(f"[MimoTTS] API Key 已通过命令设置: {masked}")
            yield CommandReturn(text=f"API Key 已设置：{masked}")

        @self.subcommand(
            name="*",
            help="将文本转换为语音（使用默认配置）",
            usage="!tts <文本>",
            aliases=["说", "say"],
        )
        async def tts_default(self, context: ExecuteContext) -> AsyncGenerator[CommandReturn, None]:
            api_key = await get_api_key(self.plugin)
            if not api_key:
                yield CommandReturn(text="请先设置 API Key：!tts key <你的Key>")
                return

            config = self.plugin.get_config()
            voice = config.get("default_voice", "mimo_default")
            style = config.get("default_style", "")
            text = " ".join(context.crt_params)

            if style:
                text = f"<style>{style}</style>{text}"

            logger.info(f"[MimoTTS] 命令合成，音色: {voice}，文本: {text[:50]}...")
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
            return
            yield  # noqa: unreachable — makes this function an AsyncGenerator

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

            api_key = await get_api_key(self.plugin)
            if not api_key:
                yield CommandReturn(text="请先设置 API Key：!tts key <你的Key>")
                return

            logger.info(f"[MimoTTS] 命令合成，音色: {voice}，文本: {text[:50]}...")
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
            return
            yield  # noqa: unreachable — makes this function an AsyncGenerator

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

            api_key = await get_api_key(self.plugin)
            if not api_key:
                yield CommandReturn(text="请先设置 API Key：!tts key <你的Key>")
                return

            config = self.plugin.get_config()
            voice = config.get("default_voice", "mimo_default")

            logger.info(f"[MimoTTS] 命令合成，风格: {style}，文本: {text[:50]}...")
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
            return
            yield  # noqa: unreachable — makes this function an AsyncGenerator

        @self.subcommand(
            name="help",
            help="显示帮助信息",
            usage="!tts help",
            aliases=["h", "帮助"],
        )
        async def tts_help(self, context: ExecuteContext) -> AsyncGenerator[CommandReturn, None]:
            yield CommandReturn(text=HELP_TEXT)
