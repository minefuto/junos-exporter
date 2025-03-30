import os
import re
from glob import glob

import yaml
from fastapi import HTTPException, status
from jnpr.junos import Device
from jnpr.junos.exception import ConnectError
from jnpr.junos.factory import loadyaml
from jnpr.junos.factory.cmdtable import CMDTable
from jnpr.junos.factory.optable import OpTable

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

from .config import Config, Credential, logger


class Connector:
    def __init__(
        self,
        host: str,
        credential: Credential,
        textfsm_dir: str | None,
        ssh_config: str | None,
    ) -> None:
        self.device: Device = None
        self.host: str = host
        self.username: str = credential.username
        self.password: str = credential.password
        self.textfsm_dir: str | None = textfsm_dir
        self.ssh_config: str | None = ssh_config

    def __enter__(self) -> "Connector":
        try:
            logger.info(f"Start to open netconf connection(target: {self.host})")
            if self.ssh_config is not None:
                self.device = Device(
                    host=self.host,
                    user=self.username,
                    password=self.password,
                    gather_fasts=False,
                    ssh_config=self.ssh_config,
                ).open()
            else:
                self.device = Device(
                    host=self.host,
                    user=self.username,
                    password=self.password,
                    gather_fasts=False,
                ).open()
            logger.info(f"Completed to open netconf connection(target: {self.host})")
        except ConnectError as err:
            logger.error(
                f"Could not open netconf connection(target: {self.host}, error: {err})"
            )
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Could not open netconf connection(target: {self.host}, error: {err})",
            )
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        try:
            self.device.close()
            logger.info(f"Closed netconf connection(target: {self.host})")
        except Exception as err:
            logger.error(f"Cannot close netconf connection(target: {self.host}, error: {err})")

    def _get(self, name: str) -> CMDTable | OpTable | None:
        if issubclass(globals()[name], CMDTable):
            if self.textfsm_dir is None:
                table = globals()[name](self.device)
            else:
                table = globals()[name](self.device, template_dir=self.textfsm_dir)
        elif issubclass(globals()[name], OpTable):
            table = globals()[name](self.device)
        else:
            raise NotImplementedError

        try:
            logger.info(
                f"Start to get table items(target: {self.host}, table: {name})"
            )
            table.get()
            logger.info(
                f"Completed to get table items(target: {self.host}, table: {name})"
            )
            return table
        except AttributeError:
            # https://github.com/Juniper/py-junos-eznc/issues/1366
            logger.debug(
                f"Could not get table items(target: {self.host}, table: {name}, error: junos output is empty"
            )
            return None
        except Exception as err:
            logger.error(
                f"Could not get table items(target: {self.host}, table: {name}, error: {err})"
            )
            return None

    def collect(self, name: str) -> list[dict]:
        table = self._get(name)
        if table is None:
            return []

        items = []
        if isinstance(table, CMDTable):
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
        elif isinstance(table, OpTable):
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
        else:
            raise NotImplementedError

        return items

    def debug(self, name: str) -> list[dict]:
        table = self._get(name)
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
                f"Could not build Connector(target: {host}, credential: {credential_name}, error: credential is not defined)"
            )
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Could not build Connector(target: {host}, credential: {credential_name}, error: credential is not defined)",
            )
        return Connector(
            host=host,
            credential=self.credentials[credential_name],
            textfsm_dir=self.textfsm_dir,
            ssh_config=self.ssh_config,
        )
