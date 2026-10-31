from pydantic_settings import BaseSettings
from pydantic import Field
import yaml
import os

class Settings(BaseSettings):
    ollama_base_url: str = Field(default="http://localhost:11434/v1", alias="OLLAMA_BASE_URL")
    ollama_model: str = Field(default="qwen2.5:14b", alias="OLLAMA_MODEL")
    openrouter_api_key: str = Field(default="", alias="OPENROUTER_API_KEY")
    openrouter_base_url: str = Field(default="https://openrouter.ai/api/v1", alias="OPENROUTER_BASE_URL")
    macro_model: str = Field(default="deepseek/deepseek-chat", alias="MACRO_MODEL")
    deep_dive_model: str = Field(default="deepseek/deepseek-chat", alias="DEEP_DIVE_MODEL")
    qdrant_host: str = Field(default="localhost", alias="QDRANT_HOST")
    qdrant_port: int = Field(default=6333, alias="QDRANT_PORT")
    sqlite_db_path: str = Field(default="./data/novel_ledger.db", alias="SQLITE_DB_PATH")

    class Config:
        env_file = ".env"
        extra = "ignore"

settings = Settings()

def load_yaml_config(filepath: str = "configs/config.yaml") -> dict:
    if os.path.exists(filepath):
        with open(filepath, "r", encoding="utf-8") as f:
            return yaml.safe_load(f)
    return {}

yaml_config = load_yaml_config()

# Prototype compatibility: load arc_detection from novel config
pntt1_path = "configs/novels/pntt1.yaml"
if not os.path.exists(pntt1_path):
    repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    pntt1_path = os.path.join(repo_root, "configs", "novels", "pntt1.yaml")
if os.path.exists(pntt1_path):
    with open(pntt1_path, "r", encoding="utf-8") as f:
        _pntt1_cfg = yaml.safe_load(f) or {}
    if "arc_detection" in _pntt1_cfg:
        yaml_config["arc_detection"] = _pntt1_cfg["arc_detection"]

