"""MiMo TTS API 调用工具模块"""
from __future__ import annotations

import base64
import logging
from typing import Any

from openai import AsyncOpenAI

logger = logging.getLogger(__name__)

STORAGE_KEY_API = "mimo_tts_api_key"


async def get_api_key(plugin: Any) -> str:
    """获取 API Key：命令设置的优先，其次 WebUI 配置"""
    # 优先从持久化存储读取（通过命令设置的）
    try:
        keys = await plugin.get_plugin_storage_keys()
        if STORAGE_KEY_API in keys:
            stored = await plugin.get_plugin_storage(STORAGE_KEY_API)
            if stored:
                return stored.decode("utf-8")
    except BaseException:
        pass
    # 回退到 WebUI 配置
    return plugin.get_config().get("api_key", "")


async def call_mimo_tts(api_key: str, text: str, voice: str) -> bytes | None:
    """调用 MiMo TTS API，返回 WAV 字节数据，失败返回 None"""
    try:
        client = AsyncOpenAI(
            api_key=api_key,
            base_url="https://api.xiaomimimo.com/v1",
            default_headers={"api-key": api_key},
        )

        completion = await client.chat.completions.create(
            model="mimo-v2-tts",
            messages=[
                {
                    "role": "assistant",
                    "content": text,
                }
            ],
            extra_body={
                "audio": {
                    "format": "wav",
                    "voice": voice,
                }
            },
        )

        message = completion.choices[0].message
        audio_obj = getattr(message, "audio", None)
        if audio_obj is not None:
            audio_b64 = audio_obj.data if hasattr(audio_obj, "data") else audio_obj.get("data")
        else:
            raw = message.model_extra or {}
            audio_b64 = raw.get("audio", {}).get("data")

        if not audio_b64:
            logger.error("[MimoTTS] 响应中未包含音频数据")
            return None

        audio_bytes = base64.b64decode(audio_b64)
        logger.info(f"[MimoTTS] 合成成功，音频大小: {len(audio_bytes)} 字节")
        return audio_bytes

    except Exception as e:
        logger.error(f"[MimoTTS] 合成失败: {e}")
        return None
