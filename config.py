import os
from dotenv import load_dotenv

load_dotenv()

class Config:
    # إعدادات واجهة برمجة تطبيقات MEXC
    API_KEY = os.getenv("MEXC_API_KEY", "").strip()
    API_SECRET = os.getenv("MEXC_API_SECRET", "").strip()
    BASE_URL = os.getenv("MEXC_BASE_URL", "https://api.mexc.com").strip().rstrip("/")

    # المتغير الافتراضي هو ALL_USDT لتداول جميع أزواج USDT النشطة
    SYMBOLS_CONFIG = os.getenv("SYMBOLS", os.getenv("SYMBOL", "ALL_USDT")).strip()

    # إعدادات حجم التداول وإدارة المخاطر
    TRADE_AMOUNT_USDT = float(os.getenv("TRADE_AMOUNT_USDT", "6.0"))  # الحد الأدنى المسموح به لكل صفقة
    MAX_OPEN_POSITIONS = int(os.getenv("MAX_OPEN_POSITIONS", "10"))   # أقصى عدد صفقات مفتوحة بالتوازي
    
    # هوامش الربح ووقف الخسارة
    TAKE_PROFIT_PCT = float(os.getenv("TAKE_PROFIT_PCT", "1.5"))      # نسبة جني الأرباح %
    STOP_LOSS_PCT = float(os.getenv("STOP_LOSS_PCT", "2.0"))          # نسبة وقف الخسارة %

    # إعدادات التوقيت ومعدل الطلبات
    POLL_INTERVAL = float(os.getenv("POLL_INTERVAL", "15"))           # وقت الانتظار بين كل دورة مسح شاملة (بالثواني)
    REQUEST_DELAY = float(os.getenv("REQUEST_DELAY", "0.2"))          # تأخير زمني بين كل زوج وآخر لتفادي حظر API
    SYMBOLS_REFRESH_MINUTES = int(os.getenv("SYMBOLS_REFRESH_MINUTES", "60")) # إعادة فحص العملات الجديدة كل ساعة
