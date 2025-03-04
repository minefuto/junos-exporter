from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Generator

from fastapi import Depends, FastAPI, Response
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


app = FastAPI(title="junos-exporter", lifespan=lifespan)


@app.exception_handler(StarletteHTTPException)
def http_exception_handler(request, exc) -> PlainTextResponse:
    return PlainTextResponse(content=str(exc.detail), status_code=exc.status_code)


def get_connector(target: str, credential: str) -> Generator[Connector, None, None]:
    with app.connector.build(target, credential) as connector:  # type: ignore
        yield connector


@app.get("/metrics", tags=["exporter"], response_class=PlainTextResponse)
def metrics(module: str, connector: Connector = Depends(get_connector)) -> str:
    exporter: Exporter = app.exporter.build(module)  # type: ignore
    return exporter.collect(connector)


@app.get("/debug", tags=["debug"])
def debug(optable: str, connector: Connector = Depends(get_connector)) -> Response:
    return Response(content=connector.debug(optable), media_type="application/json")
