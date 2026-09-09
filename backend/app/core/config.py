"""
Sentinel application configuration.
Centralized configuration using environment variables with sensible defaults.
"""

import os


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
        "http://live.corp8.cloud/api/ingest"
    )
    RTSP_TRANSPORT: str = os.environ.get("RTSP_TRANSPORT", "tcp")

    # AI / Detection
    AI_MODEL_PATH: str = os.environ.get("AI_MODEL_PATH", "models/yolov8n.pt")
    ANPR_MODEL_PATH: str = os.environ.get("ANPR_MODEL_PATH", "models/anpr.pt")
    DETECTION_CONFIDENCE: float = float(os.environ.get("DETECTION_CONFIDENCE", "0.40"))
    ANPR_CONFIDENCE: float = float(os.environ.get("ANPR_CONFIDENCE", "0.70"))

    # Logging
    LOG_LEVEL: str = os.environ.get("LOG_LEVEL", "INFO")

    # CORS — allowed origins for the React frontend
    CORS_ORIGINS: list[str] = os.environ.get(
        "CORS_ORIGINS", "http://localhost:5173,http://localhost:3000"
    ).split(",")

    # API prefix
    API_PREFIX: str = "/api"


settings = Settings()
