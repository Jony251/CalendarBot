# CalendarBot: a Telegram secretary for Google Calendar

A Python Telegram bot that turns a text or voice message into a Google Calendar event. Voice notes are transcribed with Whisper (through the OpenAI API, or a local Whisper model), then an OpenAI model extracts the title, start, end or duration and notes as JSON. If the model is unavailable or returns something unusable, a rule-based fallback takes over: `dateparser` plus regular expressions for Russian time expressions ("в пять вечера", "в 5 часов вечера", ranges like "12:00 до 14:00"), duration phrases and title clean-up. A strict multi-line format (title / date / time / end or duration / notes, one field per line) bypasses the AI entirely. The bot replies with the created event and a link to it. Built for Russian-language input.

**Stack:** Python 3.10+ · python-telegram-bot 21 (async) · OpenAI API (chat model for extraction, `whisper-1` for speech) or local `openai-whisper` · Google Calendar API with OAuth 2.0 (installed-app flow, token cached in `token.json`) · dateparser · python-dotenv.

**Files:** `bot.py` (handlers and parsing pipeline) · `speech_service.py` (OpenAI or local Whisper) · `calendar_service.py` (OAuth and event creation) · `config.py` (settings from `.env`, fails fast on missing required values).

## Run locally

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env               # Windows: copy .env.example .env
python bot.py
```

On the first event the bot opens the Google OAuth consent flow in a browser and stores the resulting token in `token.json` (git-ignored).

**Required in `.env`:** `TELEGRAM_TOKEN` (from BotFather), `OPENAI_API_KEY`, `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` (an OAuth client of type *Desktop app* with the Google Calendar API enabled).
**Optional:** `OPENAI_MODEL`, `WHISPER_PROVIDER` (`openai` or `local`), `OPENAI_WHISPER_MODEL`, `LOCAL_WHISPER_MODEL`, `WHISPER_LANGUAGE`, `GOOGLE_CALENDAR_ID`, `GOOGLE_PROJECT_ID`, `GOOGLE_REDIRECT_URI`, `GOOGLE_OAUTH_CLIENT_TYPE`, `GOOGLE_OAUTH_LOCAL_SERVER_PORT`, `TZ`.

For `WHISPER_PROVIDER=local`, also install `openai-whisper` and make sure `ffmpeg` is on your `PATH`. If Google returns `403 access_denied`, add your account as a test user on the OAuth consent screen.

**Example messages:** "запиши меня к зубному на завтра в 12:00" · "созвон с Петром в пятницу в 15:30 на 45 минут".

## Author

Evgeny Nemchenko, full-stack developer: [bluecat.cc](https://bluecat.cc) · [LinkedIn](https://www.linkedin.com/in/evgeny-nemchenko) · [nevgeny90@gmail.com](mailto:nevgeny90@gmail.com) · [GitHub @Jony251](https://github.com/Jony251)
