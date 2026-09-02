import re
from datetime import datetime, timedelta
from math import isfinite, isnan

from fastapi import HTTPException, status

from junos_exporter.config import Config, Label, Metric, Table, logger
from junos_exporter.connector import Connector
from junos_exporter.parser import Parser


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
        self.key = metric.key if metric.path else ""
        self.value = metric.value
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
            if label.key not in item:
                continue

            if not label.regex:
                label_exposition.append(f'{label.name}="{item[label.key]}"')
                continue

            match = label.regex.match(item[label.key])
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
            if self.value is not None:
                exposition.append(
                    f"{self.name}{{{label_exposition}}} {to_prom(self.value)}\n"
                )
                continue

            if self.key not in item:
                logger.debug(
                    f"Could not convert metric value(Name: {self.name}, Path: {self.key}, Error: path was not resolved)"
                )
                continue

            value = item[self.key]

            if self.regex is not None:
                match = self.regex.match(value)
                if match is None:
                    logger.debug(
                        f"Could not convert metric value(Name: {self.name}, Path: {self.key}, Value: {value}, Regex: {self.regex}, Error: could not match regex)"
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
                        f"Could not convert metric value(Metric: {self.name}, Path: {self.key}, Value: {value}, Error: could not convert type to float)"
                    )
        return "".join(exposition)


class TableCollector:
    def __init__(
        self, name: str, table: Table, converters: list[MetricConverter]
    ) -> None:
        self.name = name
        self.table = table
        self.parser = Parser(table)
        self.converters = converters


class Exporter:
    def __init__(self, collectors: list[TableCollector], prefix: str) -> None:
        self.collectors = collectors
        self.prefix = prefix

    async def collect(self, connector: Connector) -> str:
        exposition: list[str] = []
        up_status: int = 1
        for collector in self.collectors:
            reply = await connector.get(collector.name, collector.table)
            if reply is None:
                up_status = 0
                continue

            logger.debug(
                f"Start to parse rpc reply(Target: {connector.target}, Table: {collector.name})"
            )
            items = collector.parser.parse(reply)
            logger.debug(
                f"Completed to parse rpc reply(Target: {connector.target}, Table: {collector.name}, Records: {len(items)})"
            )

            if not items:
                logger.debug(
                    f"Table items are empty(Target: {connector.target}, Table: {collector.name})"
                )
                continue

            exposition.append(
                "\n".join([c.convert(items) for c in collector.converters])
            )

        exposition.append(f"# HELP {self.prefix}_up All rpcs to target were successful")
        exposition.append(f"# TYPE {self.prefix}_up gauge")
        exposition.append(f"{self.prefix}_up{{}} {up_status}\n")
        return "\n".join(exposition)


class ExporterBuilder:
    def __init__(self, config: Config) -> None:
        self.collectors: dict[str, list[TableCollector]] = {}
        self.prefix = config.prefix
        unixtime_regex: dict[str, re.Pattern] = {
            "timestamp": re.compile(r".*(\d\d\d\d-\d\d-\d\d \d\d:\d\d:\d\d).*"),
            "wd_uptime": re.compile(r".*(\d+)w(\d+)d (\d\d):(\d\d):(\d\d).*"),
            "d_uptime": re.compile(r".*(\d+)d (\d\d):(\d\d):(\d\d).*"),
            "uptime": re.compile(r".*(\d\d):(\d\d):(\d\d).*"),
        }

        for name, module in config.modules.items():
            self.collectors[name] = [
                TableCollector(
                    table,
                    config.tables[table],
                    [
                        MetricConverter(
                            metric,
                            labels=config.tables[table].labels,
                            prefix=self.prefix,
                            unixtime_regex=unixtime_regex,
                        )
                        for metric in config.tables[table].metrics
                    ],
                )
                for table in module.tables
            ]

    def build(self, module_name: str) -> Exporter:
        if module_name not in self.collectors:
            logger.error(f"Module is not defined(Module: {module_name})")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Module is not defined(Module: {module_name})",
            )
        return Exporter(self.collectors[module_name], self.prefix)
