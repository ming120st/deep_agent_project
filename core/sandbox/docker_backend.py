from __future__ import annotations

import io
import tarfile
import uuid

from deepagents.backends.sandbox import (
    BaseSandbox,
)
from deepagents.backends.protocol import (
    ExecuteResponse,
    FileUploadResponse,
    FileDownloadResponse,
)

from core.sandbox.config import (
    DockerSandboxConfig,
)
from core.sandbox.docker_manager import (
    DockerSandboxManager,
)


class DockerSandboxBackend(
    BaseSandbox,
):
    def __init__(
        self,
        config: DockerSandboxConfig | None = None,
    ):
        self.config = (
            config
            or DockerSandboxConfig()
        )

        self.manager = (
            DockerSandboxManager(
                self.config
            )
        )

        self._id = str(
            uuid.uuid4()
        )

        self.manager.start()

    @property
    def id(self) -> str:
        return self._id

    def execute(
        self,
        command: str,
        *,
        timeout: int | None = None,
    ) -> ExecuteResponse:
        """
        Docker Sandbox 내부에서 shell 명령을 실행한다.

        Python 코드 실행, 데이터 가공 및 계산 등
        격리된 Sandbox 환경에서 명령 실행이 필요한 경우 사용한다.

        Args:
            command:
                Sandbox에서 실행할 shell 명령.
            timeout:
                명령 실행 제한 시간(초).

        Returns:
            명령 실행 결과와 exit code를 포함한 ExecuteResponse.
        """

        container = (
            self.manager.start()
        )

        timeout_seconds = (
            timeout
            if timeout is not None
            else self.config.default_timeout
        )

        result = container.exec_run(
            [
                "timeout",
                f"{timeout_seconds}s",
                "/bin/bash",
                "-lc",
                command,
            ],
            workdir=(
                self.config.workspace
            ),
        )

        output = (
            result.output.decode(
                "utf-8",
                errors="replace",
            )
        )

        if result.exit_code == 124 and not output:
            output = (
                "Command timed out after "
                f"{timeout_seconds} seconds."
            )

        return ExecuteResponse(
            output=output,
            exit_code=result.exit_code,
            truncated=False,
        )

    def upload_files(
        self,
        files: list[tuple[str, bytes]],
    ) -> list[FileUploadResponse]:
        container = (
            self.manager.start()
        )

        responses: list[
            FileUploadResponse
        ] = []

        for path, content in files:
            try:
                normalized_path = (
                    path.lstrip("/")
                )

                archive_stream = io.BytesIO()

                with tarfile.open(
                    fileobj=archive_stream,
                    mode="w",
                ) as tar:
                    tar_info = (
                        tarfile.TarInfo(
                            name=normalized_path
                        )
                    )

                    tar_info.size = len(
                        content
                    )

                    tar.addfile(
                        tar_info,
                        io.BytesIO(content),
                    )

                archive_stream.seek(0)

                container.put_archive(
                    path="/",
                    data=archive_stream.read(),
                )

                responses.append(
                    FileUploadResponse(
                        path=path,
                        error=None,
                    )
                )

            except Exception as exc:
                responses.append(
                    FileUploadResponse(
                        path=path,
                        error=str(exc),
                    )
                )

        return responses

    def download_files(
        self,
        paths: list[str],
    ) -> list[FileDownloadResponse]:
        container = (
            self.manager.start()
        )

        responses: list[
            FileDownloadResponse
        ] = []

        for path in paths:
            try:
                stream, _ = (
                    container.get_archive(
                        path
                    )
                )

                archive_bytes = b"".join(
                    stream
                )

                archive_stream = (
                    io.BytesIO(
                        archive_bytes
                    )
                )

                with tarfile.open(
                    fileobj=archive_stream,
                    mode="r:*",
                ) as tar:
                    members = (
                        tar.getmembers()
                    )

                    if not members:
                        raise FileNotFoundError(
                            path
                        )

                    extracted = (
                        tar.extractfile(
                            members[0]
                        )
                    )

                    if extracted is None:
                        raise IsADirectoryError(
                            path
                        )

                    content = (
                        extracted.read()
                    )

                responses.append(
                    FileDownloadResponse(
                        path=path,
                        content=content,
                        error=None,
                    )
                )

            except Exception as exc:
                responses.append(
                    FileDownloadResponse(
                        path=path,
                        content=None,
                        error=str(exc),
                    )
                )

        return responses
