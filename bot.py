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


_RU_NUM_WORDS: dict[str, int] = {
    "ноль": 0,
    "один": 1,
    "одна": 1,
    "одно": 1,
    "два": 2,
    "две": 2,
    "три": 3,
    "четыре": 4,
    "пять": 5,
    "шесть": 6,
    "семь": 7,
    "восемь": 8,
    "девять": 9,
    "десять": 10,
    "одиннадцать": 11,
    "двенадцать": 12,
}


def _ru_hour_to_24h(hour: int, part_of_day: str | None) -> int:
    part = (part_of_day or "").lower().strip()

    if part in {"вечера", "дня"}:
        if 1 <= hour <= 11:
            return hour + 12
        return hour

    if part == "ночи":
        if hour == 12:
            return 0
        return hour

    return hour


def _normalize_russian_word_time(text: str) -> str:
    t = text

    def repl(m: re.Match) -> str:
        raw_hour = (m.group("hour") or "").strip().lower()
        part = m.group("part")

        if raw_hour.isdigit():
            h = int(raw_hour)
        else:
            h = _RU_NUM_WORDS.get(raw_hour, -1)

        if h < 0 or h > 23:
            return m.group(0)

        h = _ru_hour_to_24h(h, part)
        return f"{h:02d}:00"

    # Examples:
    # - "в пять часов вечера"
    # - "в 5 часов вечера"
    # - "в пять вечера"
    # - "в 5 вечера"
    t = re.sub(
        r"\bв\s+(?P<hour>\d{1,2}|один|одна|два|две|три|четыре|пять|шесть|семь|восемь|девять|десять|одиннадцать|двенадцать)"
        r"(?:\s*час(?:а|ов)?)?\s*(?P<part>утра|дня|вечера|ночи)\b",
        repl,
        t,
        flags=re.IGNORECASE,
    )

    return t


def _normalize_text(text: str) -> str:
    t = text.strip()
    t = _normalize_russian_word_time(t)
    t = re.sub(r"\b(\d{1,2})[-.](\d{2})\b", r"\1:\2", t)
    t = re.sub(r"\b(\d{1,2})\s*h\s*(\d{2})\b", r"\1:\2", t, flags=re.IGNORECASE)
    return t


def _extract_time_range(text: str) -> tuple[str | None, str | None]:
    t = text
    m = re.search(
        r"\b(?:в\s*)?(\d{1,2}:\d{2})\s*(?:до|\-|–|—|to)\s*(\d{1,2}:\d{2})\b",
        t,
        flags=re.IGNORECASE,
    )
    if not m:
        return None, None
    return m.group(1), m.group(2)


def _extract_first_time(text: str) -> str | None:
    m = re.search(r"\b(\d{1,2}):(\d{2})\b", text)
    if not m:
        return None
    hh = int(m.group(1))
    mm = int(m.group(2))
    if 0 <= hh <= 23 and 0 <= mm <= 59:
        return f"{hh:02d}:{mm:02d}"
    return None


def _combine_date_and_time(base: dt.datetime, time_str: str, tz: str) -> dt.datetime | None:
    try:
        hh, mm = time_str.split(":")
        h = int(hh)
        m = int(mm)
    except Exception:
        return None

    tzinfo = base.tzinfo or ZoneInfo(tz)
    return dt.datetime(base.year, base.month, base.day, h, m, tzinfo=tzinfo)


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

    t = re.sub(
        r"^\s*(есть|будет|нужно|надо|хочу|у\s+меня|у\s+нас)\b\s*[:\-—]*\s*",
        "",
        t,
        flags=re.IGNORECASE,
    )
    lowered = t.lower()

    if re.search(r"\b(к\s+зубному|к\s+стоматологу|стоматолог)\b", lowered):
        return "Зубной врач"

    if re.search(r"\b(к\s+врачу|прием\s+у\s+врача|прие?м\s+у\s+врача|врач)\b", lowered):
        return "Приём у врача"

    if re.search(r"\b(налоговую|налоговая|налоговая\s+инспекция|ифнс)\b", lowered):
        return "Налоговая"

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


def _extract_notes_fallback(text: str) -> str:
    t = text.strip()
    lowered = t.lower()
    markers = [
        "документы",
        "документ",
        "взять",
        "принести",
        "не забыть",
        "очередь",
    ]

    idx = None
    for m in markers:
        pos = lowered.find(m)
        if pos != -1:
            idx = pos if idx is None else min(idx, pos)

    if idx is None:
        return ""

    notes = t[idx:].strip(" -—:;,.\t\n")
    return notes[:800]


def _extract_title_fallback(text: str) -> str:
    t = text.strip()
    t = re.sub(
        r"^\s*(есть|будет|нужно|надо|хочу|у\s+меня|у\s+нас)\b\s*[:\-—]*\s*",
        "",
        t,
        flags=re.IGNORECASE,
    )
    lowered = t.lower()

    if re.search(r"\b(налоговую|налоговая|налоговая\s+инспекция|ифнс)\b", lowered):
        return "Налоговая"
    if re.search(r"\b(к\s+зубному|к\s+стоматологу|стоматолог)\b", lowered):
        return "Зубной врач"

    cut_markers = ["документы", "документ", "очередь", "принести", "взять", "не забыть"]
    cut_idx = None
    for m in cut_markers:
        pos = lowered.find(m)
        if pos != -1:
            cut_idx = pos if cut_idx is None else min(cut_idx, pos)

    if cut_idx is not None:
        t = t[:cut_idx]

    t = re.sub(r"\s+\d{1,2}\s+[а-яA-Za-z]+\s+\d{2,4}.*$", "", t, flags=re.IGNORECASE)
    t = re.sub(r"\s+\d{1,2}:\d{2}.*$", "", t, flags=re.IGNORECASE)
    t = re.sub(r"\s+в\s+\d{1,2}:\d{2}.*$", "", t, flags=re.IGNORECASE)
    t = t.strip(" -—:;,.\t\n")

    return (t[:80].capitalize() if t else "Встреча")


def _extract_explicit_fields(text: str) -> dict:
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if len(lines) < 2:
        return {}

    patterns = {
        "title": re.compile(r"^(?:титул|заголовок|title)\s*(?:это)?\s*[-—:=]+\s*(.+)$", re.IGNORECASE),
        "date": re.compile(r"^(?:дата|date)\s*[-—:=]+\s*(.+)$", re.IGNORECASE),
        "time": re.compile(r"^(?:время|time)\s*[-—:=]+\s*(.+)$", re.IGNORECASE),
        "end": re.compile(r"^(?:конец|окончание|end)\s*[-—:=]+\s*(.+)$", re.IGNORECASE),
        "duration": re.compile(
            r"^(?:протяженность|длительность|duration)\s*(?:\(.*\))?\s*[-—:=]+\s*(.+)$",
            re.IGNORECASE,
        ),
        "notes": re.compile(r"^(?:заметки|описание|документы|notes)\s*[-—:=]+\s*(.+)$", re.IGNORECASE),
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
    end_s = (out.get("end") or "").strip()
    duration_raw = (out.get("duration") or "").strip()
    notes = (out.get("notes") or "").strip()

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

    end_datetime = ""
    if date_s and end_s:
        end_datetime = f"{date_s} {end_s}"

    res = {
        "title": title,
        "start_datetime": start_datetime,
        "end_datetime": end_datetime,
        "duration_minutes": duration_minutes or 60,
        "notes": notes,
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
        "Ты профессиональный секретарь-парсер событий. "
        "Твоя задача — извлечь из сообщения данные события и вернуть ТОЛЬКО валидный JSON. "
        "Никакого текста вне JSON. "

        "Строгая схема ответа: "
        '{"title":"","start_datetime":"","end_datetime":"","duration_minutes":60,"notes":""}. '

        "Правила обработки: "

        "1. title — короткая тема события (1-4 слова), без даты, времени и документов. "
        "   Если есть цель (например 'на собеседование') — это и есть title. "
        "   Если указано 'к врачу' — title = 'Врач'. "
        "   Если указано 'к зубному' — title = 'Зубной врач'. "
        "   Если указано 'в банк на собеседование' — title = 'Собеседование'. "

        "2. start_datetime и end_datetime вернуть в ISO 8601 формате с часовым поясом +03:00. "
        "   Формат: YYYY-MM-DDTHH:MM:SS+03:00 "

        "3. Если указано 'сегодня' — используй сегодняшнюю дату. "
        "   Если указано 'завтра' — прибавь 1 день к текущей дате. "

        f"Текущая дата пользователя: {now.strftime('%Y-%m-%d')}. "
        f"Текущее время пользователя: {now.strftime('%H:%M')}. "

        "4. Если указан диапазон времени (например 'с 8:00 до 14:00'), "
        "   заполни и start_datetime и end_datetime. "

        "5. Если указано только одно время — заполни только start_datetime. "

        "6. duration_minutes — "
        "   если указана длительность ('на 45 минут') — используй её. "
        "   иначе всегда 60. "

        "7. notes — перечисли, что нужно взять или подготовить. "
        "   Убери слова 'взять', 'принести', 'не забыть'. "
        "   Перечисляй через '; '. "
        "   Если ничего не указано — оставь пустую строку."

        "Примеры: "

        "\nВвод: 'запиши меня к врачу сегодня на 19-00' "
        "-> {\"title\":\"Врач\",\"start_datetime\":\"2026-02-12T19:00:00+03:00\",\"end_datetime\":\"\",\"duration_minutes\":60,\"notes\":\"\"}"

        "\nВвод: 'сходить в банк завтра на собеседование в 10:30, взять диплом и паспорт' "
    "-> {\"title\":\"Собеседование\",\"start_datetime\":\"2026-02-13T10:30:00+03:00\",\"end_datetime\":\"\",\"duration_minutes\":60,\"notes\":\"диплом; паспорт\"}"
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
        end_raw = (explicit.get("end_datetime") or "").strip()
        notes = (explicit.get("notes") or "").strip()

        start_dt = _parse_start_datetime_fallback(_normalize_text(start_raw), cfg.tz) if start_raw else None
        end_dt = _parse_start_datetime_fallback(_normalize_text(end_raw), cfg.tz) if end_raw else None
        if not start_dt:
            await update.message.reply_text(
                "Не смог распознать дату/время из полей. Уточните (пример: 'дата - 13.02.2026' и 'время - 12:00')."
            )
            return

        if start_dt.tzinfo is None:
            start_dt = start_dt.replace(tzinfo=ZoneInfo(cfg.tz))

        try:
            link = cal.create_event(
                title=title,
                start_dt=start_dt,
                duration_minutes=duration,
                end_dt=end_dt,
                description=notes,
            )
        except CalendarServiceError as e:
            await update.message.reply_text(f"Ошибка Google Calendar: {e}")
            return

        msg = f"Добавил: {title}\nНачало: {start_dt.isoformat()}"
        if end_dt:
            msg += f"\nКонец: {end_dt.isoformat()}"
        msg += f"\nДлительность: {duration} мин"
        if notes:
            msg += f"\nЗаметки: {notes}"
        if link:
            msg += f"\nСсылка: {link}"
        await update.message.reply_text(msg)
        return

    try:
        data = _openai_extract_event_json(normalized, cfg)
    except Exception:
        data = {}

    notes = (data.get("notes") or "").strip()
    if not notes:
        notes = _extract_notes_fallback(normalized)

    title = (data.get("title") or "").strip()
    if not title or len(title) > 60 or re.search(r"\b(документ|документы|паспорт|очередь)\b", title, re.IGNORECASE):
        title = _extract_title_fallback(normalized)
    if not title:
        title = _fallback_title(normalized)
    duration = int(data.get("duration_minutes") or _fallback_duration_minutes(normalized) or 60)

    start_str = (data.get("start_datetime") or "").strip()
    end_str = (data.get("end_datetime") or "").strip()
    start_dt: dt.datetime | None = None
    end_dt: dt.datetime | None = None

    if start_str:
        try:
            start_dt = dt.datetime.fromisoformat(start_str)
        except Exception:
            start_dt = None

    if end_str:
        try:
            end_dt = dt.datetime.fromisoformat(end_str)
        except Exception:
            end_dt = None

    if not start_dt:
        start_time, end_time = _extract_time_range(normalized)
        if start_time and end_time:
            base = _parse_start_datetime_fallback(normalized, cfg.tz)
            if base:
                start_dt = _parse_start_datetime_fallback(f"{base.date()} {start_time}", cfg.tz)
                end_dt = _parse_start_datetime_fallback(f"{base.date()} {end_time}", cfg.tz)

        if not start_dt:
            start_dt = _parse_start_datetime_fallback(normalized, cfg.tz)

    # If user explicitly provided a time, ensure we use it (dateparser may default to 'now' time).
    if start_dt:
        explicit_time = _extract_first_time(normalized)
        if explicit_time:
            now = dt.datetime.now(ZoneInfo(cfg.tz))
            # Heuristic: if parsed time is close to 'now', we likely lost the intended time.
            if abs((start_dt - now).total_seconds()) < 5 * 60:
                combined = _combine_date_and_time(start_dt, explicit_time, cfg.tz)
                if combined:
                    start_dt = combined

    if not start_dt:
        await update.message.reply_text(
            "Не смог распознать дату/время. Уточните, пожалуйста (пример: 'в пятницу в 14:00')."
        )
        return

    # If user explicitly provided a time, force it when parsed time differs.
    if start_dt:
        explicit_time = _extract_first_time(normalized)
        if explicit_time:
            combined = _combine_date_and_time(start_dt, explicit_time, cfg.tz)
            if combined and (start_dt.hour != combined.hour or start_dt.minute != combined.minute):
                start_dt = combined

    if start_dt.tzinfo is None:
        start_dt = start_dt.replace(tzinfo=ZoneInfo(cfg.tz))

    if end_dt and end_dt.tzinfo is None:
        end_dt = end_dt.replace(tzinfo=ZoneInfo(cfg.tz))

    if end_dt and end_dt <= start_dt:
        end_dt = None

    try:
        link = cal.create_event(
            title=title,
            start_dt=start_dt,
            duration_minutes=duration,
            end_dt=end_dt,
            description=notes,
        )
    except CalendarServiceError as e:
        await update.message.reply_text(f"Ошибка Google Calendar: {e}")
        return

    msg = f"Добавил: {title}\nНачало: {start_dt.isoformat()}"
    if end_dt:
        msg += f"\nКонец: {end_dt.isoformat()}"
    msg += f"\nДлительность: {duration} мин"
    if notes:
        msg += f"\nЗаметки: {notes}"
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
        local_model=cfg.local_whisper_model,
        language=cfg.whisper_language,
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
