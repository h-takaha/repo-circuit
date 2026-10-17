"""紹介用デモ。個人の .env・GitHub・ローカルrepoを使わず、loopbackだけで起動する。"""

from __future__ import annotations

import argparse
import os
from pathlib import Path

from .config import Settings
from .demo import DemoSource

ROOT = Path(__file__).resolve().parent.parent


def create_demo_app(data_dir: Path | None = None):
    # app.mainの通常app生成より先に実トークンを無効化する。変更はこのprocess内だけ。
    os.environ["GITHUB_TOKEN"] = ""
    os.environ["BASIC_AUTH_USER"] = "demo"
    os.environ["BASIC_AUTH_PASSWORD"] = "demo"
    os.environ["CSRF_SECRET"] = "repo-circuit-local-demo"
    from .main import create_app

    data_dir = data_dir if data_dir is not None else ROOT / ".demo-data"
    settings = Settings(
        github_token="", owner="demo", lab_git_dir=data_dir / "unused-git",
        lab_run_dir=data_dir / "unused-run", agent_ops_dir=data_dir / "unused-ops",
        data_dir=data_dir, cache_ttl=60,
    )
    return create_app(settings, DemoSource(showcase=True))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=18017, help="デモ専用ポート（既定: 18017）")
    args = parser.parse_args()
    import uvicorn

    print(f"デモ: http://127.0.0.1:{args.port} / ユーザー名: demo / パスワード: demo", flush=True)
    uvicorn.run(create_demo_app(), host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()
