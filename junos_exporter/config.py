import os
import re
import sys
from collections import defaultdict
from typing import DefaultDict, Literal

import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    ValidationInfo,
    field_validator,
)


class General(BaseModel):
    prefix: str = "junos"
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
    password: str


class Module(BaseModel):
    tables: list[str]

    @field_validator("tables", mode="before")
    @classmethod
    def check_exist_optables(cls, tables: list[str], info: ValidationInfo) -> list[str]:
        if isinstance(info.context, dict):
            optables = info.context.get("optables", dict())
            for table in tables:
                if table not in optables:
                    raise ValueError(f"table({table}) does not contain optables")
        return tables


class Label(BaseModel):
    model_config = ConfigDict(coerce_numbers_to_str=True)

    name: str
    value: str
    regex: re.Pattern | None = None

    @field_validator("regex", mode="before")
    @classmethod
    def to_re_pattern(cls, regex: str) -> re.Pattern:
        if not isinstance(regex, str):
            raise ValueError(f"regex({regex}) is not a str")
        return re.compile(regex)


class Metric(BaseModel):
    model_config = ConfigDict(coerce_numbers_to_str=True)

    name: str
    value: str
    type_: Literal["untyped", "counter", "gauge"] = Field("untyped", alias="type")
    help_: str = Field("", alias="help")
    regex: re.Pattern | None = None
    value_transform: DefaultDict[str | bool, float] | None = None
    to_unixtime: str | None = None

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
        return defaultdict(lambda: "NaN", value_transform)


class OpTable(BaseModel):
    metrics: list[Metric]
    labels: list[Label]


class Config:
    def __init__(self) -> None:
        config = {}

        config_location = [
            "config.yml",
            os.path.expanduser("~/.junos-exporter/config.yml"),
        ]
        for c in config_location:
            if os.path.isfile(c):
                try:
                    with open(c, "r") as f:
                        config = yaml.safe_load(f)
                except ValidationError as e:
                    sys.exit(f"failed to load config file.\n{e}")

        if not config:
            sys.exit(
                "config file(./config.yml or ~/.junos-exporter/config.yml) is not found."
            )

        self.general = General(**config["general"])
        self.credentials = {
            name: Credential.model_validate(
                credential, context={"optables": config["optables"]}
            )
            for name, credential in config["credentials"].items()
        }
        self.modules = {
            name: Module.model_validate(
                module, context={"optables": config["optables"]}
            )
            for name, module in config["modules"].items()
        }
        self.optables = {
            name: OpTable(**optable) for name, optable in config["optables"].items()
        }

    @property
    def prefix(self) -> str:
        return self.general.prefix

    @property
    def ssh_config(self) -> str | None:
        return self.general.ssh_config
