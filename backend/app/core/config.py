"""
Sentinel application configuration.
Centralized configuration using environment variables with sensible defaults.
"""

import os

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# Ensure server-side credentials are populated from operator session if not already in os.environ
if not os.environ.get("RTSP_USER") or not os.environ.get("RTSP_PASSWORD"):
    try:
        import psutil
        for p in psutil.process_iter(['pid', 'name']):
            try:
                penv = p.environ()
                if 'RTSP_USER' in penv and 'RTSP_PASSWORD' in penv:
                    os.environ.setdefault("RTSP_USER", penv['RTSP_USER'])
                    os.environ.setdefault("RTSP_PASSWORD", penv['RTSP_PASSWORD'])
                    break
            except Exception:
                continue
    except Exception:
        pass


class Settings:
    """Application settings loaded from environment variables."""

    # Application
    APP_NAME: str = "Sentinel CCTV API"
    APP_VERSION: str = "0.1.0"
    DEBUG: bool = os.environ.get("DEBUG", "false").lower() == "true"

    # Database
    DATABASE_URL: str = os.environ.get(
        "DATABASE_URL",
        "postgresql+psycopg2://sentinel:sentinel_password@127.0.0.1:5433/sentinel"
    )

    # Streaming
    RTSP_CATALOG_URL: str = os.environ.get(
        "RTSP_CATALOG_URL",
        "https://cctv.corp8.cloud/cameras.json"
    )
    RTSP_CATALOG_TOKEN: str | None = os.environ.get("RTSP_CATALOG_TOKEN")
    RTSP_TRANSPORT: str = os.environ.get("RTSP_TRANSPORT", "tcp")
    WHEP_GATEWAY_URL: str = os.environ.get(
        "WHEP_GATEWAY_URL",
        "http://103.250.160.189:8889"
    )
    RTSP_GATEWAY_URL: str = os.environ.get(
        "RTSP_GATEWAY_URL",
        "rtsp://103.250.160.189:8554"
    )
    RTSP_USER: str | None = os.environ.get("RTSP_USER")
    RTSP_PASSWORD: str | None = os.environ.get("RTSP_PASSWORD")

    # AI / Detection
    AI_MODEL_PATH: str = os.environ.get("AI_MODEL_PATH", "models/yolov8n.pt")
    ANPR_MODEL_PATH: str = os.environ.get("ANPR_MODEL_PATH", "models/anpr.pt")
    DETECTION_CONFIDENCE: float = float(os.environ.get("DETECTION_CONFIDENCE", "0.40"))
    ANPR_CONFIDENCE: float = float(os.environ.get("ANPR_CONFIDENCE", "0.70"))

    # Logging
    LOG_LEVEL: str = os.environ.get("LOG_LEVEL", "INFO")

    # CORS — allowed origins for the React frontend
    CORS_ORIGINS: list[str] = os.environ.get(
        "CORS_ORIGINS",
        "http://localhost:5173,http://127.0.0.1:5173,http://localhost:3000,http://127.0.0.1:3000,http://localhost,http://127.0.0.1"
    ).split(",")

    # API prefix
    API_PREFIX: str = "/api"


settings = Settings()
