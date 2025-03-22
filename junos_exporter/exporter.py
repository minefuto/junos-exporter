import re
from datetime import datetime, timedelta

from fastapi import HTTPException, status

from .config import Config, Label, Metric
from .connector import Connector


class MetricConverter:
    def __init__(
        self,
        metric: Metric,
        labels: list[Label],
        prefix: str,
        unixtime_regex: dict[str, re.Pattern],
    ):
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
            return float(0)

    def _convert_label(self, item: dict) -> list[str]:
        label_exposition = []
        for label in self.labels:
            # label value missing
            if label.value not in item:
                continue

            # label value is None
            if item[label.value] is None:
                continue

            # label regex is not defined
            if not label.regex:
                label_exposition.append(f'{label.name}="{item[label.value]}"')
                continue

            match = label.regex.match(item[label.value])
            # label regex is not hitting
            if match is None:
                continue
            # label regex is hitting
            else:
                try:
                    label_exposition.append(f'{label.name}="{match.group(1)}"')
                except IndexError:
                    continue
        return label_exposition

    def convert(self, items: list[dict]) -> str:
        exposition = []
        exposition.append(f"# HELP {self.name} {self.help_}\n")
        exposition.append(f"# TYPE {self.name} {self.type_}\n")

        for item in items:
            label_exposition = ",".join(self._convert_label(item))
            if self.value_name not in item:
                try:
                    # static value
                    exposition.append(
                        f"{self.name}{{{label_exposition}}} {float(self.value_name)}\n"
                    )
                    continue
                except (ValueError, TypeError):
                    # value is not type change to float
                    exposition.append(f"{self.name}{{{label_exposition}}} NaN\n")
                    continue

            value = item[self.value_name]
            if self.regex is not None:
                match = self.regex.match(value)
                if match is None:
                    exposition.append(f"{self.name}{{{label_exposition}}} NaN\n")
                    continue
                else:
                    try:
                        value = match.group(1)
                    except IndexError:
                        value = match.group()

            if self.value_transform:
                exposition.append(
                    f"{self.name}{{{label_exposition}}} {self.value_transform[value]}\n"
                )
            elif self.to_unixtime:
                try:
                    exposition.append(
                        f"{self.name}{{{label_exposition}}} {self._convert_to_unixtime(value)}\n"
                    )
                except (ValueError, TypeError):
                    exposition.append(f"{self.name}{{{label_exposition}}} NaN\n")
            else:
                try:
                    exposition.append(
                        f"{self.name}{{{label_exposition}}} {float(value)}\n"
                    )
                except (ValueError, TypeError):
                    # value is not type change to float
                    exposition.append(f"{self.name}{{{label_exposition}}} NaN\n")

        return "".join(exposition)


class Exporter:
    def __init__(self, converter: dict[str, list[MetricConverter]]) -> None:
        self.converter = converter

    def collect(self, connector: Connector) -> str:
        exposition: list[str] = []
        for name, metrics in self.converter.items():
            exposition.append(
                "\n".join(
                    [metric.convert(connector.collect(name)) for metric in metrics]
                )
            )
        return "\n".join(exposition)


class ExporterBuilder:
    def __init__(self, config: Config) -> None:
        self.converters = {}
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
                        prefix=config.prefix,
                        unixtime_regex=unixtime_regex,
                    )
                    for metric in config.optables[table].metrics
                ]
            self.converters[name] = converter

    def build(self, module_name: str) -> Exporter:
        if module_name not in self.converters:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"module({module_name}) is not defined",
            )
        return Exporter(self.converters[module_name])
