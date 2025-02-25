from fastapi import HTTPException, status

from .config import Config, Label, Metric
from .connector import Connector


class MetricConverter:
    def __init__(self, metric: Metric, labels: list[Label], prefix: str):
        if metric.type_ == "counter":
            self.name = f"{prefix}_{metric.name}_total"
        else:
            self.name = f"{prefix}_{metric.name}"
        self.value = metric.value
        self.type_ = metric.type_
        self.help_ = metric.help_
        self.value_transform = metric.value_transform
        self.labels = labels

    def convert(self, items: list[dict]) -> str:
        exposition = []
        exposition.append(f"# HELP {self.name} {self.help_}")
        exposition.append(f"# TYPE {self.name} {self.type_}")

        for item in items:
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
                    label_exposition.append(f'{label.name}="{match.group()}"')

            if self.value not in item:
                try:
                    # static value
                    exposition.append(
                        f"{self.name}{{{','.join(label_exposition)}}} {float(self.value)}"
                    )
                except ValueError:
                    # value is not type change to float
                    exposition.append(
                        f"{self.name}{{{','.join(label_exposition)}}} NaN"
                    )

            elif self.value_transform:
                exposition.append(
                    f"{self.name}{{{','.join(label_exposition)}}} {self.value_transform[item[self.value]]}"
                )
            else:
                try:
                    exposition.append(
                        f"{self.name}{{{','.join(label_exposition)}}} {float(item[self.value])}"
                    )
                except ValueError:
                    # value is not type change to float
                    exposition.append(
                        f"{self.name}{{{','.join(label_exposition)}}} NaN"
                    )

        return "\n".join(exposition) + "\n"


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
        for name, module in config.modules.items():
            converter = {}
            for table in module.tables:
                converter[table] = [
                    MetricConverter(
                        metric,
                        labels=config.optables[table].labels,
                        prefix=config.prefix,
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
