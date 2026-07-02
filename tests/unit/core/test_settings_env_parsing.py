import os
import subprocess
import sys
from pathlib import Path


def test_settings_accepts_release_debug_env_value():
    repo_root = Path(__file__).resolve().parents[3]
    env = os.environ.copy()
    env.update(
        {
            "DEBUG": "release",
            "DEBUG_MASTER_PASSWORD": "test-master-password",
            "DEBUG_SKIP_PASSWORD_CHECK": "false",
            "DATABASE_URL": "mysql+aiomysql://placeholder:placeholder@127.0.0.1:3306/placeholder",
            "EMAIL_FROM": "test@example.com",
            "SMTP_SERVER": "smtp.example.com",
            "SMTP_PORT": "465",
            "SMTP_USER": "test@example.com",
            "SMTP_PASSWORD": "test-password",
            "REDIS_HOST": "127.0.0.1",
            "REDIS_PORT": "6379",
            "REDIS_PASSWORD": "",
        }
    )

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "from app.core.config import Settings; print(Settings().DEBUG)",
        ],
        cwd=str(repo_root),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "False"
