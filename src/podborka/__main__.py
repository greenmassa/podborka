"""Запуск: `python -m podborka serve [--demo]` или `python -m podborka seed`."""

from __future__ import annotations

import argparse
import os

from podborka.db import Database


def main() -> None:
    parser = argparse.ArgumentParser(prog="podborka")
    parser.add_argument("--db", default=os.environ.get("PODBORKA_DB", "podborka.db"))
    sub = parser.add_subparsers(dest="cmd", required=True)
    serve = sub.add_parser("serve", help="запустить веб-интерфейс")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    serve.add_argument("--demo", action="store_true", help="демо-сборщики вместо реальных API")
    sub.add_parser("seed", help="наполнить базу демо-данными")
    args = parser.parse_args()

    db = Database(args.db)
    if args.cmd == "seed":
        from podborka.demo import seed

        seed(db)
        print(f"Демо-данные записаны в {args.db}")
    elif args.cmd == "serve":
        import uvicorn

        from podborka.collectors.demo import demo_collectors
        from podborka.web.app import create_app

        # Реальные сборщики подключаются на следующих шагах.
        collectors = demo_collectors() if args.demo else []
        uvicorn.run(create_app(db, collectors), host=args.host, port=args.port)


if __name__ == "__main__":
    main()
