import os

from dotenv import load_dotenv


def load_config() -> None:
    load_dotenv()


def getenv_required(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


class Config:
    def __init__(self) -> None:
        load_config()

        self.telegram_token = getenv_required("TELEGRAM_TOKEN")

        self.openai_api_key = getenv_required("OPENAI_API_KEY")
        self.openai_model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

        self.whisper_provider = os.getenv("WHISPER_PROVIDER", "openai")
        self.openai_whisper_model = os.getenv("OPENAI_WHISPER_MODEL", "whisper-1")
        self.local_whisper_model = os.getenv("LOCAL_WHISPER_MODEL", "base")
        self.whisper_language = os.getenv("WHISPER_LANGUAGE", "ru")

        self.google_client_id = getenv_required("GOOGLE_CLIENT_ID")
        self.google_client_secret = getenv_required("GOOGLE_CLIENT_SECRET")
        self.google_project_id = os.getenv("GOOGLE_PROJECT_ID", "")
        self.google_redirect_uri = os.getenv("GOOGLE_REDIRECT_URI", "http://localhost")
        self.google_oauth_client_type = os.getenv("GOOGLE_OAUTH_CLIENT_TYPE", "installed")
        self.google_oauth_local_server_port = int(os.getenv("GOOGLE_OAUTH_LOCAL_SERVER_PORT", "0"))
        self.google_calendar_id = os.getenv("GOOGLE_CALENDAR_ID", "primary")

        self.tz = os.getenv("TZ", "Europe/Kyiv")
