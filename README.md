# CalendarBot

Telegram-бот (Python) — личный секретарь: принимает текст или голос, извлекает данные встречи и добавляет событие в Google Calendar.

## Возможности

- Текстовые сообщения -> создание события в Google Calendar
- Голосовые сообщения (audio/voice) -> распознавание речи -> создание события
- Извлечение структуры встречи (title / date+time / duration)
- OAuth авторизация Google Calendar:
  - первый раз создаётся `token.json`
  - далее используется сохранённый `token.json`

## Требования

- Windows/macOS/Linux
- Python 3.10+ (проект тестировался на Windows)
- Аккаунт Telegram (BotFather токен)
- Google Cloud Project с включенным Google Calendar API
- OpenAI API key (используется для извлечения структуры встречи из текста)

## Установка

```powershell
python -m venv .venv
.\.venv\Scripts\activate
python -m pip install -r requirements.txt
```

## Настройка окружения (.env)

1. Скопируйте пример:

```powershell
copy .env.example .env
```

2. Заполните `.env` значениями:

- `TELEGRAM_TOKEN` — токен вашего бота
- `OPENAI_API_KEY` — ключ OpenAI
- `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` — OAuth Client (Desktop)
- `GOOGLE_CALENDAR_ID` — календарь (`primary` по умолчанию)
- `TZ` — часовой пояс, например `Europe/Kyiv`

### Важно про секреты

- Все ключи/секреты хранятся **только** в `.env`
- `token.json` не коммитится (в `.gitignore`)

## Google Cloud настройка

1. Включите API:
   - Google Cloud Console -> APIs & Services -> Library -> **Google Calendar API** -> Enable
2. OAuth consent screen:
   - Если Publishing status = **Testing**, добавьте свой email в **Test users**
3. Credentials:
   - Создайте OAuth Client ID типа **Desktop app**
   - Скопируйте `client_id` и `client_secret` в `.env`

## Запуск

```powershell
.\.venv\Scripts\python.exe bot.py
```

При первой попытке создать событие откроется OAuth-авторизация. После подтверждения будет создан `token.json`.

## Использование

### Обычный текст

Примеры сообщений:

- `запиши меня к зубному на завтра в 12:00`
- `созвон с Петром в пятницу в 15:30 на 45 минут`

Бот старается:

- корректно распознать дату/время
- если длительность не указана — ставит 60 минут
- улучшать заголовок (например, про стоматолога -> `Зубной врач`)

### Явный формат (точное задание полей)

Можно отправить в нескольких строках:

```text
титул это - Зубной врач
дата - 13.02.26
время - 12-00
протяженность - 1 час
```

### Голосовые сообщения

Голосовые сообщения распознаются и далее обрабатываются как обычный текст.

Выбор провайдера задаётся в `.env`:

- `WHISPER_PROVIDER=openai` — распознавание через OpenAI (нужна оплаченная квота)
- `WHISPER_PROVIDER=local` — локальный Whisper

#### Локальный Whisper и ffmpeg (Windows)

Для `WHISPER_PROVIDER=local` требуется установленный `ffmpeg` в `PATH`.

Проверка:

```powershell
ffmpeg -version
```

## Файлы проекта

- `bot.py` — Telegram bot (text + voice)
- `speech_service.py` — распознавание речи (OpenAI или local Whisper)
- `calendar_service.py` — Google Calendar API + OAuth (`token.json`)
- `config.py` — загрузка конфигурации из `.env`

## Troubleshooting

- **Бот не отвечает**
  - проверьте, что процесс `bot.py` запущен
  - проверьте `TELEGRAM_TOKEN`
- **Google OAuth 403 access_denied**
  - проверьте OAuth consent screen (Testing -> Test users)
  - убедитесь, что клиент типа Desktop
- **Local Whisper WinError 2**
  - обычно нет `ffmpeg` в `PATH`

## Лицензия

Добавьте лицензию при необходимости.
