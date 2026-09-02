from __future__ import annotations

from dataclasses import dataclass

from integrations.device_job.client import (
    DeviceJobClient,
)
from integrations.device_job.schemas import (
    DeviceJobRequest,
)


@dataclass(slots=True)
class WifiLineChangeSubmission:
    job_id: str


async def submit_wifi_line_change_job(
    *,
    device_number: str,
    country_code: str,
    expiry_date: str,
    thread_id: str,
    user_name: str | None = None,
) -> WifiLineChangeSubmission:

    request = DeviceJobRequest(
        thread_id=thread_id,
        task="wifi_sim_change",
        parameters={
            "imei": device_number,
            "country_code": country_code,
            "expiry_date": expiry_date,
        },
        context={
            "user_name": user_name,
        },
    )

    client = DeviceJobClient()

    await client.submit(
        request
    )

    return WifiLineChangeSubmission(
        job_id=request.job_id,
    )
