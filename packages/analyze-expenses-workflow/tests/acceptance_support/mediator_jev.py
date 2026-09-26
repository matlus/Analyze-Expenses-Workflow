import json
import secrets
from dataclasses import dataclass
from typing import ClassVar, cast, override

import httpx2


class JevTransportCloseError(OSError):
    pass


@dataclass(frozen=True, slots=True)
class CapturedJevRequest:
    method: str
    path: str
    jev_request_field_value_by_name: dict[str, object]


class TestMediatorJev:
    __test__: ClassVar[bool] = False

    def __init__(self, scripted_status_code: int | None = None, close_failures_before_success: int = 0) -> None:
        self.scripted_status_code: int | None = scripted_status_code
        self.close_failures_before_success: int = close_failures_before_success
        self.captured_jev_requests: list[CapturedJevRequest] = []
        self.captured_close_attempt_count: int = 0

    def create_http_transport(self) -> httpx2.AsyncBaseTransport:
        return JevTransportSpy(self)


class JevTransportSpy(httpx2.AsyncBaseTransport):
    def __init__(self, test_mediator_jev: TestMediatorJev) -> None:
        self._test_mediator_jev: TestMediatorJev = test_mediator_jev
        self._async_client: httpx2.AsyncClient | None = None

    @override
    async def handle_async_request(self, request: httpx2.Request) -> httpx2.Response:
        jev_request_field_value_by_name: dict[str, object] = cast("dict[str, object]", json.loads(request.content))
        self._test_mediator_jev.captured_jev_requests.append(CapturedJevRequest(request.method, request.url.path, jev_request_field_value_by_name))
        if self._test_mediator_jev.scripted_status_code is not None:
            return httpx2.Response(
                status_code=self._test_mediator_jev.scripted_status_code,
                json={"error": {"message": secrets.token_hex(8)}},
            )
        if self._async_client is None:
            self._async_client = httpx2.AsyncClient()
        return await self._async_client.send(request)

    @override
    async def aclose(self) -> None:
        self._test_mediator_jev.captured_close_attempt_count += 1
        if self._test_mediator_jev.close_failures_before_success > 0:
            self._test_mediator_jev.close_failures_before_success -= 1
            raise JevTransportCloseError("Jev transport close failed")
        if self._async_client is not None:
            await self._async_client.aclose()
            self._async_client = None
