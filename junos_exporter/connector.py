import socket
from types import TracebackType
from xml.sax.saxutils import escape

import pygxml
from asyncssh.pbe import KeyEncryptionError
from asyncssh.public_key import KeyImportError
from fastapi import HTTPException, status
from scrapli.exceptions import ScrapliAuthenticationFailed, ScrapliConnectionNotOpened
from scrapli_netconf import AsyncNetconfDriver
from scrapli_netconf.constants import NetconfVersion

from junos_exporter.config import Config, Credential, Table, logger

NEW_LINE = 10
CHUNK_MARKER = 35
END_OF_MESSAGE = b"]]>]]>"


def _localname(tag: str) -> str:
    return tag.rpartition(":")[2]


class RpcError(Exception):
    def __init__(self, err: str) -> None:
        self.err = err

    def __str__(self) -> str:
        return f"{self.err}"


def _deframe(raw: bytes, netconf_version: NetconfVersion) -> bytes:
    if netconf_version == NetconfVersion.VERSION_1_0:
        return raw.replace(END_OF_MESSAGE, b"")

    data = raw.strip()
    end = len(data)
    chunks = []
    cursor = 0
    while cursor < end:
        if data[cursor] == NEW_LINE:
            cursor += 1
            continue
        if data[cursor] != CHUNK_MARKER:
            raise RpcError("chunk marker is not found")
        cursor += 1
        if cursor >= end or data[cursor] == CHUNK_MARKER:
            break

        marker = data.find(b"\n", cursor, cursor + 11)
        if marker == -1:
            raise RpcError("chunk size is not found")
        try:
            size = int(data[cursor:marker])
        except ValueError:
            raise RpcError("chunk size is not a number") from None
        if size <= 0:
            raise RpcError("chunk size is not positive")

        cursor = marker + 1
        chunks.append(data[cursor : cursor + size])
        cursor += size
    return b"".join(chunks)


class Connector:
    def __init__(
        self,
        target: str,
        backup_connections: list[str],
        credential: Credential,
        ssh_config: str | None,
        timeout_socket: int,
    ) -> None:
        self.target = target
        self.backup_connections = backup_connections

        transport_options: dict = {"asyncssh": {}}
        if credential.private_key:
            transport_options["asyncssh"]["client_keys"] = credential.private_key

        if credential.private_key_passphrase:
            transport_options["asyncssh"]["passphrase"] = (
                credential.private_key_passphrase
            )

        self.transport_options = transport_options

        self.conn: AsyncNetconfDriver = AsyncNetconfDriver(
            host=self.target,
            auth_username=credential.username,
            auth_password=credential.password,
            auth_strict_key=False,
            ssh_config_file=True if ssh_config is None else ssh_config,
            transport="asyncssh",
            transport_options=transport_options,
            timeout_socket=timeout_socket,
        )

    async def open(self) -> "None":
        try:
            await self.conn.open()
        except (
            ScrapliConnectionNotOpened,
            KeyImportError,
            KeyEncryptionError,
            socket.gaierror,
        ) as err:
            logger.error(
                f"Could not open netconf connection(Target: {self.target}, {err.__class__.__name__}: {err})"
            )
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Could not open netconf connection(Target: {self.target}, {err.__class__.__name__}: {err})",
            ) from None
        except (OSError, ScrapliAuthenticationFailed) as err:
            is_try_backup = True
            if not self.backup_connections or (
                isinstance(err, ScrapliAuthenticationFailed)
                and str(err) != "timed out opening connection to device"
            ):
                is_try_backup = False

            if not is_try_backup:
                logger.error(
                    f"Could not open netconf connection(Target: {self.conn.host}, {err.__class__.__name__}: {err})"
                )
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail=f"Could not open netconf connection(Target: {self.conn.host}, {err.__class__.__name__}: {err})",
                ) from None

            self.conn = AsyncNetconfDriver(
                host=self.backup_connections.pop(0),
                auth_username=self.conn.auth_username,
                auth_password=self.conn.auth_password,
                auth_strict_key=self.conn.auth_strict_key,
                ssh_config_file=self.conn.ssh_config_file,
                transport="asyncssh",
                transport_options=self.transport_options,
                timeout_socket=self.conn.timeout_socket,
            )
            logger.info(
                f"Try to fallback to the backup connection(Target: {self.target}, Connection: {self.conn.host})"
            )
            await self.open()

    async def __aenter__(self) -> "Connector":
        logger.debug(
            f"Start to open netconf connection(Target: {self.target}, Connection: {self.conn.host})"
        )
        await self.open()
        logger.debug(
            f"Completed to open netconf connection(Target: {self.target}, Connection: {self.conn.host})"
        )
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        await self.conn.close()
        logger.debug(
            f"Closed netconf connection(Target: {self.target}, Connection: {self.conn.host})"
        )

    async def _get_rpc(self, filter_: str) -> pygxml.Result:
        request = self.conn._pre_rpc(filter_)
        raw = await self.conn.channel.send_input_netconf(request.channel_input)
        reply = pygxml.parse(_deframe(raw, self.conn.netconf_version)).get("rpc-reply")
        if not reply.exists():
            raise RpcError("rpc-reply is not found")
        if reply.type_ is not dict:
            raise RpcError("rpc-reply is empty")

        name, element = next(iter(reply.children()))
        if _localname(name) == "rpc-error":
            message = element.get("error-message")
            raise RpcError(message.to_str() or "unknown rpc error")
        return element

    async def get(self, name: str, table: Table) -> pygxml.Result | None:
        """Sends the table's rpc and returns the reply element.

        The result borrows the response buffer, so it keeps that buffer alive
        for as long as the caller holds on to it.
        """
        args = []
        for arg, value in table.args.items():
            if value is False:
                continue
            tag = arg.replace("_", "-")
            if value is True:
                args.append(f"<{tag}/>")
            else:
                args.append(f"<{tag}>{escape(str(value))}</{tag}>")
        rpc = f'<{table.rpc} format="xml-minified">{"".join(args)}</{table.rpc}>'

        logger.debug(f"Start to get rpc reply(Target: {self.target}, Table: {name})")
        try:
            reply = await self._get_rpc(rpc)
        except RpcError as err:
            logger.error(
                f"Could not get rpc reply(Target: {self.target}, Table: {name}, RpcError: {err})"
            )
            return None
        logger.debug(
            f"Completed to get rpc reply(Target: {self.target}, Table: {name})"
        )
        return reply


class ConnecterBuilder:
    def __init__(self, config: Config) -> None:
        self.credentials: dict[str, Credential] = config.credentials
        self.ssh_config: str | None = config.ssh_config
        self.timeout_socket: int = config.timeout_socket

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
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Could not build Connector(Target: {target}, Credential: {credential_name}, Error: credential is not defined)",
            )
        return Connector(
            target=target,
            backup_connections=backup_connections,
            credential=self.credentials[credential_name],
            ssh_config=self.ssh_config,
            timeout_socket=self.timeout_socket,
        )
