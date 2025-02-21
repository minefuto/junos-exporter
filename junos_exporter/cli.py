import argparse
import os
import sys
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Generator

import uvicorn
from fastapi import Depends, FastAPI
from fastapi.responses import PlainTextResponse
from pydantic import ValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

from .config import Config
from .connector import ConnecterBuilder, Connector
from .exporter import Exporter, ExporterBuilder


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    config = Config.get()
    app.exporter = ExporterBuilder(config)  # type: ignore
    app.connector = ConnecterBuilder(config)  # type: ignore
    yield


app = FastAPI(default_response_class=PlainTextResponse, lifespan=lifespan)


@app.exception_handler(StarletteHTTPException)
def http_exception_handler(request, exc) -> PlainTextResponse:
    return PlainTextResponse(content=str(exc.detail), status_code=exc.status_code)


def get_connector(target: str, module: str) -> Generator[Connector, None, None]:
    with app.connector.build(target, module) as connector:  # type: ignore
        yield connector


@app.get("/metrics")
def collect(
    target: str, module: str, connector: Connector = Depends(get_connector)
) -> str:
    exporter: Exporter = app.exporter.build(module)  # type: ignore
    return exporter.collect(connector)


def cli() -> None:
    try:
        parser = argparse.ArgumentParser()
        parser.add_argument(
            "-c",
            "--config",
            type=str,
            default="config.yml",
            help="configuration file path",
        )
        args = parser.parse_args()
        config = Config(args.config)
        uvicorn.run("junos_exporter.cli:app", host="0.0.0.0", port=config.port)
    except FileNotFoundError:
        sys.exit(f"config file({os.path.abspath(args.config)}) is not found")
    except ValidationError as e:
        sys.exit(f"config file({os.path.abspath(args.config)}) is invalid.\n{e}")
