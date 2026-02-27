import os
import smtplib
from email.mime.text import MIMEText
from dotenv import load_dotenv

def send_mail(to_list: list, message: str, subject: str, sender: str = 'info@widemobile.com') -> None:
    # .env에서 환경변수 로드
    load_dotenv()
    gmail_app_pw = os.getenv('GMAIL_APP_PW_INFO') # info메일의 Gmail 앱 비밀번호

    try:
        # Gmail SMTP 서버 연결
        smtp = smtplib.SMTP('smtp.gmail.com', 587)
        smtp.ehlo()
        smtp.starttls()
        smtp.login(sender, gmail_app_pw)

        # 메일 내용 구성
        msg = MIMEText(message, 'plain', 'utf-8') #plain은 일반 텍스트, html은 html 형식
        msg['Subject'] = subject
        msg['From'] = sender
        msg['To'] = ", ".join(to_list)

        # 메일 전송
        smtp.sendmail(msg['From'], to_list, msg.as_string())
        print("메일 전송 완료")
    except Exception as e:
        print("메일 전송 실패:", e)
    finally:
        smtp.quit()