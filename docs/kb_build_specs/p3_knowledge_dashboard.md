# P3 Build Spec — 지식베이스 대시보드 UI

> **상위 설계**: `docs/kb_architecture.md` §7
> **Phase**: P3 (Topics 관리 UI + Conflicts/Gaps 스텁)
> **선행 조건**: P1, P2 완료
> **작업 완료의 의미**: `/knowledge` 페이지에서 Topic CRUD가 브라우저에서 동작한다.

---

## 0. 작업 시작 전 필수 확인

1. `CLAUDE.md`를 읽었는가?
2. `src/dashboard/agents_router.py`를 `view`로 읽어 기존 HTML 페이지 패턴(CSS 변수, nav 구조, fetch 패턴)을 파악했는가?
3. `src/dashboard/knowledge_v2_router.py`를 `view`로 읽어 사용할 API 엔드포인트 목록을 확인했는가?

---

## 1. 범위 (Scope)

### 1.1 포함 (P3에서 하는 것)

- [ ] `src/dashboard/knowledge_v2_router.py`에 HTML 페이지 엔드포인트 추가: `GET /knowledge`
- [ ] Topics 탭: 목록, 검색/필터, 생성 모달, 상세/편집 모달, 삭제
- [ ] Related Topics: 상세 모달에서 연관 주제 목록 표시
- [ ] Conflicts 탭: 스텁 (빈 상태 표시, P4에서 채움)
- [ ] Gaps 탭: 스텁 (빈 상태 표시, P5에서 채움)

### 1.2 제외 (P3에서 하지 않는 것)

- **Graph 탭 구현 금지** — vis.js/cytoscape 연동은 P6 대상.
- **Ingest Log 탭 구현 금지** — P7 대상.
- **Conflicts/Gaps 실제 기능 금지** — 스텁 UI만.
- **신규 API 엔드포인트 추가 금지** — P1에서 만든 API만 사용.

---

## 2. 절대 건드리지 말 것 (Do-not-touch)

- `src/dashboard/knowledge_v2_router.py`의 기존 API 엔드포인트 — HTML 페이지 엔드포인트 1개만 추가.
- 다른 라우터 파일 전체.

---

## 3. 파일 변경 목록

| 경로 | 상태 | 내용 |
|------|------|------|
| `src/dashboard/knowledge_v2_router.py` | 변경 | `GET /knowledge` HTML 엔드포인트 + HTML 문자열 상수 추가 |

---

## 4. 사용할 API 엔드포인트

모두 P1에서 구현된 것. 신규 구현 없음.

| 용도 | Method | Path |
|------|--------|------|
| 목록 조회 | GET | `/api/knowledge/v2/topics?status=&domain=&tag=` |
| 단일 조회 | GET | `/api/knowledge/v2/topics/{id}` |
| 생성 | POST | `/api/knowledge/v2/topics` |
| 수정 | PATCH | `/api/knowledge/v2/topics/{id}` |
| 연관 주제 조회 | GET | `/api/knowledge/v2/topics/{id}/related` |
| 관계 추가 | POST | `/api/knowledge/v2/topics/{id}/relations` |
| 관계 제거 | DELETE | `/api/knowledge/v2/topics/{id}/relations/{related_id}` |

삭제 API는 P1에 없다. **P3에서 DELETE 엔드포인트를 추가한다** (아래 §5 참조).

---

## 5. DELETE 엔드포인트 추가

`knowledge_v2_router.py`에 추가:

```python
@router.delete("/api/knowledge/v2/topics/{topic_id}", status_code=200)
async def delete_v2_topic(request: Request, topic_id: str):
    """
    Topic 삭제.
    - 연관 주제의 역링크도 함께 제거 (remove_relation 경유).
    - 삭제 전 related_topic_ids를 조회하여 양방향 링크 전부 해제 후 문서 삭제.
    """
    user = _require_user(request)
    actor = user["email"]

    doc = await boa_knowledge_collection.find_one({"_id": topic_id}, {"related_topic_ids": 1})
    if not doc:
        raise HTTPException(status_code=404, detail="Topic not found")

    # 양방향 링크 전부 해제
    for rel_id in (doc.get("related_topic_ids") or []):
        await remove_relation(topic_id, rel_id, actor)

    await boa_knowledge_collection.delete_one({"_id": topic_id})
    return {"ok": True}
```

---

## 6. HTML 페이지 엔드포인트

`knowledge_v2_router.py` 하단에 추가:

```python
@router.get("/knowledge", response_class=HTMLResponse)
async def knowledge_page(request: Request):
    user = get_current_user(request)
    if not user:
        return RedirectResponse(url="/login")
    return HTMLResponse(KNOWLEDGE_HTML)
```

`from fastapi.responses import HTMLResponse, RedirectResponse` import 추가 필요.

---

## 7. HTML 명세

### 7.1 전체 구조

기존 대시보드 페이지(agents_router.py)와 동일한 패턴:
- 상단 nav: 로고 + nav 링크 + 로그아웃
- nav 링크: 기존 페이지들과 동일하게 구성 (`/agents`, `/members`, `/spaces`, `/tasks`, `/knowledge` 등)
- **`/knowledge` 링크에 `active` 클래스**

### 7.2 CSS 변수

기존과 동일하게 사용:
```css
:root {
  --bg: #f5f6fa; --surface: #ffffff; --border: #e2e7f0;
  --text: #1e2436; --muted: #7b8499; --accent: #4f46e5;
  --green: #22c55e; --red: #ef4444; --yellow: #f59e0b; --blue: #3b82f6;
}
```

추가 색상:
```css
--internal: #7c3aed;   /* domain: internal 배지 */
--general:  #0891b2;   /* domain: general 배지 */
```

### 7.3 탭 구조

```
[Topics] [Conflicts] [Gaps]
```

- Topics: 기본 활성 탭
- Conflicts: 클릭 시 "아직 감지된 충돌이 없습니다." 빈 상태 표시
- Gaps: 클릭 시 "아직 기록된 지식 갭이 없습니다." 빈 상태 표시

### 7.4 Topics 탭 레이아웃

```
[검색창 300px] [domain 필터 ▼] [status 필터 ▼]    [+ 새 주제]

┌─ 주제 목록 테이블 ────────────────────────────────────────┐
│ TITLE     │ DOMAIN   │ STATUS  │ TAGS        │ UPDATED  │ │
│ ...       │ internal │ active  │ esim cancel │ 04-23    │ │
└────────────────────────────────────────────────────────────┘
```

**테이블 컬럼**:

| 컬럼 | 내용 |
|------|------|
| TITLE | 제목 (클릭 시 상세 모달 오픈) + description을 title 아래 작은 글씨로 |
| DOMAIN | `internal` / `general` / `-` 배지 |
| STATUS | `active` / `draft` / `archived` 배지 |
| TAGS | 태그 최대 3개 표시, 초과 시 `+N` |
| UPDATED | `updated_at` 날짜만 (MM-DD) |
| 액션 | 편집 아이콘, 삭제 아이콘 |

**배지 색상**:
- domain `internal`: `--internal` (보라)
- domain `general`: `--general` (청록)
- status `active`: `--green`
- status `draft`: `--yellow`
- status `archived`: `--muted`

**필터**:
- domain 셀렉트: 전체 / internal / general
- status 셀렉트: 전체 / draft / active / archived
- 검색창: title + description 클라이언트 사이드 필터링 (이미 로드된 데이터 기준)

### 7.5 생성 모달

`+ 새 주제` 버튼 클릭 시 오픈. 오버레이 + 중앙 카드.

**필드**:
```
제목 *          [입력창]
설명 *          [입력창] (80~200자 권장, 글자수 표시)
본문 *          [textarea, 높이 200px]
태그            [입력창] (쉼표 구분, 예: esim, 재발급)
도메인          [셀렉트] internal / general / (없음)
연관 주제       [검색 입력 + 선택 목록]  ← 아래 §7.7 참조
```

**버튼**: [취소] [저장]

저장 시 `POST /api/knowledge/v2/topics` 호출. 성공 시 모달 닫고 목록 새로고침.

### 7.6 상세/편집 모달

Topic 행 클릭 시 오픈. 오버레이 + 넓은 카드 (max-width 720px).

**섹션 구성**:

```
[제목]                              [편집] [삭제] [×]
[도메인 배지] [상태 배지] [태그들]

── 설명 ──────────────────────────────────────────
(description 텍스트)

── 본문 ──────────────────────────────────────────
(content 마크다운 텍스트, pre-wrap)

── 연관 주제 (N개) ───────────────────────────────
[주제명] [주제명] [주제명]   ← 클릭 시 해당 Topic 상세 모달로 교체
```

**[편집] 클릭 시**: 같은 모달이 편집 모드로 전환 (입력창으로 교체).
편집 모드에서 [저장] → `PATCH /api/knowledge/v2/topics/{id}` 호출.

**[삭제] 클릭 시**: `confirm("이 주제를 삭제하시겠습니까? 연관 주제의 역링크도 함께 제거됩니다.")` → `DELETE /api/knowledge/v2/topics/{id}` 호출 → 성공 시 모달 닫고 목록 새로고침.

### 7.7 연관 주제 선택 UI

생성/편집 모달의 연관 주제 입력:
- 텍스트 입력창에 입력 시 기존 로드된 Topic 목록에서 제목 매칭하여 드롭다운 표시
- 선택 시 칩(chip) 형태로 입력창 위에 추가
- 칩의 × 버튼으로 제거
- 저장 시 선택된 topic_id 배열을 `related_topic_ids`로 전송

### 7.8 빈 상태 (empty state)

검색/필터 결과가 없거나 Topic이 0개일 때:
```
📄
지식이 아직 없어요
+ 새 주제 버튼으로 첫 번째 주제를 등록해보세요.
```

---

## 8. JS 구현 가이드라인

- **Vanilla JS만 사용** — 외부 라이브러리 없음 (기존 패턴 준수).
- **상태 관리**: `let topics = []` 전역 배열로 로드된 데이터 관리. 필터링은 클라이언트 사이드.
- **초기 로드**: `DOMContentLoaded`에서 `fetchTopics()` 호출.
- **fetch 패턴**: 기존 agents_router.py의 패턴 그대로. `credentials: 'include'` 포함.
- **에러 처리**: fetch 실패 시 테이블 영역에 "데이터를 불러오지 못했습니다." 표시.
- **모달 열기/닫기**: `modal.style.display = 'flex'` / `'none'`. 오버레이 클릭 시 닫기.
- **글자수 표시**: description 입력창 아래 `(현재글자수/200)` 실시간 표시.

---

## 9. nav 링크 통일

기존 다른 라우터(`agents_router.py`, `members_router.py` 등)의 nav에 `/knowledge` 링크가 없을 수 있다. **이 파일들은 수정하지 않는다.** `/knowledge` 페이지의 nav에만 전체 링크 목록을 정확히 구성한다.

기존 페이지들의 nav 링크 목록을 `agents_router.py`에서 확인하여 동일하게 맞춘다.

---

## 10. 작업 순서

1. **Step 1**: `src/dashboard/agents_router.py`의 nav HTML 구조를 읽어 nav 링크 목록 파악.
2. **Step 2**: `knowledge_v2_router.py`에 DELETE 엔드포인트 추가 (§5).
3. **Step 3**: `knowledge_v2_router.py`에 `GET /knowledge` 엔드포인트 + `KNOWLEDGE_HTML` 상수 작성 (§6, §7).
4. **Step 4**: §11 검증 시나리오 실행 및 결과 보고.

---

## 11. 검증 시나리오

**dev 환경**에서 수행. `schema_version: 2` Topic 3개 이상 사전 생성 필요.

### 시나리오 A: 목록 조회 및 필터

1. `/knowledge` 접속 → Topics 탭에 목록이 표시되는가.
2. domain 필터 `internal` 선택 → internal Topic만 표시되는가.
3. 검색창에 제목 일부 입력 → 해당 Topic만 필터링되는가.

### 시나리오 B: 생성

4. `+ 새 주제` 클릭 → 모달 오픈.
5. 필수 필드 미입력 후 저장 → 저장되지 않고 입력창 강조.
6. 전체 필드 입력 후 저장 → 목록에 새 Topic 추가됨.

### 시나리오 C: 상세 및 편집

7. Topic 행 클릭 → 상세 모달에 title, description, content, 연관 주제 표시.
8. 편집 모드 전환 → 수정 후 저장 → 목록에 반영됨.

### 시나리오 D: 삭제

9. 삭제 아이콘 클릭 → confirm 다이얼로그 → 확인 → 목록에서 제거.
10. 연관 주제가 있는 Topic 삭제 후 → 연관 Topic 상세에서 역링크가 제거되어 있는지 확인.

### 시나리오 E: 스텁 탭

11. Conflicts 탭 클릭 → 빈 상태 메시지 표시.
12. Gaps 탭 클릭 → 빈 상태 메시지 표시.

---

## 12. 완료 보고 형식

```
## P3 구현 완료 보고

### 변경 파일
- src/dashboard/knowledge_v2_router.py (DELETE 엔드포인트 추가 + HTML 엔드포인트 + KNOWLEDGE_HTML)

### 검증 결과
- 시나리오 A: ✅ / 상세
- 시나리오 B: ✅ / 상세
- 시나리오 C: ✅ / 상세
- 시나리오 D: ✅ / 상세
- 시나리오 E: ✅ / 상세

### 이슈/의사결정 사항
- (스펙과 다르게 처리한 부분 및 이유)

### 다음 단계 제안
- (P4 진행 전 확인이 필요한 사항)
```