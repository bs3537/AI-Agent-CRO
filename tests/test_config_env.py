from sma_monitor.config import Settings


def test_settings_ignore_blank_process_env_and_use_dotenv_values(tmp_path, monkeypatch):
    """Blank inherited env vars must not mask non-empty .env credentials."""
    for key in (
        "IBKR_FLEX_TOKEN",
        "IBKR_FLEX_QUERY_ID",
        "SEMANTIC_SCHOLAR_API_KEY",
    ):
        monkeypatch.setenv(key, "")

    env_file = tmp_path / ".env"
    env_file.write_text(
        "IBKR_FLEX_TOKEN=token-from-dotenv\n"
        "IBKR_FLEX_QUERY_ID=query-from-dotenv\n"
        "SEMANTIC_SCHOLAR_API_KEY=semantic-from-dotenv\n",
        encoding="utf-8",
    )

    settings = Settings(_env_file=env_file)

    assert settings.ibkr_flex_token == "token-from-dotenv"
    assert settings.ibkr_flex_query_id == "query-from-dotenv"
    assert settings.semantic_scholar_api_key == "semantic-from-dotenv"
