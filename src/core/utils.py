import pytz
from datetime import datetime, timedelta

def today():
    korea = pytz.timezone('Asia/Seoul') 
    now = datetime.now(korea)
    return now.strftime("%Y%m%d")

def yesterday():
    korea = pytz.timezone('Asia/Seoul')
    now = datetime.now(korea)
    one_day = timedelta(days=1)
    yesterday = now - one_day
    return yesterday.strftime('%Y%m%d')

def currTime():
    korea = pytz.timezone('Asia/Seoul')
    now = datetime.now(korea)
    formatted_now = now.strftime("%Y.%m.%d %H:%M:%S")
    return formatted_now