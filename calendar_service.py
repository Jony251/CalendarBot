import datetime as dt
import os
from typing import Any, Dict, Optional

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError


class CalendarServiceError(RuntimeError):
    pass


class CalendarService:
    SCOPES = ["https://www.googleapis.com/auth/calendar.events"]

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        project_id: str,
        redirect_uri: str,
        client_type: str,
        local_server_port: int,
        calendar_id: str,
        token_path: str = "token.json",
    ) -> None:
        self.client_id = client_id
        self.client_secret = client_secret
        self.project_id = project_id
        self.redirect_uri = redirect_uri
        self.client_type = (client_type or "installed").lower().strip()
        self.local_server_port = int(local_server_port)
        self.calendar_id = calendar_id
        self.token_path = token_path

    def _client_config(self) -> Dict[str, Any]:
        key = "web" if self.client_type == "web" else "installed"
        return {
            key: {
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "project_id": self.project_id or None,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
                "redirect_uris": [self.redirect_uri],
            }
        }

    def get_service(self):
        creds: Optional[Credentials] = None
        if os.path.exists(self.token_path):
            creds = Credentials.from_authorized_user_file(self.token_path, self.SCOPES)

        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
            except Exception:
                creds = None

        if not creds or not creds.valid:
            flow = InstalledAppFlow.from_client_config(self._client_config(), self.SCOPES)
            creds = flow.run_local_server(port=self.local_server_port)
            with open(self.token_path, "w", encoding="utf-8") as f:
                f.write(creds.to_json())

        return build("calendar", "v3", credentials=creds)

    def create_event(
        self,
        title: str,
        start_dt: dt.datetime,
        duration_minutes: int,
        end_dt: Optional[dt.datetime] = None,
        description: str = "",
    ) -> str:
        if end_dt is None:
            end_dt = start_dt + dt.timedelta(minutes=duration_minutes)

        body = {
            "summary": title,
            "start": {"dateTime": start_dt.isoformat()},
            "end": {"dateTime": end_dt.isoformat()},
        }

        if description:
            body["description"] = description

        service = self.get_service()
        try:
            created = (
                service.events()
                .insert(calendarId=self.calendar_id, body=body)
                .execute()
            )
        except HttpError as e:
            raise CalendarServiceError(f"Google Calendar API error: {e}") from e
        except Exception as e:
            raise CalendarServiceError(f"Failed to create event: {e}") from e

        link = created.get("htmlLink")
        return link or ""
