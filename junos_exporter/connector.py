import re
import socket
from types import TracebackType

from asyncssh.pbe import KeyEncryptionError
from asyncssh.public_key import KeyImportError
from fastapi import HTTPException, status
from lxml import etree
from scrapli.exceptions import ScrapliAuthenticationFailed, ScrapliConnectionNotOpened
from scrapli_netconf import AsyncNetconfDriver

from junos_exporter.config import Config, Credential, Table, logger


class RpcError(Exception):
    def __init__(self, err: str) -> None:
        self.err = err

    def __str__(self) -> str:
        return f"{self.err}"


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

    async def _get_rpc(self, filter_: str) -> etree._Element:
        rpc = await self.conn.rpc(filter_=filter_)
        xml = rpc.xml_result
        if len(xml) == 0:
            raise RpcError("rpc-reply is empty")

        if re.match(r"\{.*\}rpc-reply$", xml.tag) and not re.match(
            r"\{.*\}rpc-error$", xml[0].tag
        ):
            return xml[0]
        if err := xml.find(
            ".//{urn:ietf:params:xml:ns:netconf:base:1.0}error-message"
        ).text:
            raise RpcError(err)
        else:
            raise RpcError("unknown rpc error")

    async def get(self, name: str, table: Table) -> str | None:
        """Sends the table's rpc and returns the reply element as a string."""
        rpc = etree.Element(table.rpc, format="xml-minified")
        for arg, value in table.args.items():
            element = etree.SubElement(rpc, arg.replace("_", "-"))
            if value is not True:
                element.text = str(value)

        logger.debug(f"Start to get rpc reply(Target: {self.target}, Table: {name})")
        try:
            reply = await self._get_rpc(etree.tostring(rpc).decode())
        except RpcError as err:
            logger.error(
                f"Could not get rpc reply(Target: {self.target}, Table: {name}, RpcError: {err})"
            )
            return None
        logger.debug(
            f"Completed to get rpc reply(Target: {self.target}, Table: {name})"
        )
        return etree.tostring(reply).decode()


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
