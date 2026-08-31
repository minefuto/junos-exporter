import os
import re
import sys
from collections import defaultdict
from importlib.resources import files
from logging import getLogger
from typing import Literal

import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    ValidationInfo,
    field_validator,
    model_validator,
)

logger = getLogger("uvicorn.error")

XML_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_.-]*$")


class General(BaseModel):
    prefix: str = "junos"
    timeout: int = 60
    timeout_socket: int = 15
    ssh_config: str | None = None

    @field_validator("ssh_config", mode="after")
    @classmethod
    def check_exist_file(cls, path: str) -> str:
        abs_path = os.path.abspath(os.path.expanduser(path))
        if not os.path.isfile(abs_path):
            raise ValueError(f"file({abs_path}) does not exist")
        return abs_path


class Credential(BaseModel):
    username: str
    password: str = ""
    private_key: str = ""
    private_key_passphrase: str = ""


class Module(BaseModel):
    tables: list[str]

    @field_validator("tables", mode="before")
    @classmethod
    def check_exist_tables(cls, tables: list[str], info: ValidationInfo) -> list[str]:
        if isinstance(info.context, dict):
            defined = info.context.get("tables", dict())
            for table in tables:
                if table not in defined:
                    raise ValueError(f"table({table}) does not contain tables")
        return tables


class Label(BaseModel):
    model_config = ConfigDict(coerce_numbers_to_str=True)

    name: str = ""
    value: str
    regex: re.Pattern | None = None

    @field_validator("regex", mode="before")
    @classmethod
    def to_re_pattern(cls, regex: str) -> re.Pattern:
        if not isinstance(regex, str):
            raise ValueError(f"regex({regex}) is not a str")
        return re.compile(regex)

    @model_validator(mode="after")
    def add_name(self) -> "Label":
        if self.name == "":
            self.name = self.value
        return self


class Metric(BaseModel):
    model_config = ConfigDict(coerce_numbers_to_str=True)

    name: str
    value: str
    type_: Literal["untyped", "counter", "gauge"] = Field("untyped", alias="type")
    help_: str = Field("", alias="help")
    regex: re.Pattern | None = None
    value_transform: defaultdict[str | bool, float] | None = None
    to_unixtime: bool = False

    @field_validator("regex", mode="before")
    @classmethod
    def to_re_pattern(cls, regex: str) -> re.Pattern:
        if not isinstance(regex, str):
            raise ValueError(f"regex({regex}) is not a str")
        return re.compile(regex)

    @field_validator("value_transform", mode="before")
    @classmethod
    def to_defaultdict(cls, value_transform: dict) -> dict:
        if default := value_transform.get("_"):
            return defaultdict(lambda: float(default), value_transform)
        return defaultdict(lambda: float("NaN"), value_transform)


class FieldSpec(BaseModel):
    model_config = ConfigDict(coerce_numbers_to_str=True)

    path: str
    exists: bool = False


class Table(BaseModel):
    rpc: str
    args: dict[str, str | bool] = Field(default_factory=dict)
    container: str = ""
    item: list[str]
    recursive: bool = False
    fields_: dict[str, list[FieldSpec]] = Field(default_factory=dict, alias="fields")
    metrics: list[Metric] = Field(default_factory=list)
    labels: list[Label] = Field(default_factory=list)

    @field_validator("rpc", mode="after")
    @classmethod
    def check_xml_name(cls, rpc: str) -> str:
        if not XML_NAME.match(rpc):
            raise ValueError(f"rpc({rpc}) is not a valid xml element name")
        return rpc

    @field_validator("args", mode="after")
    @classmethod
    def check_xml_names(cls, args: dict[str, str | bool]) -> dict[str, str | bool]:
        for arg in args:
            if not XML_NAME.match(arg.replace("_", "-")):
                raise ValueError(f"arg({arg}) is not a valid xml element name")
        return args

    @field_validator("item", mode="before")
    @classmethod
    def to_list(cls, item: str | list[str]) -> list[str]:
        return [item] if isinstance(item, str) else item

    @field_validator("fields_", mode="before")
    @classmethod
    def to_specs(cls, fields: dict) -> dict:
        specs = {}
        for name, spec in fields.items():
            candidates = [spec] if isinstance(spec, str | dict) else spec
            specs[name] = [{"path": c} if isinstance(c, str) else c for c in candidates]
        return specs


class Config:
    def __init__(self) -> None:
        config = {}

        config_location = [
            "config.yml",
            os.path.expanduser("~/.junos-exporter/config.yml"),
            str(files("junos_exporter").joinpath("config.yml")),
        ]
        for c in config_location:
            if os.path.isfile(c):
                try:
                    with open(c) as f:
                        config = yaml.safe_load(f)
                except yaml.YAMLError as e:
                    sys.exit(f"failed to load config file.\n{e}")

        if not config:
            sys.exit(
                "config file(./config.yml or ~/.junos-exporter/config.yml) is not found."
            )

        try:
            self.general = General(**config["general"])
            self.credentials = {
                name: Credential.model_validate(credential)
                for name, credential in config["credentials"].items()
            }
            self.modules = {
                name: Module.model_validate(
                    module, context={"tables": config["tables"]}
                )
                for name, module in config["modules"].items()
            }
            self.tables = {
                name: Table(**table) for name, table in config["tables"].items()
            }
        except ValidationError as e:
            sys.exit(f"failed to load config file.\n{e}")
        except KeyError as e:
            sys.exit(f"failed to load config file.\nsection({e}) is not found.")

    @property
    def prefix(self) -> str:
        return self.general.prefix

    @property
    def timeout(self) -> int:
        return self.general.timeout

    @property
    def timeout_socket(self) -> int:
        return self.general.timeout_socket

    @property
    def ssh_config(self) -> str | None:
        return self.general.ssh_config
