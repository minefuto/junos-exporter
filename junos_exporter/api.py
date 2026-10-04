import asyncio
import json
import re
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import datetime
from importlib.metadata import version
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Request, Response, status
from fastapi.responses import JSONResponse, PlainTextResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from junos_exporter.config import Config, Query, QueryField, logger
from junos_exporter.connector import ConnecterBuilder, Connector
from junos_exporter.errors import DeviceError, ExporterError, RpcError
from junos_exporter.exporter import Exporter, ExporterBuilder
from junos_exporter.parser import Parser

ARG = re.compile(r"args\[(.+)\]")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    config = Config()
    app.state.timeout = config.timeout
    app.state.probes = config.probes
    app.state.queries = {
        name: (query, Parser(query)) for name, query in config.queries.items()
    }
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


def resolve_args(
    defined: dict[str, str | bool | None], given: dict[str, str]
) -> dict[str, str | bool]:
    args: dict[str, str | bool] = {}
    for key, default in defined.items():
        value = given.get(key, default)
        if value is None:
            raise ValueError(f"args({key}) is required")
        if isinstance(default, bool) and isinstance(value, str):
            if value not in ("true", "false"):
                raise ValueError(f"args({key}) must be true or false")
            value = value == "true"
        if value != "":
            args[key] = value
    return args


def to_fields(record: dict[str, str], fields: list[QueryField]) -> dict[str, str]:
    result = {}
    for field in fields:
        if field.key not in record:
            continue
        value = record[field.key]
        if field.regex is not None:
            match = field.regex.match(value)
            if match is None:
                continue
            value = match.group(1) if field.regex.groups else match.group()
        result[field.name] = value
    return result


@app.get("/query", tags=["query"])
async def query(
    request: Request, target: str, queries: str, credential: str = "default"
) -> JSONResponse:
    def error(status_code: int, errors: dict[str, str]) -> JSONResponse:
        return JSONResponse(
            {"target": target, "errors": errors}, status_code=status_code
        )

    defined: dict[str, tuple[Query, Parser]] = app.state.queries
    names = list(dict.fromkeys(n for n in queries.split(",") if n))
    given = {
        m[1]: v for k, v in request.query_params.items() if (m := ARG.fullmatch(k))
    }

    errors: dict[str, str] = {}
    if not names:
        errors["_request"] = "queries is empty"
    known = {k for n in names if n in defined for k in defined[n][0].args}
    if unknown := sorted(given.keys() - known):
        errors["_request"] = f"args({', '.join(unknown)}) is not defined in the queries"
    resolved = {}
    for name in names:
        if name not in defined:
            errors[name] = "query is not defined"
            continue
        try:
            resolved[name] = resolve_args(defined[name][0].args, given)
        except ValueError as e:
            errors[name] = str(e)
    if errors:
        return error(status.HTTP_400_BAD_REQUEST, errors)

    try:
        connector: Connector = app.state.connector.build(target, credential)
    except ExporterError as e:
        return error(status.HTTP_400_BAD_REQUEST, {"_request": str(e)})

    async def run() -> dict[str, list[dict[str, str]]]:
        results = {}
        for name in names:
            query, parser = defined[name]
            try:
                reply = await connector.get(
                    name, query.model_copy(update={"args": resolved[name]})
                )
            except RpcError as e:
                logger.error(
                    f"Could not get rpc reply(Target: {connector.target}, Query: {name}, RpcError: {e})"
                )
                errors[name] = str(e)
                continue
            if not errors:
                records = (to_fields(r, query.fields) for r in parser.parse(reply))
                results[name] = [r for r in records if r]
        return results

    collected_at = datetime.now().astimezone().isoformat(timespec="seconds")
    try:
        async with connector:
            results = await asyncio.wait_for(run(), timeout=app.state.timeout)
    except TimeoutError:
        logger.error(
            f"Request timeout(Target: {connector.target}, Timeout: {app.state.timeout})"
        )
        return error(
            status.HTTP_504_GATEWAY_TIMEOUT,
            {"_target": f"Request timeout(Timeout: {app.state.timeout})"},
        )
    except DeviceError as e:
        return error(status.HTTP_502_BAD_GATEWAY, {"_target": str(e)})
    if errors:
        return error(status.HTTP_502_BAD_GATEWAY, errors)

    return JSONResponse(
        {"target": target, "collected_at": collected_at, "queries": results}
    )
