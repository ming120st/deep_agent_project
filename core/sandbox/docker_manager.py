from __future__ import annotations

import docker
from docker.models.containers import (
    Container,
)

from core.sandbox.config import (
    DockerSandboxConfig,
)


class DockerSandboxManager:
    def __init__(
        self,
        config: DockerSandboxConfig,
    ):
        self.config = config

        self.client = docker.from_env()

        self.container: Container | None = None

    def start(self) -> Container:
        if self.container is not None:
            return self.container

        self.container = (
            self.client.containers.run(
                image=self.config.image,
                command="sleep infinity",
                detach=True,
                working_dir=(
                    self.config.workspace
                ),
                network_disabled=(
                    self.config.network_disabled
                ),
                mem_limit=(
                    self.config.memory_limit
                ),
                nano_cpus=(
                    self.config.cpu_count
                    * 1_000_000_000
                ),
                security_opt=[
                    "no-new-privileges:true",
                ],
            )
        )

        return self.container

    def stop(self) -> None:
        if self.container is None:
            return

        try:
            self.container.remove(
                force=True,
            )
        finally:
            self.container = None