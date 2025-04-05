import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, Response, status
from fastapi.responses import PlainTextResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from .config import Config, logger
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
async def http_exception_handler(request, exc) -> PlainTextResponse:
    return PlainTextResponse(content=str(exc.detail), status_code=exc.status_code)


async def get_connector(target: str, credential: str) -> AsyncGenerator[None, None]:
    async with app.connector.build(target, credential) as connector:  # type: ignore
        yield connector


@app.get("/metrics", tags=["exporter"], response_class=PlainTextResponse)
async def metrics(module: str, connector: Connector = Depends(get_connector)) -> str:
    exporter: Exporter = app.exporter.build(module)  # type: ignore
    try:
        return await asyncio.wait_for(
            exporter.collect(connector), timeout=config.timeout
        )
    except asyncio.TimeoutError:
        logger.error(
            f"Request timeout(Target: {connector.host}, Timeout: {config.timeout}"
        )
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail=f"Request timeout(Target: {connector.host}, Timeout: {config.timeout}",
        )


@app.get("/debug", tags=["debug"])
async def debug(
    optable: str, connector: Connector = Depends(get_connector)
) -> Response:
    content = await connector.debug(optable)
    return Response(content=content, media_type="application/json")
