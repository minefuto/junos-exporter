import argparse

import uvicorn


def cli() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--log-level",
        type=str,
        choices=["critical", "error", "warning", "info", "debug", "trace"],
        default="info",
        help="log level[default: info]",
    )
    parser.add_argument(
        "--no-access-log", action="store_false", help="disable access log"
    )
    parser.add_argument(
        "--port",
        type=int,
        default=9326,
        help="listening port[default: 9326]",
    )
    parser.add_argument(
        "--root-path",
        type=str,
        default="",
        help='root path[default: ""]',
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=1,
        help="number of worker processes[default: 1]",
    )
    args = parser.parse_args()
    uvicorn.run(
        "junos_exporter.api:app",
        host="0.0.0.0",
        port=args.port,
        workers=args.workers,
        log_config="log_config.yml",
        log_level=args.log_level,
        root_path=args.root_path,
        access_log=args.no_access_log,
    )


if __name__ == "__main__":
    cli()
