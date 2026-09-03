from langchain.tools import ToolRuntime
from langchain_core.tools import tool

from integrations.device_job.client import DeviceJobClient, DeviceJobRequest


@tool
async def submit_wifi_line_change(
    device_number: str,
    country_code: str,
    expiry_date: str,
    runtime: ToolRuntime,
) -> str:
    """ 
    WiFi 단말기의 회선을 변경한다.  
    사용자가 실제 회선 변경을 요청한 경우 사용한다. 
    device_number, country_code, expiry_date가 모두 확보된 후 호출한다.     
    """  

    state = runtime.state

    thread_id = state.get("session_id")
    user_name = state.get("user_name")

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
    await client.submit(request)

    return request.job_id