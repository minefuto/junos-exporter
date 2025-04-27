import re
from datetime import datetime, timedelta
from math import isfinite, isnan

from fastapi import HTTPException, status

from .config import Config, Label, Metric, logger
from .connector import Connector


class MetricConverter:
    def __init__(
        self,
        metric: Metric,
        labels: list[Label],
        prefix: str,
        unixtime_regex: dict[str, re.Pattern],
    ) -> None:
        if metric.type_ == "counter":
            self.name = f"{prefix}_{metric.name}_total"
        else:
            self.name = f"{prefix}_{metric.name}"
        self.value_name = metric.value
        self.type_ = metric.type_
        self.help_ = metric.help_
        self.regex = metric.regex
        self.value_transform = metric.value_transform
        self.to_unixtime = metric.to_unixtime
        self.labels = labels
        self.unixtime_regex = unixtime_regex

    def _convert_to_unixtime(self, value: str) -> float:
        if result := self.unixtime_regex["timestamp"].search(value):
            return float(
                datetime.strptime(result.group(1), "%Y-%m-%d %H:%M:%S").timestamp()
                * 1000
            )

        init_time = datetime.fromtimestamp(0)
        if result := self.unixtime_regex["wd_uptime"].search(value):
            return float(
                (
                    init_time
                    + timedelta(
                        weeks=int(result.group(1)),
                        days=int(result.group(2)),
                        hours=int(result.group(3)),
                        minutes=int(result.group(4)),
                        seconds=int(result.group(5)),
                    )
                ).timestamp()
                * 1000
            )
        elif result := self.unixtime_regex["d_uptime"].search(value):
            return float(
                (
                    init_time
                    + timedelta(
                        days=int(result.group(1)),
                        hours=int(result.group(2)),
                        minutes=int(result.group(3)),
                        seconds=int(result.group(4)),
                    )
                ).timestamp()
                * 1000
            )
        elif result := self.unixtime_regex["uptime"].search(value):
            return float(
                (
                    init_time
                    + timedelta(
                        hours=int(result.group(1)),
                        minutes=int(result.group(2)),
                        seconds=int(result.group(3)),
                    )
                ).timestamp()
                * 1000
            )
        else:
            return 0.0

    def _convert_label(self, item: dict) -> list[str]:
        label_exposition = []
        for label in self.labels:
            if label.value not in item:
                continue

            if item[label.value] is None:
                continue

            if not label.regex:
                label_exposition.append(f'{label.name}="{item[label.value]}"')
                continue

            match = label.regex.match(item[label.value])
            if match is None:
                continue
            else:
                try:
                    label_exposition.append(f'{label.name}="{match.group(1)}"')
                except IndexError:
                    continue
        return label_exposition

    def convert(self, items: list[dict]) -> str:
        def to_prom(value: float) -> float | str:
            if isfinite(value):
                return value
            elif isnan(value):
                return "NaN"
            else:  # isinf
                return "+Inf" if value > 0 else "-Inf"

        exposition = []
        exposition.append(f"# HELP {self.name} {self.help_}\n")
        exposition.append(f"# TYPE {self.name} {self.type_}\n")

        for item in items:
            label_exposition = ",".join(self._convert_label(item))
            if self.value_name not in item:
                try:
                    # static value
                    exposition.append(
                        f"{self.name}{{{label_exposition}}} {to_prom(float(self.value_name))}\n"
                    )
                    continue
                except ValueError:
                    logger.debug(
                        f"Could not convert metric value(Name: {self.name}, Value: {self.value_name}, Error: value does not exist)"
                    )
                    continue

            value = item[self.value_name]
            if value is None:
                continue

            if self.regex is not None:
                match = self.regex.match(value)
                if match is None:
                    logger.debug(
                        f"Could not convert metric value(Name: {self.name}, Value({self.value_name}): {value}, Regex: {self.regex}, Error: could not match regex)"
                    )
                    continue
                else:
                    try:
                        value = match.group(1)
                    except IndexError:
                        value = match.group()

            if self.value_transform:
                exposition.append(
                    f"{self.name}{{{label_exposition}}} {to_prom(self.value_transform[value])}\n"
                )
            elif self.to_unixtime:
                exposition.append(
                    f"{self.name}{{{label_exposition}}} {self._convert_to_unixtime(value)}\n"
                )
            else:
                try:
                    exposition.append(
                        f"{self.name}{{{label_exposition}}} {to_prom(float(value))}\n"
                    )
                except ValueError:
                    logger.debug(
                        f"Could not convert metric value(Metric: {self.name}, Value({self.value_name}): {value}, Error: could not convert type to float)"
                    )
        return "".join(exposition)


class Exporter:
    def __init__(
        self, converter: dict[str, list[MetricConverter]], prefix: str
    ) -> None:
        self.converter = converter
        self.prefix = prefix

    async def collect(self, connector: Connector) -> str:
        exposition: list[str] = []
        up_status: int = 1
        for name, metrics in self.converter.items():
            items = await connector.collect(name)
            if items is None:
                up_status = 0
                continue

            if not items:
                logger.debug(
                    f"Table items are empty(Target: {connector.host}, Table: {name})"
                )
                continue

            logger.debug(
                f"Start to convert table items(Target: {connector.host}, Table: {name})"
            )
            exposition.append("\n".join([metric.convert(items) for metric in metrics]))
            logger.debug(
                f"Completed to convert table items(Target: {connector.host}, Table: {name})"
            )

        exposition.append(f"# HELP {self.prefix}_up All rpcs to target were successful")
        exposition.append(f"# TYPE {self.prefix}_up gauge")
        exposition.append(f"{self.prefix}_up{{}} {up_status}\n")
        return "\n".join(exposition)


class ExporterBuilder:
    def __init__(self, config: Config) -> None:
        self.converters = {}
        self.prefix = config.prefix
        unixtime_regex: dict[str, re.Pattern] = {
            "timestamp": re.compile(r".*(\d\d\d\d-\d\d-\d\d \d\d:\d\d:\d\d).*"),
            "wd_uptime": re.compile(r".*(\d+)w(\d+)d (\d\d):(\d\d):(\d\d).*"),
            "d_uptime": re.compile(r".*(\d+)d (\d\d):(\d\d):(\d\d).*"),
            "uptime": re.compile(r".*(\d\d):(\d\d):(\d\d).*"),
        }

        for name, module in config.modules.items():
            converter = {}
            for table in module.tables:
                converter[table] = [
                    MetricConverter(
                        metric,
                        labels=config.optables[table].labels,
                        prefix=self.prefix,
                        unixtime_regex=unixtime_regex,
                    )
                    for metric in config.optables[table].metrics
                ]
            self.converters[name] = converter

    def build(self, module_name: str) -> Exporter:
        if module_name not in self.converters:
            logger.error(f"Module is not defined(Module: {module_name})")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Module is not defined(Module: {module_name})",
            )
        return Exporter(self.converters[module_name], self.prefix)
