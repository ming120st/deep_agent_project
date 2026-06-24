'''
import asyncio
import json
import os
import threading
from typing import Any, Sequence

from google.oauth2 import service_account
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build


SHEETS_SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]


class GoogleSheetsClient:
    """Google Sheets API 호출을 담당하는 공용 클라이언트.

    인증 방식 두 가지를 지원한다:
      1. 서비스 계정 (GOOGLE_SERVICE_ACCOUNT_KEY)
      2. OAuth 사용자 인증 (GOOGLE_CLIENT_ID + GOOGLE_CLIENT_SECRET + GOOGLE_REFRESH_TOKEN)
         → 서비스 계정 공유가 불가한 워크스페이스 정책 환경에서 사용
    """

    def __init__(
        self,
        *,
        credentials: Any,
        default_spreadsheet_id: str | None = None,
        default_sheet_name: str | None = None,
    ) -> None:
        self._service = build("sheets", "v4", credentials=credentials, cache_discovery=False)
        self._lock = threading.RLock()
        self.default_spreadsheet_id = default_spreadsheet_id
        self.default_sheet_name = default_sheet_name

    @classmethod
    def from_env(cls) -> "GoogleSheetsClient":
        """환경변수에서 설정을 읽어 GoogleSheetsClient를 생성한다.

        인증 방식 우선순위:
          1. 서비스 계정: GOOGLE_SERVICE_ACCOUNT_KEY (JSON 문자열)
          2. OAuth:       GOOGLE_CLIENT_ID + GOOGLE_CLIENT_SECRET + GOOGLE_REFRESH_TOKEN

        공통 선택:
          - GOOGLE_SHEETS_DEFAULT_SPREADSHEET_ID
          - GOOGLE_SHEETS_DEFAULT_SHEET_NAME
        """
        credentials = cls._resolve_credentials()

        return cls(
            credentials=credentials,
            default_spreadsheet_id=os.getenv("GOOGLE_SHEETS_DEFAULT_SPREADSHEET_ID"),
            default_sheet_name=os.getenv("GOOGLE_SHEETS_DEFAULT_SHEET_NAME"),
        )

    @staticmethod
    def _resolve_credentials() -> Any:
        """환경변수를 보고 적절한 인증 객체를 반환한다."""

        # 1순위: 서비스 계정
        raw_key = os.getenv("GOOGLE_SERVICE_ACCOUNT_KEY")
        if raw_key:
            try:
                info = json.loads(raw_key)
            except json.JSONDecodeError as exc:
                raise ValueError("GOOGLE_SERVICE_ACCOUNT_KEY 값이 유효한 JSON이 아닙니다.") from exc
            return service_account.Credentials.from_service_account_info(
                info, scopes=SHEETS_SCOPES
            )

        # 2순위: OAuth refresh_token
        client_id = os.getenv("GOOGLE_CLIENT_ID")
        client_secret = os.getenv("GOOGLE_CLIENT_SECRET")
        refresh_token = os.getenv("GOOGLE_REFRESH_TOKEN")

        if client_id and client_secret and refresh_token:
            creds = Credentials(
                token=None,
                refresh_token=refresh_token,
                token_uri="https://oauth2.googleapis.com/token",
                client_id=client_id,
                client_secret=client_secret,
                scopes=SHEETS_SCOPES,
            )
            # 최초 호출 시 access_token 자동 갱신
            creds.refresh(Request())
            return creds

        raise ValueError(
            "Google Sheets 인증 환경변수가 없습니다.\n"
            "서비스 계정: GOOGLE_SERVICE_ACCOUNT_KEY\n"
            "OAuth:       GOOGLE_CLIENT_ID + GOOGLE_CLIENT_SECRET + GOOGLE_REFRESH_TOKEN"
        )

    def _spreadsheet_id(self, spreadsheet_id: str | None) -> str:
        resolved = spreadsheet_id or self.default_spreadsheet_id
        if not resolved:
            raise ValueError("spreadsheet_id is required.")
        return resolved

    def _range_name(self, range_name: str) -> str:
        if "!" in range_name or not self.default_sheet_name:
            return range_name
        sheet_name = self.default_sheet_name.replace("'", "''")
        return f"'{sheet_name}'!{range_name}"

    def get_metadata(self, spreadsheet_id: str | None = None) -> dict[str, Any]:
        """스프레드시트 메타데이터(시트 목록, 속성 등)를 조회한다."""
        with self._lock:
            return (
                self._service.spreadsheets()
                .get(spreadsheetId=self._spreadsheet_id(spreadsheet_id))
                .execute()
            )

    def get_values(
        self,
        range_name: str,
        spreadsheet_id: str | None = None,
        *,
        major_dimension: str = "ROWS",
    ) -> list[list[Any]]:
        """지정한 범위의 값을 조회한다."""
        with self._lock:
            result = (
                self._service.spreadsheets()
                .values()
                .get(
                    spreadsheetId=self._spreadsheet_id(spreadsheet_id),
                    range=self._range_name(range_name),
                    majorDimension=major_dimension,
                )
                .execute()
            )
        return result.get("values", [])

    def batch_get_values(
        self,
        ranges: Sequence[str],
        spreadsheet_id: str | None = None,
        *,
        major_dimension: str = "ROWS",
    ) -> dict[str, list[list[Any]]]:
        """여러 범위를 한 번에 조회해 {range: values} 형태로 반환한다."""
        with self._lock:
            result = (
                self._service.spreadsheets()
                .values()
                .batchGet(
                    spreadsheetId=self._spreadsheet_id(spreadsheet_id),
                    ranges=[self._range_name(r) for r in ranges],
                    majorDimension=major_dimension,
                )
                .execute()
            )
        return {
            item.get("range", ""): item.get("values", [])
            for item in result.get("valueRanges", [])
        }

    def update_values(
        self,
        range_name: str,
        values: Sequence[Sequence[Any]],
        spreadsheet_id: str | None = None,
        *,
        value_input_option: str = "USER_ENTERED",
    ) -> dict[str, Any]:
        """지정한 범위를 전달한 값으로 덮어쓴다."""
        with self._lock:
            return (
                self._service.spreadsheets()
                .values()
                .update(
                    spreadsheetId=self._spreadsheet_id(spreadsheet_id),
                    range=self._range_name(range_name),
                    valueInputOption=value_input_option,
                    body={"values": [list(row) for row in values]},
                )
                .execute()
            )

    def append_values(
        self,
        range_name: str,
        values: Sequence[Sequence[Any]],
        spreadsheet_id: str | None = None,
        *,
        value_input_option: str = "USER_ENTERED",
        insert_data_option: str = "INSERT_ROWS",
    ) -> dict[str, Any]:
        """지정한 범위 기준으로 행 데이터를 추가(append)한다."""
        with self._lock:
            return (
                self._service.spreadsheets()
                .values()
                .append(
                    spreadsheetId=self._spreadsheet_id(spreadsheet_id),
                    range=self._range_name(range_name),
                    valueInputOption=value_input_option,
                    insertDataOption=insert_data_option,
                    body={"values": [list(row) for row in values]},
                )
                .execute()
            )

    def clear_values(
        self,
        range_name: str,
        spreadsheet_id: str | None = None,
    ) -> dict[str, Any]:
        """지정한 범위의 값을 비운다."""
        with self._lock:
            return (
                self._service.spreadsheets()
                .values()
                .clear(
                    spreadsheetId=self._spreadsheet_id(spreadsheet_id),
                    range=self._range_name(range_name),
                    body={},
                )
                .execute()
            )

    async def aget_metadata(self, spreadsheet_id: str | None = None) -> dict[str, Any]:
        return await asyncio.to_thread(self.get_metadata, spreadsheet_id)

    async def aget_values(
        self,
        range_name: str,
        spreadsheet_id: str | None = None,
        *,
        major_dimension: str = "ROWS",
    ) -> list[list[Any]]:
        return await asyncio.to_thread(
            self.get_values, range_name, spreadsheet_id, major_dimension=major_dimension
        )

    async def abatch_get_values(
        self,
        ranges: Sequence[str],
        spreadsheet_id: str | None = None,
        *,
        major_dimension: str = "ROWS",
    ) -> dict[str, list[list[Any]]]:
        return await asyncio.to_thread(
            self.batch_get_values, ranges, spreadsheet_id, major_dimension=major_dimension
        )

    async def aupdate_values(
        self,
        range_name: str,
        values: Sequence[Sequence[Any]],
        spreadsheet_id: str | None = None,
        *,
        value_input_option: str = "USER_ENTERED",
    ) -> dict[str, Any]:
        return await asyncio.to_thread(
            self.update_values, range_name, values, spreadsheet_id,
            value_input_option=value_input_option,
        )

    async def aappend_values(
        self,
        range_name: str,
        values: Sequence[Sequence[Any]],
        spreadsheet_id: str | None = None,
        *,
        value_input_option: str = "USER_ENTERED",
        insert_data_option: str = "INSERT_ROWS",
    ) -> dict[str, Any]:
        return await asyncio.to_thread(
            self.append_values, range_name, values, spreadsheet_id,
            value_input_option=value_input_option,
            insert_data_option=insert_data_option,
        )

    async def aclear_values(
        self,
        range_name: str,
        spreadsheet_id: str | None = None,
    ) -> dict[str, Any]:
        return await asyncio.to_thread(self.clear_values, range_name, spreadsheet_id)


google_sheets_client: GoogleSheetsClient | None = None


def init_google_sheets_client() -> GoogleSheetsClient:
    """프로세스 전역 Google Sheets 싱글턴을 초기화하고 반환한다."""
    global google_sheets_client
    if google_sheets_client is None:
        google_sheets_client = GoogleSheetsClient.from_env()
    return google_sheets_client


def get_google_sheets_client() -> GoogleSheetsClient:
    """초기화된 싱글턴을 반환하고, 없으면 지연 초기화한다."""
    return init_google_sheets_client()
'''