"""Единый лаунчер EduTutor.

python run.py            — запуск backend (uvicorn)
python run.py --frontend — запуск backend + Vite dev server (frontend)
"""

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BACKEND_DIR = ROOT / "adaptive_tutor"
FRONTEND_DIR = ROOT / "frontend"

FRONTEND_URL = "http://localhost:5173"


def _venv_python() -> Path:
    """Путь к интерпретатору локального venv backend'а (если существует)."""
    is_win = os.name == "nt"
    scripts_dir = "Scripts" if is_win else "bin"
    exe_name = "python.exe" if is_win else "python"
    return BACKEND_DIR / ".venv" / scripts_dir / exe_name


def resolve_python() -> str:
    """Предпочитает локальный venv с uvicorn, иначе текущий интерпретатор."""
    venv_python = _venv_python()
    if venv_python.is_file():
        probe = subprocess.run(
            [str(venv_python), "-c", "import uvicorn"],
            capture_output=True,
            text=True,
        )
        if probe.returncode == 0:
            return str(venv_python)
    return sys.executable


def build_backend_cmd(python: str, host: str, port: int, reload: bool) -> list[str]:
    cmd = [
        python,
        "-m",
        "uvicorn",
        "src.api.server:app",
        "--host",
        host,
        "--port",
        str(port),
    ]
    if reload:
        cmd.append("--reload")
    return cmd


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="run.py",
        description="Запуск EduTutor: backend (uvicorn) и, опционально, frontend (Vite).",
    )
    parser.add_argument(
        "--frontend", "-f", action="store_true",
        help="Также запустить Vite dev server (frontend).",
    )
    parser.add_argument(
        "--host", default="127.0.0.1",
        help="Хост backend (по умолчанию 127.0.0.1).",
    )
    parser.add_argument(
        "--port", type=int, default=8000,
        help="Порт backend (по умолчанию 8000).",
    )
    parser.add_argument(
        "--reload", action="store_true",
        help="Авто-перезапуск uvicorn при изменении кода (dev).",
    )
    args = parser.parse_args(argv)

    python = resolve_python()
    backend_url = f"http://{args.host}:{args.port}"

    print(f"[run.py] Запуск backend через: {python}")
    print(f"[run.py] Backend будет доступен на {backend_url} (cwd={BACKEND_DIR})")
    backend = subprocess.Popen(
        build_backend_cmd(python, args.host, args.port, args.reload),
        cwd=str(BACKEND_DIR),
    )

    frontend: subprocess.Popen | None = None
    try:
        if args.frontend:
            npm = shutil.which("npm")
            if npm is None:
                print("[run.py] npm не найден в PATH — frontend не запущен", file=sys.stderr)
            else:
                print(f"[run.py] Запуск frontend: {npm} run dev (cwd={FRONTEND_DIR})")
                print(f"[run.py] Frontend будет доступен на {FRONTEND_URL}")
                frontend = subprocess.Popen([npm, "run", "dev"], cwd=str(FRONTEND_DIR))
        backend.wait()
        return backend.returncode
    except KeyboardInterrupt:
        print("\n[run.py] Получен Ctrl+C, остановка...")
        return 0
    finally:
        for proc, name in ((frontend, "frontend"), (backend, "backend")):
            if proc is not None and proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    print(f"[run.py] {name} принудительно остановлен", file=sys.stderr)


if __name__ == "__main__":
    sys.exit(main())
