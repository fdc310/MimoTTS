"""MiMo TTS API 调用工具模块"""
from __future__ import annotations

import base64
import logging

from openai import AsyncOpenAI

logger = logging.getLogger(__name__)


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
