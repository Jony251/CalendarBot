import os
import shutil
from typing import Optional


class SpeechServiceError(RuntimeError):
    pass


class SpeechService:
    def __init__(self, provider: str, openai_api_key: str, openai_model: str) -> None:
        self.provider = provider.lower().strip()
        self.openai_api_key = openai_api_key
        self.openai_model = openai_model

    def transcribe(self, audio_path: str) -> str:
        if not os.path.exists(audio_path):
            raise SpeechServiceError(f"Audio file not found: {audio_path}")

        if self.provider == "openai":
            return self._transcribe_openai(audio_path)
        if self.provider == "local":
            return self._transcribe_local_whisper(audio_path)

        raise SpeechServiceError(
            "Unsupported WHISPER_PROVIDER. Use 'openai' or 'local'."
        )

    def _transcribe_openai(self, audio_path: str) -> str:
        try:
            from openai import OpenAI
        except Exception as e:  # pragma: no cover
            raise SpeechServiceError(
                "openai package is required for WHISPER_PROVIDER=openai"
            ) from e

        client = OpenAI(api_key=self.openai_api_key)
        try:
            with open(audio_path, "rb") as f:
                result = client.audio.transcriptions.create(
                    model=self.openai_model,
                    file=f,
                )
        except Exception as e:
            msg = str(e)
            if "insufficient_quota" in msg or "exceeded your current quota" in msg:
                raise SpeechServiceError(
                    "OpenAI: недостаточно квоты/не включён биллинг для распознавания голоса. "
                    "Решение: пополните баланс/включите Billing в OpenAI, "
                    "или переключитесь на локальное распознавание (WHISPER_PROVIDER=local)."
                ) from e
            raise SpeechServiceError(f"OpenAI transcription failed: {e}") from e

        text = getattr(result, "text", None)
        if not text:
            raise SpeechServiceError("Empty transcription result")
        return text.strip()

    def _transcribe_local_whisper(self, audio_path: str) -> str:
        try:
            import whisper
        except Exception as e:  # pragma: no cover
            raise SpeechServiceError(
                "openai-whisper package is required for WHISPER_PROVIDER=local"
            ) from e

        if shutil.which("ffmpeg") is None:
            raise SpeechServiceError(
                "Локальный Whisper требует установленный ffmpeg (не найден в PATH). "
                "Установите ffmpeg и перезапустите терминал/IDE."
            )

        model = whisper.load_model("base")
        try:
            res = model.transcribe(audio_path)
        except FileNotFoundError as e:
            raise SpeechServiceError(
                "Local Whisper transcription failed: не найден исполняемый файл. "
                "Чаще всего это ffmpeg. Установите ffmpeg и добавьте в PATH."
            ) from e
        except Exception as e:
            raise SpeechServiceError(f"Local Whisper transcription failed: {e}") from e

        text: Optional[str] = res.get("text") if isinstance(res, dict) else None
        if not text:
            raise SpeechServiceError("Empty transcription result")
        return text.strip()
