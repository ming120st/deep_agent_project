도커(Docker) = 컨테이너를 만들고 실행하는 프로그램
컨테이너(Container) = 도커가 만든 독립 실행 환경
----------------------------------------------------------------------------------------
ssl적용한 실행 (개발환경)
sudo uvicorn src.main:app --host 0.0.0.0 --port 8080 --ssl-keyfile=certs/privKey.pem --ssl-certfile=certs/cert.pem --reload
uvicorn src.main:app --host 0.0.0.0 --port 8080 --ssl-keyfile=certs/privKey.pem --ssl-certfile=certs/cert.pem
----------------------------------------------------------------------------------------
ngrok (localhost 외부접속)
ngrok http https://localhost:8080
(ssl 인증서때문에 https로 지정해줘야만 터널링 가능!)
----------------------------------------------------------------------------------------
도커데스크탑 컨테이너 만드는 명령어 (아래 순서대로 실행)
1. 도커 빌드 (aiagent-fastapi는 이미지 이름)
docker build -t aiagent-fastapi .
2. 컨테이너 실행(aiagent-fastapi-ctn는 컨테이너 이름)
docker run -d -p 8080:8080 --name aiagent-fastapi-ctn --env-file .env aiagent-fastapi
----------------------------------------------------------------------------------------
SSL 설치방법
https://www.iotmaker.kr/2023/03/14/install-openssl-make-cert-windows/
----------------------------------------------------------------------------------------
모듈을 읽지 못한다면 실행버튼 말고 전용 터미널에서 python 실행으로 해보기
----------------------------------------------------------------------------------------
새 창에서 프로젝트 열어서 보고싶을 때:
ctrl + shift + p 또는 F1 을 누르고 Workspaces : Duplicate As Workspace in New Window 를 찾아서 실행
----------------------------------------------------------------------------------------

uvicorn src.main:app --host 0.0.0.0 --port 8080 --ssl-keyfile=certs/privKey.pem --ssl-certfile=certs/cert.pem