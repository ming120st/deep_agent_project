# wifi_sim_change 워크플로우

## 목적

이심 재발급: 자사가 판매하는 이심 사용 중 문제 발생 시 대체 이심을 발송하는 업무.

## 파일 구조

| 파일 | 단계 | 역할 |
|------|------|------|
| graph.py | 전체 | LangGraph 그래프 정의, 노드 연결 및 분기 조건 |
| node.py | 3.1~3.7 | 각 단계 노드 함수 |


## 관련 정보
  - 주문번호(order_id) 양식 예시: "E26031101886"



## 3. 세부 절차

### 3.1 주요 파라미터 확보
이심 재발급 업무처리에 필요한 파라미터를 사용자로부터 확보합니다.
| 항목 | 변수명 | 데이터타입 | 설명 |
|------|-------|-----------|------|
|주문번호 | order_id | str | E로 시작하는 내부 주문번호 |
|사용일수 | request_days | int | 며칠짜리 eSIM을 발급할지 |

※ 수량(request_qnt)은 사용자에게 묻지 않음. 3.2.2에서 사용자가 선택한 iccid 수로 자동 결정됨.


### 3.2 주문 상세내용 조회
  - src.core.internal_esim_client.py 의 "esim_client" 인스턴스 사용.
  - order_id 파라미터를 가지고 formatted_order_id = esim_client.encrypt(order_id) 해서 encrypt된 order_id 생성
  - response = await esim_client.call_esim_api_get(
    f"https://wapi.wifidosirak.com/api/v1/order/{formatted_order_id}"
    )
    호출하여 주문의 상세내용 반환받음.
  - response["resultCode"]가 True(boolean)면 진행, False면 프로세스 종료하고 사용자에게 현재 내부 전산 오류로 주문조회가 되지 않으니 나중에 다시 시도해보라고 반환.
  - 아래 공통 정보 추출:
    | 변수명 | 추출key |
    |------|-----------|
    | name | response["resultValue"]["name"] |
    | email | response["resultValue"]["email"] |
    | mobile | response["resultValue"]["mobile"] |
    | status | response["resultValue"]["status"] |
    | join_route_id | response["resultValue"]["joinRouteId"] |

  - response["resultValue"]["ordersDetail"] 리스트를 전체 순회하여 order_products 리스트 구성:
```python
    order_products = []
    for item in response["resultValue"]["ordersDetail"]:
        order_products.append({
            "product_code": item["productCode"],
            "product_name": item["productName"],
            "days":         item["useDay"],
            "price":        item["price"],
            "iccid":        item["iccid"],
        })
```

#### 3.2.1 재발급 대상 iccid 선택
  - len(order_products) == 1인 경우:
    - selected_iccids = [order_products[0]["iccid"]] 로 자동 선택. 사용자에게 묻지 않음.
  - len(order_products) >= 2인 경우:
    - 사용자에게 전체 목록을 보여주며 재발급이 필요한 iccid 선택 요청. (interrupt)
```
      아래 구매 목록 중 재발급이 필요한 항목을 선택해주세요. (복수 선택 가능)

      1. {product_name} / ICCID: {iccid} / {days}일
      2. {product_name} / ICCID: {iccid} / {days}일
      ...
```
    - 사용자 응답에서 선택한 iccid들을 selected_iccids 리스트로 확정.

#### 3.2.2 선택된 iccid 유효성 검증 및 공통 상품정보 추출
  - selected_iccids에 해당하는 항목들을 order_products에서 필터링.
  - 선택된 항목들의 product_code가 모두 동일한지 확인.
    - 동일하지 않으면: "선택하신 iccid들의 상품 종류가 서로 다릅니다. 동일한 상품의 iccid만 함께 선택해주세요." 안내 후 3.2.1로 돌아가 재선택 요청.
    - 동일하면: 첫번째 선택 항목 기준으로 아래 공통 변수 추출.
      | 변수명 | 설명 |
      |--------|------|
      | product_code | 선택된 상품코드 |
      | product_name | 선택된 상품명 |
      | days | 선택된 상품 사용일수 |
      | price | 선택된 상품 가격 |
  - request_qnt = len(selected_iccids) 로 자동 설정.


### 3.3 상품 선정
  반환데이터 형식: product_code 리스트. (변수명:product_list)
  - if request_days == days -> product_list = [product_code] 하고 3.4로 이동 (기존 구매한 상품 그대로)
  - else 일때는
    3.3.1 상품코드는 전부 예를들어 "E_EU33_BC_10GB_30D" 이런식으로 마지막에 {N}D 형식으로 끝나는데, {N}만 request_days로 변경하여 alt_product_code 변수에 할당
    3.3.2 response = await esim_client.call_esim_api_get(
    f"https://wapi.wifidosirak.com/api/v1/product/{alt_product_code}"
    )
    3.3.3 response["resultCode"]가 True(boolean)면 상품이 있는거니까 alt_product_code로 확정, False면 3.3.4로 진행
    3.3.4 사용자에게 사용국가를 물어보고 (예시: 일본, 대만) -> 3.3.5에서 ISO 3166-1 Alpha-3 기준 알파벳 3자리 국가코드로 변환해서 country 변수에 할당
     - 예시: 일본 -> JPN, 대만 -> TWN
     - 국가코드 변환은 https://en.wikipedia.org/wiki/ISO_3166-1_alpha-3 참고
     - 국가코드 변환 실패 시 사용자에게 죄송하지만 국가코드 변환이 안되는 국가입니다 라고 반환하고 다시 물어보기
     - 국가코드 변환 성공 시 해당 국가코드를 country 변수에 할당하고 3.3.5로 진행
    3.3.5 country 변수로 해당 국가의 전체 상품 리스트를 조회하는 API 호출
      - 방식: POST
      - payload = {"countryId": country}
      - response = await esim_client.call_esim_api_post(
          api_url="https://wapi.wifidosirak.com/api/v1/product/detail/country",
          json= payload
        )
    3.3.6 response["resultCode"]가 True(boolean)면 진행, False면 프로세스 종료하고 사용자에게 현재 내부 전산 오류로 나중에 다시 시도해보라고 반환.
    3.3.7 response["resultValue"]["products"]는 리스트인데 순회하면서 아래 조건에 맞는것만 alt_product_list 리스트값에 넣기
      - response["resultValue"]["products"][{i}]["useDay"]가 request_days와 같은 것
      - response["resultValue"]["products"][{i}]["description"]에 "개통희망일" 이 있으면 제외
      - response["resultValue"]["products"][{i}]["name"]에 "실명인증" 이 있으면 제외
      - response["resultValue"]["products"][{i}]["price"]/response["resultValue"]["products"][{i}]["useDay"]가 price/days (3.2.2에서 추출한 값. 기존 구매상품의 하루환산 단가) 값보다 큰 것
      ※ 하루 단가 기준 초과 조건은 의도적임. 재발급이므로 기존보다 저렴한 상품은 품질 저하로 간주하여 제외.
      - 위 조건에 맞는 상품이 하나도 없으면 프로세스 종료하고 사용자에게 죄송하지만 조건에 맞는 대체상품이 없습니다 라고 반환.
      - 위 조건에 맞는 상품이 있으면 최대 20개까지 alt_product_list_sorted에 가격 오름차순으로 정렬하여 저장 (3.3.8에서 AI 선정 전에 상위 20개로 제한)

    3.3.8 alt_product_list를 AI에게 전달하여 최종 product_list 구성.
      AI 프롬프트 지시사항:
      - alt_product_list의 각 상품 name과 3.2.2에서 추출한 product_name(기존 구매상품명)을 참고하여 기존 구매상품보다 데이터제공량이 적지 않은 상품을 대체상품으로 선정. (무제한은 무제한만 매칭)
      - 가격이 낮은 것 우선
      - 위 조건이 동일하면 데이터 용량이 큰 것 우선
      - 최대 5개까지만 선정하여 리스트로 반환
      반환형식: ["E_JP_BC_5GB_30D", "E_JP_BC_3GB_30D", ...]


### 3.4 상품 상세 조회
  3.3에서 만들어진 product_list를 for문 돌면서 필요한 정보를 조회해서 추가하여 리스트 완성
  - 방식: Get
  - url: https://wapi.wifidosirak.com/api/v1/product/{productCode}
  - response["resultCode"]가 True(boolean)면 진행, False면 프로세스 종료하고 사용자에게 현재 내부 전산 오류로 나중에 다시 시도해보라고 반환.
  - response["resultValue"]에서 아래 정보 추출해서 변수 할당
    | 변수명 | 추출key |
    |------|-----------|
    | product_code | ["code"] |
    | product_name | ["name"] |
    | product_price | ["price"] |
  -> 위 3가지 정보로 이루어진 상품의 리스트변수를 반환
  예시: [{"product_code": "E_EU33_BC_10GB_30D", "product_name": "유럽 33개국 10GB 30일", "product_price": 1350}, {"product_code": "E_JP_BC_5GB_30D", "product_name": "일본 5GB 30일", "product_price": 1200}]


### 3.5 사용자 컨펌

  3.5.1 초기 payload 구성
    3.4에서 만들어진 product_list의 첫번째 요소로 payload 구성.
    current_product_index = 0 으로 초기화.
```json
    {
      "OutOrderId": "{order_id}재발송",
      "Name": name,
      "ProductName": product_name,
      "ProductCode": product_code,
      "JoinRoute": str(join_route_id),
      "Qty": request_qnt,
      "Email": email,
      "Mobile": mobile,
      "PaymentPrice": product_price,
      "UserID": state.user_id
    }
```

  3.5.2 컨펌 메시지 출력 후 사용자 입력 대기
    구성된 payload 내용을 사용자에게 보여주고 확인 요청. (interrupt)

  3.5.3 AI 노드 - 의도 파악 및 payload 수정
    사용자 응답을 받으면 아래 입력값을 AI에게 전달하여 처리.

    **입력:**
    - `current_payload`: 현재 payload
    - `product_list`: 3.4에서 만들어진 전체 상품 리스트
    - `current_product_index`: 현재 선택된 상품 인덱스
    - `user_message`: 사용자 입력

    **프롬프트 지시사항:**
    반드시 아래 JSON 형식으로만 응답.
```json
    {
      "action": "confirm" | "modify" | "change_product",
      "updated_payload": { },
      "next_product_index": 0,
      "message_to_user": "..."
    }
```
    - `confirm`: 사용자가 내용에 동의함
    - `modify`: 특정 필드 수정 요청 (updated_payload에 수정 반영)
    - `change_product`: 상품 교체 요청 (next_product_index로 product_list에서 선택)
    수정 가능 필드: Email, Mobile, Qty
    수정 불가 필드: OutOrderId, UserID, JoinRoute, ProductCode, ProductName, PaymentPrice
    → 수정 불가 필드 변경 요청 시 action은 modify로 하되, updated_payload는 변경하지 않고 message_to_user에 "해당 항목은 수정이 불가합니다" 안내.

  3.5.4 action에 따른 분기 (코드)
    - `confirm` → 최종 payload 확정 후 3.6으로 이동
    - `modify` → updated_payload로 교체 후 3.5.2로 돌아가 재확인
    - `change_product` →
      - next_product_index가 product_list 범위 초과 시 "더 이상 추천 상품이 없습니다"
        안내 후 3.5.2 재확인
      - 범위 내이면 product_list[next_product_index]로 아래 3개 필드만 교체 후 3.5.2로 돌아가 재확인
        - ProductCode ← product_list[next_product_index]["product_code"]
        - ProductName ← product_list[next_product_index]["product_name"]
        - PaymentPrice ← product_list[next_product_index]["product_price"]


### 3.6 이심 발송
  - POST방식
  - https://wapi.wifidosirak.com/api/v1/order/agentregist
  - payload는 3.5에서 확정된 payload 사용.
  - request_qnt만큼 반복 호출. 호출당 1개 발송.
    - 각 호출 시 payload의 "OutOrderId"를 "{order_id}재발송_{i+1}" 형식으로 구분.
      (예: 3개 발송 시 → "E26031101886재발송_1", "E26031101886재발송_2", "E26031101886재발송_3")
  
  #### 반복 로직
```python
  MAX_RETRY = 5
  results = []  # 발송 성공한 건 누적

  for i in range(request_qnt):
      payload_i = {**payload, "OutOrderId": f"{order_id}재발송_{i+1}"}
      success = False

      for attempt in range(MAX_RETRY):
          response = await esim_client.call_esim_api_post(
              api_url="https://wapi.wifidosirak.com/api/v1/order/agentregist",
              json=payload_i
          )
          if response["resultCode"] == True:
              results.append({"index": i+1, "status": "success"})
              success = True
              break
          # 실패 시 다음 시도로. 
          # 근데 {"resultCode":false,"resultMessage":"이미 처리된 외부주문번호 오류"}면 confirm_payload 노드에서 이 payload에서 "OutOrderId":   f"{order_id}재발송" 뒤에 숫자를 1,2,3..붙여가면서 재시도해야함.

      if not success:
          # 이 건에 대해 MAX_RETRY 모두 소진
          # 전체 중단
          사용자에게 아래 안내 후 프로세스 종료:
          "이심 발송 시스템에 문제가 발생했습니다.
          ({i+1}번째 발송에서 {MAX_RETRY}회 시도 후 실패)
          직접 재발급 처리를 진행해주세요."
          return state
```

  - 모든 건 성공 시 → 사용자에게 완료 안내 후 3.7로 이동.
```
    "총 {request_qnt}건의 이심 재발급 발송이 완료되었습니다."
```


### 3.7 성공 시 상담 등록

  #### 3.7.1 device_agent에 작업 요청
  job_id가 이미 state에 있으면 재전송 건너뜀 (LangGraph 재실행 방지).
```python
  params = _get_params(state)
  job_id = params.get("_job_id")

  if not job_id:
      req = DeviceJobRequest(
          thread_id=state.chat_context.get("user_id", ""),
          task="register_consultation",
          parameters={
              "order_id":     order_id,
              "name":         name,
              "product_name": product_name,
              "iccid_list":   selected_iccids,
              "memo":         f"이심 재발급 완료. 발송 상품: {product_name}",
          },
          context={"user_name": state.chat_context.get("user_name"), "user_id": state.chat_context.get("user_id")},
      )
      job_id = req.job_id
      _set_param(state, "_job_id", job_id)  # ★ interrupt() 전에 반드시 저장

      await create_device_job(job_id, state.chat_context.get("user_id", ""))

      try:
          async with httpx.AsyncClient() as client:
              resp = await client.post(
                  f"{DEVICE_JOB_URL}/device-job",
                  json=req.model_dump(),
                  headers={"X-Device-Job-Key": DEVICE_JOB_SECRET},
                  timeout=10.0,
              )
              resp.raise_for_status()
      except Exception as e:
          state.add_ai_message(f"⚠️ PC 에이전트 연결에 실패했습니다. 잠시 후 다시 시도해주세요.\n({e})")
          await set_cancelled(state, result=f"DeviceJob 전송 실패: {e}")
          state.current_agent = None
          return state

      state.add_ai_message("상담 등록 작업을 PC에이전트에 전달했습니다. 잠시만 기다려주세요.")
      await set_waiting(state, result=f"DeviceJob 대기 중: {job_id}")
```

  #### 3.7.2 콜백 대기 (interrupt)
  device_agent 작업 완료까지 그래프 중단.
  POST /device-callback 수신 시 Command(resume=callback_payload)로 재개됨.
```python
  callback_payload: dict = interrupt({"job_id": job_id, "status": "waiting"})
  callback = DeviceJobCallback(**callback_payload)
```

  #### 3.7.3 결과 처리
  - callback.status == "success" → 완료 안내 후 current_agent = None으로 초기화.
```
    "이심 재발급 및 상담 등록이 완료되었습니다."
```
  - callback.status == "error" → 아래 안내 후 current_agent = None으로 초기화.
```
    "이심 발송은 완료되었으나 상담 등록 중 오류가 발생했습니다.
    수동으로 상담 등록을 진행해주세요. (사유: {callback.error_message})"
```


## 전체 흐름
```
  [3.1] 파라미터 확보 (order_id, request_days)
    └─(interrupt)─ 사용자 입력
          ↓
  [3.2] 주문 상세 조회 → order_products 전체 추출
    ├─(실패)─ 종료
    └─(성공)─
          ↓
  [3.2.1] 재발급 대상 iccid 선택
    ├─ len == 1 → 자동 선택
    └─ len >= 2 → (interrupt) 사용자 선택
          ↓
  [3.2.2] iccid 유효성 검증
    ├─ 상품 종류 다름 → 3.2.1로 재선택
    └─ 동일 → product_code/days/price 확정, request_qnt = len(selected_iccids)
          ↓
  [3.3] 상품 선정
    ├─ request_days == days → product_list = [product_code] → [3.4]
    └─ request_days != days
          ↓
        [3.3.1~3.3.3] alt_product_code 시도
          ├─(성공)─ product_list = [alt_product_code] → [3.4]
          └─(실패)─ 국가 입력 필요
                └─(interrupt)─ 사용자: 사용국가 입력
                      ↓
                [3.3.5~3.3.8] 국가 기반 상품 조회 → AI 선정 → product_list
                      ↓
  [3.4] 상품 상세 조회 (product_list for문)
    ├─(실패)─ 종료
    └─(성공)─ product_list에 name, price 추가
          ↓
  [3.5.1] 초기 payload 구성 (product_list[0])
          ↓
  [3.5.2] payload 사용자에게 출력
    └─(interrupt)─ 사용자: 확인 또는 수정 요청
          ↓
  [3.5.3] AI 노드 - 의도 파악
    ├─ confirm        → [3.6]
    ├─ modify         → payload 수정 → [3.5.2]
    └─ change_product → payload 상품 교체 → [3.5.2]
          ↓
  [3.6] eSIM 발송 API 호출
    ├─(실패)─ 사유 안내 → [3.5.2]
    └─(성공)─ [3.7]
          ↓
  [3.7] 상담 등록 (device_agent)
    └─(interrupt)─ device_agent 콜백 대기
          ↓
        완료
```


## interrupt 발생 지점

  | 지점 | 노드 | 목적 | 재개 조건 |
  |------|------|------|-----------|
  | 3.1 | request_parameters | order_id, request_days 수집 | 파라미터 모두 확보 |
  | 3.2.1 | select_iccid | 재발급 대상 iccid 선택 (복수 상품일 때만) | iccid 선택 완료 |
  | 3.3.4 | request_country | 사용국가 입력 | 국가명 입력 |
  | 3.5.2 | confirm_payload | payload 컨펌 또는 수정 요청 | 사용자 응답 |
  | 3.7 | register_consultation | device_agent 작업 완료 콜백 | POST /device-callback 수신 |


## GraphState 사용 필드

  공통 GraphState(src.workflow.graph_infra.state.GraphState)를 그대로 사용.
  이 에이전트에서 활용하는 필드는 아래와 같음.

  | 필드 | 타입 | 사용 용도 |
  |------|------|-----------|
  | messages | List[Dict] | 사용자 ↔ AI 대화 히스토리 |
  | collected_parameters["esim_fault_replacement"] | Dict | 수집된 파라미터 저장소 |
  | collected_parameters["esim_fault_replacement"]["order_id"] | str | 주문번호 |
  | collected_parameters["esim_fault_replacement"]["request_days"] | int | 요청 사용일수 |
  | collected_parameters["esim_fault_replacement"]["request_qnt"] | int | 선택된 iccid 수 (자동 설정) |
  | collected_parameters["esim_fault_replacement"]["order_products"] | List[Dict] | 3.2에서 추출한 전체 구매 상품 리스트 |
  | collected_parameters["esim_fault_replacement"]["selected_iccids"] | List[str] | 재발급 요청 iccid 리스트 |
  | collected_parameters["esim_fault_replacement"]["product_list"] | List[Dict] | 3.4에서 완성된 대체 상품 리스트 |
  | collected_parameters["esim_fault_replacement"]["current_payload"] | Dict | 3.5에서 구성/수정 중인 발송 payload |
  | collected_parameters["esim_fault_replacement"]["current_product_index"] | int | 현재 선택된 상품 인덱스 |
  | collected_parameters["esim_fault_replacement"]["_job_id"] | str | 3.7 device_agent job_id |
  | response | Dict | 중간 API 응답 임시 저장 |
  | task_id | Optional[str] | 작업 ID |
  | task_status | Optional[str] | 작업 상태 |
  | error_message | Optional[str] | 에러 메시지 |
  | chat_context["user_id"] | str | state.user_id 참조 시 사용 |

  ※ 에이전트 전용 데이터는 모두 collected_parameters["esim_fault_replacement"] 하위에 저장.
  ※ GraphState 전체 정의는 src/workflow/graph_infra/state.py 참고.