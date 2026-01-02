import logging
import os
from logging.handlers import TimedRotatingFileHandler
import sys

def setup_logger(name: str = "app_logger", log_level: int = logging.INFO):
    """
    Sets up a logger that writes to console and a daily rotating file.
    """
    # Create logs directory if it doesn't exist
    # Assuming this is run from the project root or src, we want logs in the project root
    # Get the directory of the current file (src/)
    current_dir = os.path.dirname(os.path.abspath(__file__))
    
    # Azure App Service 환경 감지 (WEBSITE_SITE_NAME 환경변수가 존재하면 Azure로 판단)
    if os.environ.get('WEBSITE_SITE_NAME'):
        # Azure App Service에서는 /home/LogFiles (Linux) 또는 D:\home\LogFiles (Windows)가 영구 저장소임
        # HOME 환경변수 아래 LogFiles 폴더를 사용
        home_dir = os.environ.get('HOME', '.')
        log_dir = os.path.join(home_dir, 'LogFiles', 'Application')
    else:
        # 로컬 개발 환경: 프로젝트 루트의 logs 폴더 사용
        project_root = os.path.dirname(current_dir)
        log_dir = os.path.join(project_root, "logs")
    
    if not os.path.exists(log_dir):
        try:
            os.makedirs(log_dir)
        except Exception:
            # 권한 문제 등으로 생성 실패 시, 콘솔 로그만 남기기 위해 패스하거나 임시 폴더 사용
            pass

    logger = logging.getLogger(name)
    logger.setLevel(log_level)

    # Check if handlers are already added to avoid duplicate logs
    if not logger.handlers:
        # Formatter
        formatter = logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
        )

        # File Handler (Daily rotation)
        # 로그 디렉토리가 존재할 때만 파일 핸들러 추가
        if os.path.exists(log_dir):
            try:
                log_file = os.path.join(log_dir, "app.log")
                file_handler = TimedRotatingFileHandler(
                    log_file, when="midnight", interval=1, backupCount=30, encoding="utf-8"
                )
                file_handler.setFormatter(formatter)
                file_handler.suffix = "%Y%m%d" # Suffix for rotated files (e.g., app.log.20240101)
                logger.addHandler(file_handler)
            except Exception as e:
                print(f"Failed to setup file handler: {e}")
        
        # Console Handler
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setFormatter(formatter)

        logger.addHandler(console_handler)

    return logger

# Create a default logger instance
logger = setup_logger()
