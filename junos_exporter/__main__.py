import argparse

import uvicorn


def cli() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "-p",
        "--port",
        type=int,
        default=9326,
        help="listening port[default: 9326]",
    )
    parser.add_argument(
        "-w",
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
    )


if __name__ == "__main__":
    cli()
