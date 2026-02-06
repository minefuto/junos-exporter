import os
import re
import socket
from glob import glob
from importlib.resources import files

import yaml
from asyncssh.pbe import KeyEncryptionError
from asyncssh.public_key import KeyImportError
from fastapi import HTTPException, status
from jnpr.junos.factory import loadyaml
from jnpr.junos.factory.cmdtable import CMDTable
from jnpr.junos.factory.optable import OpTable
from jnpr.junos.factory.state_machine import StateMachine
from jnpr.junos.jxml import remove_namespaces_and_spaces
from lxml import etree
from scrapli.exceptions import ScrapliAuthenticationFailed, ScrapliConnectionNotOpened
from scrapli_netconf import AsyncNetconfDriver
from textfsm.parser import TextFSMTemplateError

from .config import Config, Credential, logger


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
        textfsm_dir: str | None,
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
        self.textfsm_dir: str | None = textfsm_dir

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
            )
        except (OSError, ScrapliAuthenticationFailed) as err:
            is_try_backup = True
            if not self.backup_connections:
                is_try_backup = False
            elif isinstance(err, ScrapliAuthenticationFailed):
                if not str(err) == "timed out opening connection to device":
                    is_try_backup = False

            if not is_try_backup:
                logger.error(
                    f"Could not open netconf connection(Target: {self.conn.host}, {err.__class__.__name__}: {err})"
                )
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail=f"Could not open netconf connection(Target: {self.conn.host}, {err.__class__.__name__}: {err})",
                )

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

    async def __aexit__(self, exc_type, exc_value, traceback) -> None:
        await self.conn.close()
        logger.debug(
            f"Closed netconf connection(Target: {self.target}, Connection: {self.conn.host})"
        )

    async def _get_rpc(self, filter_: str) -> etree._Element:
        rpc = await self.conn.rpc(filter_=filter_)
        xml = rpc.xml_result
        if len(xml) == 0:
            raise RpcError("rpc-reply is empty")

        if re.match(r"\{.*\}rpc-reply$", xml.tag):
            if not re.match(r"\{.*\}rpc-error$", xml[0].tag):
                return xml[0]
        if err := xml.find(
            ".//{urn:ietf:params:xml:ns:netconf:base:1.0}error-message"
        ).text:
            raise RpcError(err)
        else:
            raise RpcError("unknown rpc error")

    async def _get(self, name: str) -> OpTable | CMDTable | None:
        if not globals().get(name):
            logger.error(
                f"Could not get table items(Target: {self.target}, Table: {name}, Error: OpTable is not defined)"
            )
            return None

        if issubclass(globals()[name], OpTable):
            table = globals()[name]()

            xml_rpc = etree.Element(table.GET_RPC, format="xml-minified")
            for k, v in table.GET_ARGS.items():
                if v is True:
                    etree.SubElement(xml_rpc, k)
                else:
                    etree.SubElement(xml_rpc, k.replace("_", "-")).text = v
            filter_ = etree.tostring(xml_rpc).decode()
            try:
                rpc = await self._get_rpc(filter_)
                table.xml = remove_namespaces_and_spaces(rpc)
                return table
            except RpcError as err:
                logger.error(
                    f"Could not get table items(Target: {self.target}, Table: {name}, RpcError: {err})"
                )
                return None

        elif issubclass(globals()[name], CMDTable):
            if self.textfsm_dir is None:
                table = globals()[name]()
            else:
                table = globals()[name](template_dir=self.textfsm_dir)

            filter_ = (
                f'<command format="text">{table.GET_CMD} | display xml rpc</command>'
            )
            try:
                command = await self._get_rpc(filter_)
                command[0].set("format", "text")
                rpc_command = etree.tostring(command[0]).decode()

                rpc = await self._get_rpc(rpc_command)
                table.data = rpc.text
                if table.USE_TEXTFSM:
                    table.output = table._parse_textfsm(
                        platform=table.PLATFORM, command=table.GET_CMD, raw=rpc.text
                    )
                else:
                    sm = StateMachine(table)
                    table.output = sm.parse(rpc.text.splitlines())
                return table
            except TextFSMTemplateError as err:
                logger.error(
                    f"Could not get table items(Target: {self.target}, Table: {name}, TextFSMTemplateError: {err})"
                )
                return None
            except RpcError as err:
                logger.error(
                    f"Could not get table items(Target: {self.target}, Table: {name}, RpcError: {err})"
                )
                return None
        else:
            raise NotImplementedError

    async def collect(self, name: str) -> list[dict] | None:
        logger.debug(f"Start to get table items(Target: {self.target}, Table: {name})")
        table = await self._get(name)
        if table is None:
            return None
        logger.debug(
            f"Completed to get table items(Target: {self.target}, Table: {name})"
        )

        items = []
        if isinstance(table, OpTable):
            for t in table:
                item = {}
                try:
                    if type(t.key) is tuple:
                        for i, n in enumerate(t.key):
                            item[f"key.{i}"] = n
                            item[f"name.{i}"] = n
                    else:
                        item["key"] = t.key
                        item["name"] = t.key
                except ValueError:
                    # key is not defined
                    pass

                for k, v in t.items():
                    item[k] = v
                items.append(item)
        elif isinstance(table, CMDTable):
            for t in table:
                key, table = t
                item = {}
                if type(key) is tuple:
                    for i, n in enumerate(key):
                        item[f"key.{i}"] = n
                        item[f"name.{i}"] = n
                else:
                    item["key"] = key
                    item["name"] = key

                for k, v in table.items():
                    item[k] = v
                items.append(item)
        else:
            raise NotImplementedError

        return items

    async def debug(self, name: str) -> list[dict] | None:
        if globals().get(name):
            table = await self._get(name)
        else:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"OpTable is not defined(OpTable: {name}",
            )
        if table is None:
            return None
        return table.to_json()


class ConnecterBuilder:
    def __init__(self, config: Config) -> None:
        self.optabels_dir: str | None = None
        if os.path.isdir(os.path.expanduser("~/.junos-exporter/op")):
            self.optables_dir = os.path.expanduser("~/.junos-exporter/op")
        elif os.path.isdir("./op"):
            self.optables_dir = "./op"
        else:
            self.optables_dir = str(files("junos_exporter").joinpath("op"))

        self.textfsm_dir: str | None = None
        if os.path.isdir(os.path.expanduser("~/.junos-exporter/textfsm")):
            self.textfsm_dir = os.path.abspath(
                os.path.expanduser("~/.junos-exporter/textfsm")
            )
        elif os.path.isdir("./textfsm"):
            self.textfsm_dir = os.path.abspath("./textfsm")
        else:
            self.optables_dir = str(files("junos_exporter").joinpath("textfsm"))

        self.credentials: dict[str, Credential] = config.credentials
        self.ssh_config: str | None = config.ssh_config
        self.timeout_socket: int = config.timeout_socket
        self._load_optables()

    def _load_optables(self) -> None:
        if self.optables_dir is None:
            return
        for yml in glob(f"{self.optables_dir}/*"):
            if re.match(r".+\.(yml|yaml)$", yml):
                globals().update(loadyaml(yaml.safe_load(yml)))

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
            textfsm_dir=self.textfsm_dir,
            ssh_config=self.ssh_config,
            timeout_socket=self.timeout_socket,
        )
