from collections.abc import Iterator

import pygxml

from junos_exporter.config import PathSpec, Table

Pair = tuple[str, pygxml.Result]
Record = tuple[pygxml.Result, list[Pair], list[Pair]]


class Candidate:
    __slots__ = ("path", "head", "tail")

    def __init__(self, path: str) -> None:
        self.path = path
        self.head, _, self.tail = path.partition(".")


class Field:
    __slots__ = ("key", "candidates", "exists")

    def __init__(self, spec: PathSpec) -> None:
        self.key = spec.key
        self.candidates = [Candidate(path) for path in spec.path]
        self.exists = spec.exists


class Parser:
    def __init__(self, table: Table) -> None:
        self.container = [s for s in table.container.split(".") if s]
        self.items = set(table.item)
        self.recursive = table.recursive
        self.fields = [Field(spec) for spec in table.specs]

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
        for field in self.fields:
            value = self._resolve(field, own, siblings, inherited)
            if value is not None:
                record[field.key] = value
        return record

    def _resolve(
        self,
        field: Field,
        own: pygxml.Result,
        siblings: list[Pair],
        inherited: list[Pair],
    ) -> str | None:
        for candidate in field.candidates:
            found = self._lookup(candidate, own, siblings, inherited)
            if field.exists:
                return "true" if found is not None else "false"
            if found is not None:
                return _to_str(found)
        return None

    def _lookup(
        self,
        candidate: Candidate,
        own: pygxml.Result,
        siblings: list[Pair],
        inherited: list[Pair],
    ) -> pygxml.Result | None:
        found = own.get(candidate.path)
        if found.exists():
            return found

        for scope in (siblings, inherited):
            for name, node in scope:
                if name != candidate.head:
                    continue
                found = node.get(candidate.tail) if candidate.tail else node
                if found.exists():
                    return found
        return None


def _children(node: pygxml.Result) -> list[Pair]:
    return list(node.children()) if node.type_ is dict else []


def _to_str(result: pygxml.Result) -> str:
    if result.type_ is list:
        return next(iter(result)).to_str()
    return result.to_str()
