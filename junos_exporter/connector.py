import os
import re
from glob import glob

import yaml
from fastapi import HTTPException, status
from jnpr.junos.factory import loadyaml
from jnpr.junos.factory.cmdtable import CMDTable
from jnpr.junos.factory.optable import OpTable
from jnpr.junos.factory.state_machine import StateMachine
from jnpr.junos.jxml import remove_namespaces_and_spaces

# predefined table
from jnpr.junos.op.arp import ArpTable
from jnpr.junos.op.bfd import BfdSessionTable
from jnpr.junos.op.bgp import bgpTable
from jnpr.junos.op.ccc import CCCTable
from jnpr.junos.op.ddos import DDOSTable
from jnpr.junos.op.elsethernetswitchingtable import ElsEthernetSwitchingTable
from jnpr.junos.op.ethernetswitchingtable import EthernetSwitchingTable
from jnpr.junos.op.ethport import EthPortTable
from jnpr.junos.op.fpc import FpcHwTable, FpcInfoTable, FpcMiReHwTable, FpcMiReInfoTable
from jnpr.junos.op.idpattacks import IDPAttackTable
from jnpr.junos.op.intopticdiag import PhyPortDiagTable
from jnpr.junos.op.inventory import ModuleTable
from jnpr.junos.op.isis import IsisAdjacencyTable
from jnpr.junos.op.l2circuit import L2CircuitConnectionTable
from jnpr.junos.op.lacp import LacpPortTable
from jnpr.junos.op.ldp import LdpNeighborTable
from jnpr.junos.op.lldp import LLDPNeighborTable
from jnpr.junos.op.nd import NdTable
from jnpr.junos.op.ospf import (
    OspfInterfaceTable,
    OSPFIOStatsTable,
    OspfNeighborTable,
    OspfRoutesTable,
    OSPFStatsTable,
    ospfTable,
)
from jnpr.junos.op.pfestats import PFEStatsTrafficTable
from jnpr.junos.op.phyport import PhyPortErrorTable, PhyPortStatsTable, PhyPortTable
from jnpr.junos.op.ppm import PPMTable
from jnpr.junos.op.routes import RouteSummaryTable, RouteTable
from jnpr.junos.op.securityzone import SecurityZoneTable

# from jnpr.junos.op.systemstorage import SystemStorageTable
from jnpr.junos.op.taskmemory import TaskMemoryTable
from jnpr.junos.op.teddb import TedSummaryTable, TedTable
from jnpr.junos.op.vlan import VlanTable
from jnpr.junos.op.xcvr import XcvrTable
from lxml import etree
from scrapli.exceptions import ScrapliAuthenticationFailed, ScrapliTimeout
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
        host: str,
        credential: Credential,
        textfsm_dir: str | None,
        ssh_config: str | None,
    ) -> None:
        self.host: str = host
        self.conn: AsyncNetconfDriver = AsyncNetconfDriver(
            host=self.host,
            auth_username=credential.username,
            auth_password=credential.password,
            auth_strict_key=False,
            ssh_config_file=True if ssh_config is None else ssh_config,
            transport="asyncssh",
        )
        self.textfsm_dir: str | None = textfsm_dir

    async def __aenter__(self) -> "Connector":
        try:
            logger.debug(f"Start to open netconf connection(Target: {self.host})")
            await self.conn.open()
            logger.debug(f"Completed to open netconf connection(Target: {self.host})")
        except Exception as err:
            logger.error(
                f"Could not open netconf connection(Target: {self.host}, Error: {err})"
            )
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Could not open netconf connection(Target: {self.host}, Error: {err})",
            )
        return self

    async def __aexit__(self, exc_type, exc_value, traceback) -> None:
        try:
            await self.conn.close()
            logger.debug(f"Closed netconf connection(Target: {self.host})")
        except ScrapliAuthenticationFailed as err:
            logger.error(
                f"Cannot close netconf connection(Target: {self.host}, ScrapliAuthenticationFailed: {err})"
            )
        except ScrapliTimeout as err:
            logger.error(
                f"Cannot close netconf connection(Target: {self.host}, ScrapliTimeout: {err})"
            )
        except Exception as err:
            logger.error(
                f"Cannot close netconf connection(Target: {self.host}, Error: {err})"
            )

    async def _get_rpc(self, filter_: str) -> etree._Element:
        rpc = await self.conn.rpc(filter_=filter_)
        xml = rpc.xml_result
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
                    f"Could not get table items(Target: {self.host}, Table: {name}, RpcError: {err})"
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
                    f"Could not get table items(Target: {self.host}, Table: {name}, TextFSMTemplateError: {err})"
                )
                return None
            except Exception as err:
                logger.error(
                    f"Could not get table items(Target: {self.host}, Table: {name}, Error: {err})"
                )
                return None
        else:
            raise NotImplementedError

    async def collect(self, name: str) -> list[dict]:
        logger.debug(f"Start to get table items(Target: {self.host}, Table: {name})")
        table = await self._get(name)
        if table is None:
            logger.debug(
                f"Could not get table items(Target: {self.host}, Table: {name}, Error: table is None)"
            )
            return []
        logger.debug(
            f"Completed to get table items(Target: {self.host}, Table: {name})"
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

    async def debug(self, name: str) -> list[dict]:
        table = await self._get(name)
        if table is None:
            return []
        return table.to_json()


class ConnecterBuilder:
    def __init__(self, config: Config) -> None:
        self.optabels_dir: str | None = None
        if os.path.isdir(os.path.expanduser("~/.junos-exporter/op")):
            self.optables_dir = os.path.expanduser("~/.junos-exporter/op")
        elif os.path.isdir("./op"):
            self.optables_dir = "./op"

        self.textfsm_dir: str | None = None
        if os.path.isdir(os.path.expanduser("~/.junos-exporter/textfsm")):
            self.textfsm_dir = os.path.abspath(
                os.path.expanduser("~/.junos-exporter/textfsm")
            )
        elif os.path.isdir("./textfsm"):
            self.textfsm_dir = os.path.abspath("./textfsm")

        self.credentials: dict[str, Credential] = config.credentials
        self.ssh_config: str | None = config.ssh_config
        self._load_optables()

    def _load_optables(self) -> None:
        if self.optables_dir is None:
            return
        for yml in glob(f"{self.optables_dir}/*"):
            if re.match(r".+\.(yml|yaml)$", yml):
                globals().update(loadyaml(yaml.safe_load(yml)))

    def build(self, host: str, credential_name: str) -> Connector:
        if credential_name not in self.credentials:
            logger.error(
                f"Could not build Connector(Target: {host}, Credential: {credential_name}, Error: credential is not defined)"
            )
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Could not build Connector(Target: {host}, Credential: {credential_name}, Error: credential is not defined)",
            )
        return Connector(
            host=host,
            credential=self.credentials[credential_name],
            textfsm_dir=self.textfsm_dir,
            ssh_config=self.ssh_config,
        )
