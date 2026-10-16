# intranet_publish — 인트라넷 게시 공통 파이프라인

```
에이전트 → PostDocument(JSON) → render_post() → Blob 업로드 → SAS(읽기, 1시간) → Device Job → /device-callback → 결과 저장/알림
```

```python
from integrations.intranet_publish import get_publisher, PublishError

result = await get_publisher().publish_post(
    doc,                                   # PostDocument 또는 dict
    assets={"header": png_bytes},          # 글에 쓰인 모든 이미지의 bytes
    recipients=["홍길동"],                  # work 전용, 이름 정확 일치
    thread_id=thread_id,
    notify={"space_id": "...", "thread_name": "..."},  # 선택: 결과를 Google Chat으로
)
```

`publish_post`는 요청이 접수되면 `status="pending"`으로 바로 돌아옵니다. 최종 결과는 콜백으로 오고
`boa_intranet_posts` 컬렉션에 저장됩니다(`get_publisher().get_status(job_id)`).

## 게시판

| board | 화면 | 수신자 |
|---|---|---|
| `genai` | 생성형AI게시판 (전 직원 공개) | 불가 |
| `work` | 업무게시판 | 가능 (최대 20명) |

URL은 device_agent가 가지고 있고 BOA는 이름만 보냅니다.

## 요청 전 거부 (`PublishError.code`, 업로드·전송 없음)

`INVALID_DOCUMENT`, `RENDER_FAILED`, `INVALID_BOARD`, `INVALID_RECIPIENTS`, `MISSING_ASSET`, `INVALID_IMAGE`(png/jpg/jpeg/gif, 내용이 확장자와 일치, 10MB 이하),
`UNSAFE_BODY`, `IN_PROGRESS`(같은 글 게시 중), `NEEDS_MANUAL_CHECK`(이전 시도가 올라갔을 수 있음), `BLOB_FAILED`,
`DEVICE_REQUEST_FAILED`(device_agent 연결 불가), `DEVICE_REQUEST_REJECTED`(4xx)

## 상태와 재시도

| 상태 | 뜻 |
|---|---|
| `pending` | 요청함, 콜백 대기 |
| `retry_wait` | 재시도 대기 (30초, 120초 후 같은 job_id로, SAS 새로 발급) |
| `succeeded` | 게시됨 (`result.post_id`, `post_url`) |
| `failed` | 게시되지 않음. 입력을 고쳐 다시 요청 가능 (같은 post_key 허용) |
| `needs_check` | 올라갔을 수도 있음. 사람이 게시판을 확인. 자동 재시도 없음 |

- 재시도: `LOGIN_REQUIRED`, `IMAGE_UPLOAD_FAILED`, `SUBMIT_FAILED` (최대 2회, 그다음 `failed`)
- `failed`: `BODY_REJECTED`, `PLACEHOLDER_UNRESOLVED`, `EDITOR_NOT_FOUND`, `INVALID_PARAMS`, `RECIPIENT_NOT_FOUND`, `RECIPIENT_AMBIGUOUS`, `RECIPIENT_FAILED`
  (수신자 오류는 `result.recipient_errors = [{"index", "reason"}]`, 이름 대신 순번)
- `needs_check`: `SUBMIT_UNCERTAIN`, `VERIFY_FAILED`, 알 수 없는 오류, 콜백 10분 무응답(`CALLBACK_TIMEOUT`)
- job_id = `intranet-{board}-{post_key}` (결정적). 이미 `succeeded`인 글은 다시 보내지 않습니다. 같은 내용을 일부러 다시 올릴 때는 post_key를 바꿉니다.
- device_agent에 요청이 도착했는지 모를 때(연결 후 타임아웃, 5xx)는 다시 보내지 않고 콜백이나 타임아웃을 기다립니다.

## 환경변수

| 이름 | 기본값 | |
|---|---|---|
| `AZURE_BLOB_CONNECTION_STRING` | (필수) | AccountKey 포함. `AZURE_STORAGE_CONNECTION_STRING`도 인식 |
| `INTRANET_POST_BLOB_CONTAINER` | `wm-device-agent-ai` | |
| `INTRANET_POST_DEVICE_TASK` | `post_intranet_board` | device_agent의 JOB_DISPATCH 이름 |
| `INTRANET_POST_CALLBACK_TIMEOUT_MINUTES` | `10` | |
| `DEVICE_JOB_URL`, `DEVICE_JOB_SECRET`, `MONGODB_URI` | 기존 값 | |

## 남기지 않는 것

SAS URL·연결문자열·Job 키는 로그/DB/예외 메시지에 남기지 않습니다(DB에는 blob 경로만).
수신자 이름은 로그에 남기지 않습니다. 재시도 때 다시 보내야 해서 DB 레코드에만 저장됩니다.

## 수동 확인

```
python scripts/publish_intranet_sample.py                                   # 렌더만
python scripts/publish_intranet_sample.py --board work --recipients "본인이름" --confirm --wait
```
콜백은 device_agent의 `BOA_CALLBACK_URL` 서버로 가므로, 그 서버에 이 코드가 배포돼 있어야 결과가 기록됩니다.
