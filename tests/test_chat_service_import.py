import os
import subprocess
import sys


# Chat service must import standalone for the VPS runner path.
def test_chat_service_imports_without_api_package_cycle():
    env = os.environ.copy()
    env["PYTHONPATH"] = "src"
    result = subprocess.run(
        [sys.executable, "-c", "from sma_monitor.chat.service import complete_chat_response; print(callable(complete_chat_response))"],
        cwd=os.getcwd(),
        env=env,
        text=True,
        capture_output=True,
        timeout=30,
    )

    assert result.returncode == 0, result.stderr
    assert "True" in result.stdout
