- Tool 에서 DocString 작성하기
 
1. 동사로 시작: 조회한다 / 변경한다 / 생성한다
2. 언제 사용하는지 명시
3. 언제 사용하지 않는지 명시
4. 비슷한 Tool이 있으면 차이점을 직접 언급
5. 필수 파라미터나 선행 조건 명시
6. 구현 세부사항은 description에 과하게 넣지 않기

---

## 실행 명령어

### 1. 앱 실행 (FastAPI / Uvicorn)
```bash
# 기본 실행 (포트 8080)
uvicorn app.main:app --reload --port 8080

# certs 인증서(SSL/TLS) 포함 실행
uvicorn app.main:app --reload --port 8080 --ssl-certfile certs/cert.pem --ssl-keyfile certs/privKey.pem

# 또는 호스트 지정 필요 시 (HTTPS)
uvicorn app.main:app --reload --host 0.0.0.0 --port 8080 --ssl-certfile certs/cert.pem --ssl-keyfile certs/privKey.pem
```

### 2. ngrok 터널링 실행
```bash
# 기본 HTTP 포워딩
ngrok http 8080

# HTTPS 앱 포워딩 시
ngrok http https://localhost:8080

# 고정 도메인이 있는 경우
# ngrok http --url=<도메인> https://localhost:8080
```