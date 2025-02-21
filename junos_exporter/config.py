import os
import re
from collections import defaultdict
from typing import DefaultDict, Literal

import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationInfo,
    field_validator,
)


class General(BaseModel):
    port: int = 9326
    prefix: str = "junos"
    optables_dir: str | None = None
    textfsm_dir: str | None = None

    @field_validator("optables_dir", "textfsm_dir", mode="after")
    @classmethod
    def check_exist_dir(cls, path: str) -> str:
        abs_path = os.path.abspath(path)
        if not os.path.isdir(abs_path):
            raise ValueError(f"directory({abs_path}) does not exist")
        return abs_path


class Module(BaseModel):
    username: str
    password: str
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
    value_transform: DefaultDict[str, float] | None = None

    @field_validator("value_transform", mode="before")
    @classmethod
    def to_defaultdict(cls, value_transform: dict) -> dict:
        return defaultdict(lambda: "NaN", value_transform)


class OpTable(BaseModel):
    metrics: list[Metric]
    labels: list[Label]


class Config:
    _instance: "Config | None" = None
    _config_path: str

    def __new__(cls, config_path: str) -> "Config":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._config_path = config_path
        return cls._instance

    def __init__(self, config_path: str) -> None:
        with open(self._config_path, "r") as f:
            config = yaml.safe_load(f)

        self.general = General(**config["general"])
        self.modules = {
            name: Module.model_validate(
                module, context={"optables": config["optables"]}
            )
            for name, module in config["modules"].items()
        }
        self.optables = {
            name: OpTable(**optable) for name, optable in config["optables"].items()
        }

    @classmethod
    def get(cls) -> "Config | None":
        return cls._instance

    @property
    def port(self) -> int:
        return self.general.port

    @property
    def prefix(self) -> str:
        return self.general.prefix

    @property
    def optables_dir(self) -> str | None:
        return self.general.optables_dir

    @property
    def textfsm_dir(self) -> str | None:
        return self.general.textfsm_dir
