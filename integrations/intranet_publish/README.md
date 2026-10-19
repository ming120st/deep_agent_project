# intranet_publish — 인트라넷 게시 공통 파이프라인

```
(이미지 업로드 Tool → Blob) → HTML 작성 에이전트 → 게시 Tool: 입력 검사 + Blob 이미지 확인 → SAS(읽기, 1시간) → Device Job → /device-callback → 결과 저장/알림
```

```python
from integrations.intranet_publish import get_publisher, PublishError

result = await get_publisher().publish_post(
    {                                      # IntranetPost 또는 dict (post.py)
        "post_key": "ai-weekly-2026-W42",  # 글 고유 ID (job_id의 일부)
        # 이미지는 Blob intranet-board/{post_key}/ 에 저장
        "board": "genai", "title": "...",
        "body_html": "<div>... {{img:header}} ...</div>",   # AI가 작성한 HTML
        "images": ["header.png"],
    },
    recipients=["홍길동"],                  # 업무게시판 전용, 이름 정확 일치
    thread_id=thread_id,
    notify={"space_id": "...", "thread_name": "..."},  # 선택: 결과를 Google Chat으로
)
```

`publish_post`는 요청이 접수되면 `status="pending"`으로 바로 돌아옵니다. 최종 결과는 콜백으로 오고
`boa_intranet_posts` 컬렉션에 저장됩니다(`get_publisher().get_status(job_id)`).

## 본문 HTML

이 모듈은 HTML을 만들거나 고치지 않고 **검사만** 합니다. 작성 규칙은 `agents/common/intranet_html_agent/skills/intranet-html/SKILL.md`에 있습니다.
본문에 쓸 수 있는 이미지는 `{{img:파일명}}` 자리표시자뿐이고, 파일명은 `images`에 적습니다. 적기만 하고 안 쓴 이미지는 그대로 전달되며 `warnings`로 알려줍니다.

## 이미지

게시 함수는 이미지를 업로드하지 않습니다. 이미지는 **먼저** Blob의 `intranet-board/{post_key}/{filename}`에 올라가 있어야 하고(에이전트는 `upload_intranet_image` Tool, 주소를 내려받아 메모리에서 바로 업로드), 게시할 때 파일이 있는지/형식/10MB를 확인합니다. 글마다 자기 `post_key` 폴더의 파일만 참조할 수 있습니다.
내려받기는 http(s) 80/443만, 내부/비공개 주소와 리다이렉트 대상까지 차단합니다. `MISSING_ASSET`은 "Blob에 없음"입니다.

## 게시판

| board | 화면 | 수신자 |
|---|---|---|
| `genai` | 생성형AI게시판 (전 직원 공개) | 불가 |
| `work` | 업무게시판 | 가능 (최대 20명) |

URL은 device_agent가 가지고 있고 BOA는 이름만 보냅니다.

## 요청 전 거부 (`PublishError.code`, 업로드·전송 없음)

`INVALID_DOCUMENT`(필드 규격, 본문 300,000자 초과, `<img>`가 `{{img:파일명}}`이 아님, `images`에 없는 파일명), `INVALID_BOARD`, `INVALID_RECIPIENTS`, `MISSING_ASSET`, `INVALID_IMAGE`(png/jpg/jpeg/gif, 내용이 확장자와 일치, 10MB 이하), `INVALID_IMAGE_URL`/`IMAGE_DOWNLOAD_FAILED`(업로드 Tool),
`UNSAFE_BODY`(script/style/on*=/javascript: 등, device_agent와 같은 규칙), `IN_PROGRESS`(같은 글 게시 중), `NEEDS_MANUAL_CHECK`(이전 시도가 올라갔을 수 있음), `BLOB_FAILED`,
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
python scripts/publish_intranet_sample.py                                   # 검사만 (--html 파일 지정 가능)
python scripts/publish_intranet_sample.py --board work --recipients "본인이름" --confirm --wait
```
콜백은 device_agent의 `BOA_CALLBACK_URL` 서버로 가므로, 그 서버에 이 코드가 배포돼 있어야 결과가 기록됩니다.
