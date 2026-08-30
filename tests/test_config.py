import pytest
from pathlib import Path
from util.config import Config

@pytest.fixture
def mock_toml(tmp_path, monkeypatch):
    config_content = """
    [core]
    exclude_files = ["test.py"]
    
    [runners]
    c = "my_clang"
    
    [presets.debug]
    c = ["-g"]
    
    [[languages]]
    name = "custom"
    extensions = [".custom"]
    runner = "custom_run"
    type = "interpreter"
    """
    config_file = tmp_path / "Run.toml"
    config_file.write_text(config_content)
    
    # Mock Path.cwd() to return tmp_path so Config picks it up
    monkeypatch.setattr(Path, "cwd", lambda: tmp_path)
    return config_file

def test_config_load(mock_toml):
    config = Config()
    assert config.get_runner("c", "clang") == "my_clang"
    assert config.get_runner("cpp", "clang++") == "clang++"
    
def test_preset_flags(mock_toml):
    config = Config()
    assert config.get_preset_flags("debug", "c") == ["-g"]
    assert config.get_preset_flags("release", "c") == []
    
def test_get_custom_languages(mock_toml):
    config = Config()
    langs = config.get_custom_languages()
    assert "custom" in langs
    assert langs["custom"]["extensions"] == [".custom"]
    assert config.is_custom_language_configured(".custom")
    assert not config.is_custom_language_configured(".unknown")

def test_sandbox_config_validation_valid(tmp_path, monkeypatch):
    df = tmp_path / "Dockerfile"
    df.write_text("FROM alpine\n")
    compose = tmp_path / "docker-compose.yml"
    compose.write_text("version: '3'\n")

    toml = tmp_path / "Run.toml"
    toml.write_text(f"""
    [sandbox]
    image = "gcc:latest"
    dockerfile = "{df}"
    compose = "{compose}"
    compose_service = "app"
    sandbox_net = true
    restrict = false
    """)
    monkeypatch.setattr(Path, "cwd", lambda: tmp_path)
    config = Config()
    assert config.get_sandbox_config()["image"] == "gcc:latest"

def test_sandbox_config_validation_invalid_image(tmp_path, monkeypatch):
    toml = tmp_path / "Run.toml"
    toml.write_text("""
    [sandbox]
    image = "--privileged"
    """)
    monkeypatch.setattr(Path, "cwd", lambda: tmp_path)
    with pytest.raises(ValueError, match="Invalid sandbox image name"):
        Config()

def test_sandbox_config_validation_missing_dockerfile(tmp_path, monkeypatch):
    toml = tmp_path / "Run.toml"
    toml.write_text("""
    [sandbox]
    dockerfile = "nonexistent_dockerfile"
    """)
    monkeypatch.setattr(Path, "cwd", lambda: tmp_path)
    with pytest.raises(ValueError, match="sandbox Dockerfile '.*' not found"):
        Config()

