"""
Sentinel application configuration.
Centralized configuration using environment variables with sensible defaults.
"""

import os

from pathlib import Path

try:
    from dotenv import load_dotenv
    _env_file = Path(__file__).resolve().parent.parent.parent.parent / ".env"
    if _env_file.is_file():
        load_dotenv(dotenv_path=_env_file)
    else:
        load_dotenv()
except ImportError:
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
    ANPR_MODEL_PATH: str = os.environ.get("ANPR_MODEL_PATH", "models/license_plate_detector.pt")
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
