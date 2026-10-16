# intranet_post — 인트라넷 게시글 렌더러

에이전트가 만든 **게시 문서(JSON)** 를 인트라넷 보기 화면에서 깨지지 않는 **HTML** 로 바꿉니다.
순수 파이썬(LLM 없음)이고 BOA 어느 에이전트든 같은 파이프라인을 씁니다.

```
에이전트 → PostDocument(JSON) → render_post() → body_html + images[] → Blob 업로드 → Device Job
```

## 사용

```python
from integrations.intranet_post import render_post

rendered = render_post(doc)            # dict 또는 PostDocument. 검증 실패 시 pydantic.ValidationError
rendered.body_html                     # device job의 parameters.body_html
rendered.images                        # [ImageRef(key, filename, alt)] — 업로드해야 할 이미지 목록
rendered.blob_path("header.png")       # intranet-board/{series}/{period}/header.png
rendered.warnings                      # 선언만 하고 안 쓴 asset 등
```

Blob 업로드, Device Job 요청, 콜백 처리는 `integrations/intranet_publish`가 합니다(`get_publisher().publish_post(doc, assets=...)`).

## 게시 문서 규격 (v1)

최상위: `schema_version, post_key, series, period, board, title, theme, assets[], blocks[]`
전체 규격은 `python -m integrations.intranet_post schema --out integrations/intranet_post/schema/post_document.schema.json`
(LLM structured output에 `PostDocument`를 그대로 넘기면 형식이 보장됩니다.)

| 블록 | 필드 |
|---|---|
| `heading` | level(2/3), text |
| `paragraph` | text — 인라인: `**굵게**`, `[라벨](https://url)`, 줄바꿈 |
| `bullets` | items[], ordered |
| `callout` | tone(info/good/warn/danger/neutral), title?, text |
| `image` | key(assets 참조), alt?, caption?, width?(px) |
| `link` | text, url |
| `news_card` | title, url, source, published_date?, summary, impact?, tags[≤5], image_key? |
| `table` | caption?, columns[{label, align, width}], rows[][], style{striped, header_tone, first_col_bold, compact}, footnote? |
| `divider` | — |

표 셀: 문자열 / 숫자 / `{text, tone?, bold?, url?}`.
**숫자는 천 단위 구분(2026 → 2,026)으로 표시되니 연도·ID는 문자열로 넣으세요.**

제한: 이미지는 png/jpg/jpeg/gif만(svg 불가), 블록 200개, 표 100행×10열, 본문 30만자.
URL은 http(s)만, 색상은 항상 의미 이름(`tone`, `header_tone`)과 `theme`으로만 지정합니다(임의 CSS 불가).

## 모양을 바꾸려면

| 하고 싶은 것 | 방법 |
|---|---|
| 색/분위기 | `themes.py`에 Theme 추가 → 문서의 `"theme"` 값만 변경 |
| 표 모양 | 블록의 `style`/셀 옵션 |
| 새 구성요소 | ① `models.py`에 블록 모델 + `Block` 유니온에 추가 ② `renderer.py`에 함수 + `_RENDERERS` 등록 ③ 테스트 추가 (모든 게시글이 같이 씀) |
| 복잡한 그래픽 | PNG로 그려서 `image` 블록으로 |

렌더러가 지키는 인트라넷 규칙: 모든 스타일 인라인, `<th>`/`<style>`/`<script>`/`<iframe>`/form/`on*` 금지,
모든 `td`에 폰트·색·배경·테두리·여백 명시, 리스트·제목 크기 명시, 링크는 http(s)만, 모든 텍스트 HTML 이스케이프.
출력은 항상 `safety.UNSAFE_BODY`(device_agent 핸들러와 같은 정규식)를 통과해야 하며 실패하면 `RenderError`.

## 미리보기

```
python -m integrations.intranet_post render integrations/intranet_post/samples/ai_weekly_sample.json --out-dir out --screenshot
```
`out/<post_key>.preview.html`(인트라넷 전역 CSS를 흉내 낸 페이지), `.body.html`(device job에 들어갈 본문),
`.rendered.json`, `.preview.png`가 생깁니다. 실제 이미지가 있으면 `--images-dir`로 지정하세요.
미리보기는 근사치입니다. 최종 확인은 테스트 게시판 1건으로 하세요.

## 위치와 의존성

- BOA의 `integrations/intranet_post/`. 게시(Blob·Device Job·콜백)는 `integrations/intranet_publish/`가 맡습니다.
- 의존성: `pydantic>=2` (미리보기 스크린샷과 브라우저 테스트만 `playwright` 필요, 없으면 자동 skip)

## 테스트

```
python -m pytest -q tests/intranet_post
```
`test_browser.py`는 실제 Chromium에서 계산된 스타일로 "페이지 CSS가 본문으로 새지 않는지"를 확인합니다.
