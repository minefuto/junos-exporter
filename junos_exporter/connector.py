import os
from logging import NullHandler, getLogger
from types import TracebackType
from xml.sax.saxutils import escape

import pygxml
from scrapli.auth import Options as AuthOptions
from scrapli.exceptions import ScrapliException
from scrapli.netconf import Netconf
from scrapli.session import Options as SessionOptions
from scrapli.transport import Ssh2Options

from junos_exporter.config import Config, Credential, Probe, Query, logger
from junos_exporter.errors import DeviceError, ExporterError, RpcError

NETCONF_PORT = 830

# libscrapli repeats every failure on its own logger, which would otherwise
# reach stderr unformatted through logging.lastResort
getLogger("scrapli").addHandler(NullHandler())


def _localname(tag: str) -> str:
    return tag.rpartition(":")[2]


def _private_key(credential: Credential) -> str | None:
    if not credential.private_key:
        return None
    return os.path.expanduser(credential.private_key)


class Connector:
    def __init__(
        self,
        target: str,
        backup_connections: list[str],
        credential: Credential,
        jump_credential: Credential | None,
        timeout_socket: int,
        timeout: int,
    ) -> None:
        self.target = target
        self.backup_connections = backup_connections
        self.credential = credential
        self.jump_credential = jump_credential
        self.timeout_socket = timeout_socket
        self.timeout_ns = timeout * 1_000_000_000
        self.host = target
        self.conn = self._build(target)

    def _build(self, host: str) -> Netconf:
        # open_async() cannot take a per-call timeout before the session
        # exists, so the session default bounds the open and each rpc passes
        # its own
        session_options = SessionOptions(operation_timeout_s=self.timeout_socket)
        jump = self.credential.proxy_jump
        if jump is None or self.jump_credential is None:
            return Netconf(
                host,
                port=NETCONF_PORT,
                auth_options=AuthOptions(
                    username=self.credential.username,
                    password=self.credential.password or None,
                    private_key_path=_private_key(self.credential),
                    private_key_passphrase=self.credential.private_key_passphrase
                    or None,
                ),
                session_options=session_options,
                transport_options=Ssh2Options(),
            )

        # ssh2 takes the jump host as the primary session and the device as
        # the proxy jump target
        return Netconf(
            jump.host,
            port=jump.port,
            auth_options=AuthOptions(
                username=self.jump_credential.username,
                password=self.jump_credential.password or None,
                private_key_path=_private_key(self.jump_credential),
                private_key_passphrase=self.jump_credential.private_key_passphrase
                or None,
            ),
            session_options=session_options,
            transport_options=Ssh2Options(
                proxy_jump_host=host,
                proxy_jump_port=NETCONF_PORT,
                proxy_jump_username=self.credential.username,
                proxy_jump_password=self.credential.password or None,
                proxy_jump_private_key_path=_private_key(self.credential),
                proxy_jump_private_key_passphrase=self.credential.private_key_passphrase
                or None,
            ),
        )

    async def open(self) -> "None":
        while True:
            try:
                await self.conn.open_async()
                return
            except ScrapliException as err:
                logger.error(
                    f"Could not open netconf connection(Target: {self.host}, {err.__class__.__name__}: {err})"
                )
                if not self.backup_connections:
                    raise DeviceError(
                        f"Could not open netconf connection(Target: {self.host}, {err.__class__.__name__}: {err})"
                    ) from None

            self.host = self.backup_connections.pop(0)
            self.conn = self._build(self.host)
            logger.info(
                f"Try to fallback to the backup connection(Target: {self.target}, Connection: {self.host})"
            )

    async def __aenter__(self) -> "Connector":
        logger.debug(
            f"Start to open netconf connection(Target: {self.target}, Connection: {self.host})"
        )
        await self.open()
        logger.debug(
            f"Completed to open netconf connection(Target: {self.target}, Connection: {self.host})"
        )
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        # a session that already failed cannot answer close-session
        for force in (True,) if exc_type else (False, True):
            try:
                await self.conn.close_async(force=force)
                break
            except ScrapliException as err:
                logger.debug(
                    f"Could not close netconf connection(Target: {self.target}, Connection: {self.host}, {err.__class__.__name__}: {err})"
                )
        logger.debug(
            f"Closed netconf connection(Target: {self.target}, Connection: {self.host})"
        )

    async def _get_rpc(self, filter_: str) -> pygxml.Result:
        result = await self.conn.raw_rpc_async(
            filter_, operation_timeout_ns=self.timeout_ns
        )
        reply = pygxml.parse(result.result).get("rpc-reply")
        if not reply.exists():
            raise RpcError("rpc-reply is not found")
        if reply.type_ is not dict:
            raise RpcError("rpc-reply is empty")

        name, element = next(iter(reply.children()))
        if _localname(name) == "rpc-error":
            message = element.get("error-message")
            raise RpcError(message.to_str() or "unknown rpc error")
        return element

    async def get(self, name: str, probe: Probe | Query) -> pygxml.Result:
        args = []
        for arg, value in probe.args.items():
            if value is False:
                continue
            tag = arg.replace("_", "-")
            if value is True:
                args.append(f"<{tag}/>")
            else:
                args.append(f"<{tag}>{escape(str(value))}</{tag}>")
        rpc = f'<{probe.rpc} format="xml-minified">{"".join(args)}</{probe.rpc}>'

        logger.debug(f"Start to get rpc reply(Target: {self.target}, Probe: {name})")
        try:
            reply = await self._get_rpc(rpc)
        except ScrapliException as err:
            logger.error(
                f"Lost netconf connection(Target: {self.target}, Probe: {name}, {err.__class__.__name__}: {err})"
            )
            raise DeviceError(
                f"Lost netconf connection(Target: {self.target}, Probe: {name}, {err.__class__.__name__}: {err})"
            ) from None
        logger.debug(
            f"Completed to get rpc reply(Target: {self.target}, Probe: {name})"
        )
        return reply


class ConnecterBuilder:
    def __init__(self, config: Config) -> None:
        self.credentials: dict[str, Credential] = config.credentials
        self.timeout_socket: int = config.timeout_socket
        self.timeout: int = config.timeout

    def build(self, target_text: str, credential_name: str) -> Connector:
        targets = target_text.split(",")
        if len(targets) == 1:
            target = targets[0]
            backup_connections = []
        else:
            target = targets[0]
            backup_connections = [t for t in targets[1:] if t != ""]

        if credential_name not in self.credentials:
            logger.error(
                f"Could not build Connector(Target: {target}, Credential: {credential_name}, Error: credential is not defined)"
            )
            raise ExporterError(
                f"Could not build Connector(Target: {target}, Credential: {credential_name}, Error: credential is not defined)"
            )
        credential = self.credentials[credential_name]
        return Connector(
            target=target,
            backup_connections=backup_connections,
            credential=credential,
            jump_credential=self.credentials[credential.proxy_jump.credential]
            if credential.proxy_jump
            else None,
            timeout_socket=self.timeout_socket,
            timeout=self.timeout,
        )
