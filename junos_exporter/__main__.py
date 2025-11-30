import argparse

import uvicorn

LOG_CONFIG = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "default": {
            "class": "uvicorn.logging.DefaultFormatter",
            "format": "%(asctime)s %(levelprefix)s %(message)s",
            "use_colors": None,
        },
        "access": {
            "class": "uvicorn.logging.AccessFormatter",
            "format": '%(asctime)s %(levelprefix)s %(client_addr)s - "%(request_line)s" %(status_code)s',
        },
    },
    "handlers": {
        "default": {
            "formatter": "default",
            "class": "logging.StreamHandler",
            "stream": "ext://sys.stderr",
        },
        "access": {
            "formatter": "access",
            "class": "logging.StreamHandler",
            "stream": "ext://sys.stdout",
        },
    },
    "loggers": {
        "uvicorn": {
            "handlers": ["default"],
            "level": "INFO",
            "propagate": False,
        },
        "uvicorn.error": {
            "level": "INFO",
        },
        "uvicorn.access": {
            "handlers": ["access"],
            "level": "INFO",
            "propagate": False,
        },
    },
}


def cli() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--host",
        type=str,
        default="0.0.0.0",
        help="listen address[default: 0.0.0.0]",
    )
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
        help="listen port[default: 9326]",
    )
    parser.add_argument(
        "--reload",
        action="store_true",
        help="enable auto reload",
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
        host=args.host,
        port=args.port,
        workers=args.workers,
        log_config=LOG_CONFIG,
        log_level=args.log_level,
        root_path=args.root_path,
        access_log=args.no_access_log,
        reload=args.reload,
    )


if __name__ == "__main__":
    cli()
