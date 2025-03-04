import argparse
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Generator

import uvicorn
from fastapi import Depends, FastAPI
from fastapi.responses import PlainTextResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from .config import Config
from .connector import ConnecterBuilder, Connector
from .exporter import Exporter, ExporterBuilder

config = Config()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    app.exporter = ExporterBuilder(config)  # type: ignore
    app.connector = ConnecterBuilder(config)  # type: ignore
    yield


app = FastAPI(
    title="junos-exporter", default_response_class=PlainTextResponse, lifespan=lifespan
)


@app.exception_handler(StarletteHTTPException)
def http_exception_handler(request, exc) -> PlainTextResponse:
    return PlainTextResponse(content=str(exc.detail), status_code=exc.status_code)


def get_connector(target: str, module: str) -> Generator[Connector, None, None]:
    with app.connector.build(target, module) as connector:  # type: ignore
        yield connector


@app.get("/metrics", tags=["metrics"])
def collect(
    target: str, module: str, connector: Connector = Depends(get_connector)
) -> str:
    exporter: Exporter = app.exporter.build(module)  # type: ignore
    return exporter.collect(connector)


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
        "junos_exporter.cli:app",
        host="0.0.0.0",
        port=args.port,
        workers=args.workers,
        log_config="log_config.yml",
    )
