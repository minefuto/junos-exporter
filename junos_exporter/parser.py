from collections.abc import Iterator

import pygxml

from junos_exporter.config import FieldSpec, Table

Pair = tuple[str, pygxml.Result]
Record = tuple[pygxml.Result, list[Pair], list[Pair]]


class Field:
    __slots__ = ("path", "head", "tail", "exists")

    def __init__(self, spec: FieldSpec) -> None:
        self.path = spec.path
        self.head, _, self.tail = spec.path.partition(".")
        self.exists = spec.exists


class Parser:
    def __init__(self, table: Table) -> None:
        self.container = [s for s in table.container.split(".") if s]
        self.items = set(table.item)
        self.recursive = table.recursive
        self.fields = {
            name: [Field(spec) for spec in specs]
            for name, specs in table.fields_.items()
        }

    def parse(self, xml: str) -> list[dict[str, str]]:
        document = pygxml.parse(xml)
        records = (
            self._to_record(*record)
            for container in self._containers(document)
            for record in self._scan(container, [])
        )
        return [record for record in records if record]

    def _containers(self, document: pygxml.Result) -> list[pygxml.Result]:
        nodes = [document]
        for segment in self.container:
            nodes = [v for n in nodes for k, v in _children(n) if k == segment]
        return nodes

    def _scan(
        self, node: pygxml.Result, inherited: list[Pair], boundary: bool = False
    ) -> Iterator[Record]:
        children = _children(node)
        starts = [
            i
            for i, (name, node_) in enumerate(children)
            if name in self.items and node_.type_ is dict
        ]

        if not starts:
            if not self.recursive:
                return
            context = (
                inherited
                if boundary
                else inherited + [(k, v) for k, v in children if v.type_ is not dict]
            )
            for _, child in children:
                if child.type_ is dict:
                    yield from self._scan(child, context)
            return

        context = inherited if boundary else inherited + children[: starts[0]]
        for n, start in enumerate(starts):
            end = starts[n + 1] if n + 1 < len(starts) else len(children)
            own = children[start][1]
            yield own, children[start + 1 : end], context
            if self.recursive:
                yield from self._scan(own, context, boundary=True)

    def _to_record(
        self, own: pygxml.Result, siblings: list[Pair], inherited: list[Pair]
    ) -> dict[str, str]:
        record = {}
        for name, fields in self.fields.items():
            value = self._resolve(fields, own, siblings, inherited)
            if value is not None:
                record[name] = value
        return record

    def _resolve(
        self,
        fields: list[Field],
        own: pygxml.Result,
        siblings: list[Pair],
        inherited: list[Pair],
    ) -> str | None:
        for field in fields:
            found = self._lookup(field, own, siblings, inherited)
            if field.exists:
                return "true" if found is not None else "false"
            if found is not None:
                return _to_str(found)
        return None

    def _lookup(
        self,
        field: Field,
        own: pygxml.Result,
        siblings: list[Pair],
        inherited: list[Pair],
    ) -> pygxml.Result | None:
        found = own.get(field.path)
        if found.exists():
            return found

        for scope in (siblings, inherited):
            for name, node in scope:
                if name != field.head:
                    continue
                found = node.get(field.tail) if field.tail else node
                if found.exists():
                    return found
        return None


def _children(node: pygxml.Result) -> list[Pair]:
    return list(node.children()) if node.type_ is dict else []


def _to_str(result: pygxml.Result) -> str:
    if result.type_ is list:
        return next(iter(result)).to_str()
    return result.to_str()
