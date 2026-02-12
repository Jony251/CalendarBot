import datetime as dt
import json
import os
import tempfile
from zoneinfo import ZoneInfo
import re
import logging

import dateparser
from dateparser.search import search_dates
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters

from calendar_service import CalendarService, CalendarServiceError
from config import Config
from speech_service import SpeechService, SpeechServiceError


logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    level=logging.INFO,
)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "Напишите или продиктуйте встречу. Пример: 'Созвон с Петром завтра в 15:30 на 45 минут'."
    )


def _normalize_text(text: str) -> str:
    t = text.strip()
    t = re.sub(r"\b(\d{1,2})[-.](\d{2})\b", r"\1:\2", t)
    t = re.sub(r"\b(\d{1,2})\s*h\s*(\d{2})\b", r"\1:\2", t, flags=re.IGNORECASE)
    return t


def _parse_start_datetime_fallback(text: str, tz: str) -> dt.datetime | None:
    settings = {
        "PREFER_DATES_FROM": "future",
        "TIMEZONE": tz,
        "RETURN_AS_TIMEZONE_AWARE": True,
    }

    found = search_dates(text, languages=["ru"], settings=settings)
    if not found:
        parsed = dateparser.parse(text, languages=["ru"], settings=settings)
        return parsed

    _, when = found[0]
    return when


def _fallback_title(text: str) -> str:
    t = text.strip()
    lowered = t.lower()

    if re.search(r"\b(к\s+зубному|к\s+стоматологу|стоматолог)\b", lowered):
        return "Зубной врач"

    t = re.sub(
        r"^\s*(запиши(те)?\s+меня|запланируй|добавь|поставь|назначь)\b\s*",
        "",
        t,
        flags=re.IGNORECASE,
    )
    t = re.sub(r"\s+на\s+\S+.*$", "", t, flags=re.IGNORECASE)
    t = re.sub(r"\s+в\s+\d{1,2}:\d{2}.*$", "", t, flags=re.IGNORECASE)
    t = t.strip(" -—:;,.\t\n")

    if not t:
        return "Встреча"

    return t[:120].capitalize()


def _extract_explicit_fields(text: str) -> dict:
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if len(lines) < 2:
        return {}

    patterns = {
        "title": re.compile(r"^(?:титул|заголовок|title)\s*(?:это)?\s*[-—:=]+\s*(.+)$", re.IGNORECASE),
        "date": re.compile(r"^(?:дата|date)\s*[-—:=]+\s*(.+)$", re.IGNORECASE),
        "time": re.compile(r"^(?:время|time)\s*[-—:=]+\s*(.+)$", re.IGNORECASE),
        "duration": re.compile(
            r"^(?:протяженность|длительность|duration)\s*(?:\(.*\))?\s*[-—:=]+\s*(.+)$",
            re.IGNORECASE,
        ),
    }

    out: dict = {}
    for ln in lines:
        for key, rx in patterns.items():
            m = rx.match(ln)
            if m:
                out[key] = m.group(1).strip()

    if not out:
        return {}

    title = (out.get("title") or "").strip()
    date_s = (out.get("date") or "").strip()
    time_s = (out.get("time") or "").strip()
    duration_raw = (out.get("duration") or "").strip()

    duration_minutes = None
    if duration_raw:
        m = re.search(r"(\d+)", duration_raw)
        if m:
            n = int(m.group(1))
            if re.search(r"\b(час|часа|часов|h|hour)\b", duration_raw, re.IGNORECASE):
                duration_minutes = n * 60
            else:
                duration_minutes = n

    start_datetime = ""
    if date_s and time_s:
        start_datetime = f"{date_s} {time_s}"
    elif date_s:
        start_datetime = date_s

    res = {
        "title": title,
        "start_datetime": start_datetime,
        "duration_minutes": duration_minutes or 60,
    }
    if not res["title"] and not res["start_datetime"]:
        return {}
    return res


def _fallback_duration_minutes(text: str) -> int:
    lowered = text.lower()
    for token in [" минут", " мин", "min", "minutes"]:
        if token in lowered:
            try:
                left = lowered.split(token)[0]
                num = int("".join(ch for ch in left.split()[-1] if ch.isdigit()))
                if 5 <= num <= 24 * 60:
                    return num
            except Exception:
                pass

    for token in [" час", " часа", " часов", "hour", "hours"]:
        if token in lowered:
            try:
                left = lowered.split(token)[0]
                num = int("".join(ch for ch in left.split()[-1] if ch.isdigit()))
                if 1 <= num <= 24:
                    return num * 60
            except Exception:
                pass

    return 60


def _openai_extract_event_json(text: str, cfg: Config) -> dict:
    from openai import OpenAI

    client = OpenAI(api_key=cfg.openai_api_key)

    now = dt.datetime.now(ZoneInfo(cfg.tz))
    prompt = (
        "Ты помощник-секретарь. Извлеки из сообщения данные встречи. "
        "Верни только валидный JSON строго по схеме: "
        '{"title":"","start_datetime":"","duration_minutes":60}. '\
        "Правила: title — короткий заголовок без лишних слов вроде 'запиши меня', максимум 3-6 слов. "
        "Если речь про стоматолога/зубного — title сделай 'Зубной врач'. "
        "start_datetime верни в ISO 8601 с часовым поясом. "
        f"Текущая дата/время пользователя: {now.isoformat()}. "
        "Если длительность не указана, ставь 60. "
        "Если дату/время нельзя определить, оставь start_datetime пустым. "
        "Примеры: "
        "\nВвод: 'запиши меня к зубному на завтра в 12:00' -> {\"title\":\"Зубной врач\",\"start_datetime\":\"<завтра 12:00 в ISO>\",\"duration_minutes\":60}"
        "\nВвод: 'созвон с Петром в пятницу в 15:30 на 45 минут' -> {\"title\":\"Созвон с Петром\",\"start_datetime\":\"...\",\"duration_minutes\":45}"
    )

    kwargs = {
        "model": cfg.openai_model,
        "messages": [
            {"role": "system", "content": "Отвечай только JSON без пояснений."},
            {"role": "user", "content": prompt + "\n\nСообщение: " + text},
        ],
        "temperature": 0,
    }

    try:
        kwargs["response_format"] = {"type": "json_object"}
    except Exception:
        pass

    resp = client.chat.completions.create(**kwargs)

    content = (resp.choices[0].message.content or "{}").strip()
    content = re.sub(r"^```(?:json)?\s*", "", content, flags=re.IGNORECASE)
    content = re.sub(r"\s*```$", "", content)

    try:
        return json.loads(content)
    except Exception:
        start = content.find("{")
        end = content.rfind("}")
        if start != -1 and end != -1 and end > start:
            return json.loads(content[start : end + 1])
        raise


async def _handle_text_common(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str) -> None:
    cfg: Config = context.bot_data["cfg"]
    cal: CalendarService = context.bot_data["calendar"]

    normalized = _normalize_text(text)

    explicit = _extract_explicit_fields(normalized)
    if explicit:
        title = (explicit.get("title") or "").strip() or _fallback_title(normalized)
        duration = int(explicit.get("duration_minutes") or 60)
        start_raw = (explicit.get("start_datetime") or "").strip()
        start_dt = _parse_start_datetime_fallback(_normalize_text(start_raw), cfg.tz) if start_raw else None
        if not start_dt:
            await update.message.reply_text(
                "Не смог распознать дату/время из полей. Уточните (пример: 'дата - 13.02.2026' и 'время - 12:00')."
            )
            return

        if start_dt.tzinfo is None:
            start_dt = start_dt.replace(tzinfo=ZoneInfo(cfg.tz))

        try:
            link = cal.create_event(title=title, start_dt=start_dt, duration_minutes=duration)
        except CalendarServiceError as e:
            await update.message.reply_text(f"Ошибка Google Calendar: {e}")
            return

        msg = f"Добавил: {title}\nНачало: {start_dt.isoformat()}\nДлительность: {duration} мин"
        if link:
            msg += f"\nСсылка: {link}"
        await update.message.reply_text(msg)
        return

    try:
        data = _openai_extract_event_json(normalized, cfg)
    except Exception:
        data = {}

    title = (data.get("title") or "").strip() or _fallback_title(normalized)
    duration = int(data.get("duration_minutes") or _fallback_duration_minutes(normalized) or 60)

    start_str = (data.get("start_datetime") or "").strip()
    start_dt: dt.datetime | None = None

    if start_str:
        try:
            start_dt = dt.datetime.fromisoformat(start_str)
        except Exception:
            start_dt = None

    if not start_dt:
        start_dt = _parse_start_datetime_fallback(normalized, cfg.tz)

    if not start_dt:
        await update.message.reply_text(
            "Не смог распознать дату/время. Уточните, пожалуйста (пример: 'в пятницу в 14:00')."
        )
        return

    if start_dt.tzinfo is None:
        start_dt = start_dt.replace(tzinfo=ZoneInfo(cfg.tz))

    try:
        link = cal.create_event(title=title, start_dt=start_dt, duration_minutes=duration)
    except CalendarServiceError as e:
        await update.message.reply_text(f"Ошибка Google Calendar: {e}")
        return

    msg = f"Добавил: {title}\nНачало: {start_dt.isoformat()}\nДлительность: {duration} мин"
    if link:
        msg += f"\nСсылка: {link}"

    await update.message.reply_text(msg)


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.message.text:
        return
    logging.info("Text message received: %s", update.message.text)
    await _handle_text_common(update, context, update.message.text)


async def handle_voice(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message or not update.message.voice:
        return

    logging.info("Voice message received: file_id=%s", update.message.voice.file_id)

    cfg: Config = context.bot_data["cfg"]
    speech: SpeechService = context.bot_data["speech"]

    file = await context.bot.get_file(update.message.voice.file_id)

    with tempfile.TemporaryDirectory() as tmpdir:
        audio_path = os.path.join(tmpdir, "voice.ogg")
        await file.download_to_drive(audio_path)

        try:
            text = speech.transcribe(audio_path)
        except SpeechServiceError as e:
            await update.message.reply_text(f"Не удалось распознать голос: {e}")
            return

    await update.message.reply_text(f"Распознал: {text}")
    await _handle_text_common(update, context, text)


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logging.exception("Unhandled exception while handling an update", exc_info=context.error)
    if isinstance(update, Update) and update.effective_message:
        try:
            await update.effective_message.reply_text(
                "Произошла внутренняя ошибка. Попробуйте ещё раз."
            )
        except Exception:
            pass


def main() -> None:
    cfg = Config()

    logging.info("Starting Telegram bot polling...")

    app = Application.builder().token(cfg.telegram_token).build()

    app.bot_data["cfg"] = cfg
    app.bot_data["speech"] = SpeechService(
        provider=cfg.whisper_provider,
        openai_api_key=cfg.openai_api_key,
        openai_model=cfg.openai_whisper_model,
    )
    app.bot_data["calendar"] = CalendarService(
        client_id=cfg.google_client_id,
        client_secret=cfg.google_client_secret,
        project_id=cfg.google_project_id,
        redirect_uri=cfg.google_redirect_uri,
        client_type=cfg.google_oauth_client_type,
        local_server_port=cfg.google_oauth_local_server_port,
        calendar_id=cfg.google_calendar_id,
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_handler(MessageHandler(filters.VOICE, handle_voice))
    app.add_error_handler(error_handler)

    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
