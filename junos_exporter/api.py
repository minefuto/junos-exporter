import asyncio
import json
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from importlib.metadata import version
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Request, Response, status
from fastapi.responses import PlainTextResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from junos_exporter.config import Config, logger
from junos_exporter.connector import ConnecterBuilder, Connector
from junos_exporter.errors import DeviceError, ExporterError, RpcError
from junos_exporter.exporter import Exporter, ExporterBuilder
from junos_exporter.parser import Parser


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    config = Config()
    app.state.timeout = config.timeout
    app.state.probes = config.probes
    app.state.exporter = ExporterBuilder(config)
    app.state.connector = ConnecterBuilder(config)
    yield


app = FastAPI(
    title="junos-exporter", version=version("junos-exporter"), lifespan=lifespan
)


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(
    request: Request, exc: StarletteHTTPException
) -> PlainTextResponse:
    return PlainTextResponse(content=str(exc.detail), status_code=exc.status_code)


@app.exception_handler(ExporterError)
@app.exception_handler(DeviceError)
@app.exception_handler(RpcError)
async def error_handler(request: Request, exc: Exception) -> PlainTextResponse:
    return PlainTextResponse(
        content=str(exc), status_code=status.HTTP_500_INTERNAL_SERVER_ERROR
    )


def get_connector(target: str, credential: str = "default") -> Connector:
    return app.state.connector.build(target, credential)


@app.get("/probe", tags=["exporter"], response_class=PlainTextResponse)
async def probe(
    connector: Annotated[Connector, Depends(get_connector)], module: str = "default"
) -> str:
    exporter: Exporter = app.state.exporter.build(module)
    try:
        async with connector:
            return await asyncio.wait_for(
                exporter.collect(connector), timeout=app.state.timeout
            )
    except TimeoutError:
        logger.error(
            f"Request timeout(Target: {connector.target}, Timeout: {app.state.timeout})"
        )
        return exporter.down()
    except DeviceError:
        return exporter.down()


@app.get("/debug", tags=["debug"])
async def debug(
    connector: Annotated[Connector, Depends(get_connector)],
    probe: str,
) -> Response:
    if probe not in app.state.probes:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Probe is not defined(Probe: {probe})",
        )

    definition = app.state.probes[probe]
    async with connector:
        reply = await connector.get(probe, definition)
        content = json.dumps(Parser(definition).parse(reply), indent=2)

    return Response(content=content, media_type="application/json")
