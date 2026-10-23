---
name: waug
description: WAUG 관리자 로그인, 주문·예약 메뉴 탐색, 읽기 전용 주문 조회 및 엑셀 다운로드. common과 함께 읽는다.
metadata:
  browser:
    allowed_hosts: [wadmin.waug.com]
    start_url: https://wadmin.waug.com/login.html
    start_url_env: WAUG_ADMIN_URL
    credentials:
      username_env: WAUG_ADMIN_ID
      password_env: WAUG_ADMIN_PASSWORD
    
    selectors:
      authenticated:
        env: WAUG_AUTHENTICATED_SELECTOR
        fallback_env: WAUG_LOGGED_IN_SELECTOR
        default: 'a[href="logout.php"]'

      username:
        env: WAUG_LOGIN_ID_SELECTOR
        default:
          - 'input[placeholder*="id" i]'
          - 'input[name="id"]'
          - 'input[name="username"]'
          - 'input[type="text"]'

      password:
        env: WAUG_LOGIN_PASSWORD_SELECTOR
        default: 'input[type="password"]'

      submit:
        env: WAUG_LOGIN_BUTTON_SELECTOR
        default:
          - 'role=button[name="Login"]'
          - 'button[type="submit"]'
          - 'input[type="submit"]'

      orders_url:
        env: WAUG_ORDERS_URL
        default: 'https://wadmin.waug.com/com_order_list_new.html'

      rows:
        env: WAUG_ORDER_ROW_SELECTOR
        default: 'table.admin tr.order-list-highlight'

      order_id:
        env: WAUG_ORDER_ID_SELECTOR
        default: 'td:nth-child(4)'

      product_name:
        env: WAUG_PRODUCT_SELECTOR
        default: 'td:nth-child(8) a'

      status:
        env: WAUG_STATUS_SELECTOR
        default: '[name="com-order-status-text"]'

      download:
        env: WAUG_DOWNLOAD_SELECTOR
        default: '#btn-seller-excel-order'

---

# WAUG 관리자 작업

## 접근 및 로그인

기존 코드에서 확인한 기본 주소는 https://wadmin.waug.com/login.html 이다.
WAUG_ADMIN_URL로 바꾸더라도 허용 호스트는 wadmin.waug.com만 유지한다.
start_browser(skill="waug")로 현재 대화 세션을 재사용하거나 시작한다.
browser_action(skill="waug", action="check_login")이 authenticated이면 재입력하지 않는다.
그렇지 않으면 action="login"을 사용한다. 서버가 WAUG_ADMIN_ID, WAUG_ADMIN_PASSWORD를
직접 입력하며 모델에 반환하지 않는다.

ID·제출 선택자는 기존 구현에서도 확정되지 않았다. 위 default는 기존 탐색 후보이며
실제 사이트에서 검증됐다는 뜻이 아니다. 가시 요소 하나로 식별될 때만 사용한다.
WAUG_LOGIN_ID_SELECTOR, WAUG_LOGIN_PASSWORD_SELECTOR, WAUG_LOGIN_BUTTON_SELECTOR로
실제 확인한 선택자를 설정한다.

인증 완료는 실제 확인한 WAUG_AUTHENTICATED_SELECTOR로 판정한다.
기존 주문 코드의 WAUG_LOGGED_IN_SELECTOR도 대체 설정으로 지원한다.
URL 변경이나 로그인 폼 소멸만으로 성공을 확정하지 않는다.
credentials_required는 서버 설정 점검을 안내하고 비밀번호를 채팅으로 요청하지 않는다.
authentication_unverified/login_unconfirmed는 성공이 아니다. CAPTCHA/MFA는 수동 인증을 요청한다.
인증 판정 선택자가 없어도 서버 계정으로 로그인 요청은 제출할 수 있다.
이때 login_submitted_unverified이면 제출만 확인됐으며 로그인 완료로 보고하지 않는다.
인증 판정 선택자 미설정을 계정정보 누락으로 해석하거나 채팅으로 계정정보를 요구하지 않는다.

## 주문 메뉴와 조회 조건

인증 후 action="read"로 현재 링크와 본문을 읽는다.
기존 메뉴 탐색 기준은 텍스트/href의 `주문|예약|order|booking|reservation`이다.
wadmin.waug.com의 실제 주문 목록 링크를 확인한 뒤 action="navigate", params.url로 이동한다.
WAUG_ORDERS_URL이 실제 검증돼 설정됐다면 url을 생략할 수 있다.
WAUG 주문 목록 URL은 https://wadmin.waug.com/com_order_list_new.html이며, 실제 관리자 HTML에서 확인했다. 주문 조회 요청 시 기존 세션을 재사용하고 이 URL로 이동한다. 주문번호, 상품명, 주문 상태는 selectors에 정의된 CSS 선택자를 사용해 read_rows로 조회한다.

날짜·상태 필터 선택자/값도 기존 구현에 없다. DOM/화면에서 실제 요소를 확인한 뒤
fill/select로 사용자 요청 조건을 설정하고 조회 버튼만 클릭한다. 조건이 모호하면 확인한다.
WAUG_ORDER_ROW_SELECTOR, WAUG_ORDER_ID_SELECTOR, WAUG_PRODUCT_SELECTOR,
WAUG_STATUS_SELECTOR가 설정됐다면 action="read_rows"로 구조화된 주문을 조회한다.
없다면 본문/화면 이상의 값을 추측하지 말고 실제 선택자 설정이 필요하다고 알린다.
현재 렌더링된 행만 반환하므로 전체 주문 건수로 보고하지 않는다. 페이지 이동도 실제 UI를 확인한다.
주문 상세 경로는 기존 구현에서도 미구현이었다. URL을 임의로 만들지 않는다.

## 엑셀 다운로드

조회 조건과 결과를 확인한 뒤 action="download"를 호출한다.
버튼은 WAUG_DOWNLOAD_SELECTOR로 설정한다. 기존 코드에 고정 선택자가 없다.
설정이 없다면 실제 확인한 다운로드 버튼 selector만 params.selector로 지정한다.
파일 이벤트, bytes, SHA-256을 확인한 뒤 완료로 보고한다.
필요하면 기존 orders.excel_reader.inspect_order_workbook으로 XLSX/XLSM 내용을 검증한다.

## 복구와 금지 작업

인증 만료 시 상태부터 확인하고 실패한 로그인을 무한 반복하지 않는다.
화면이 바뀌면 DOM/화면으로 재확인한다. 다운로드 실패 시 인증과 조회 조건부터 점검한다.
조회·다운로드 요청에서 예약 확정, 주문 변경, 취소, 환불을 수행하지 않는다.
추가 인증 우회, 환경변수 값 출력, 다른 사용자 브라우저 사용은 금지한다.
