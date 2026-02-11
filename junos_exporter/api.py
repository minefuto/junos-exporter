import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Request, Response, status
from fastapi.responses import PlainTextResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from junos_exporter.config import Config, logger
from junos_exporter.connector import ConnecterBuilder, Connector
from junos_exporter.exporter import Exporter, ExporterBuilder


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    config = Config()
    app.state.timeout = config.timeout
    app.state.exporter = ExporterBuilder(config)
    app.state.connector = ConnecterBuilder(config)
    yield


app = FastAPI(title="junos-exporter", lifespan=lifespan)


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(
    request: Request, exc: StarletteHTTPException
) -> PlainTextResponse:
    return PlainTextResponse(content=str(exc.detail), status_code=exc.status_code)


async def get_connector(
    target: str, credential: str = "default"
) -> AsyncGenerator[None, None]:
    async with app.state.connector.build(target, credential) as connector:
        yield connector


@app.get("/metrics", tags=["exporter"], response_class=PlainTextResponse)
async def metrics(
    connector: Annotated[Connector, Depends(get_connector)], module: str = "default"
) -> str:
    exporter: Exporter = app.state.exporter.build(module)
    try:
        return await asyncio.wait_for(
            exporter.collect(connector), timeout=app.state.timeout
        )
    except asyncio.TimeoutError:
        logger.error(
            f"Request timeout(Target: {connector.target}, Timeout: {app.state.timeout})"
        )
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail=f"Request timeout(Target: {connector.target}, Timeout: {app.state.timeout})",
        ) from None


@app.get("/debug", tags=["debug"])
async def debug(
    connector: Annotated[Connector, Depends(get_connector)], optable: str
) -> Response:
    content = await connector.debug(optable)
    return Response(content=content, media_type="application/json")
