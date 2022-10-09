# PyFLP - An FL Studio project file (.flp) parser
# Copyright (C) 2022 demberto
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by the Free
# Software Foundation, either version 3 of the License, or (at your option)
# any later version. This program is distributed in the hope that it will be
# useful, but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU General
# Public License for more details. You should have received a copy of the
# GNU General Public License along with this program. If not, see
# <https://www.gnu.org/licenses/>.

"""Contains the ABCs used by model classes."""

from __future__ import annotations

import abc
import collections
import dataclasses
import functools
import sys
from typing import Any, Callable, DefaultDict, Iterable, Sequence, TypeVar, overload

if sys.version_info >= (3, 8):
    from typing import Protocol, runtime_checkable
else:
    from typing_extensions import Protocol, runtime_checkable

from ._events import AnyEvent


class ModelBase(abc.ABC):
    def __init__(self, **kw: Any):
        self._kw = kw


class ItemModel(ModelBase):
    """Base class for event-less models."""

    def __init__(self, item: dict[str, Any], **kw: Any):
        self._item = item
        super().__init__(**kw)

    def __getitem__(self, prop: str):
        return self._item[prop]

    def __setitem__(self, prop: str, value: Any):
        self._item[prop] = value


class SingleEventModel(ModelBase):
    """Base class for models whose properties are derived from a single event."""

    def __init__(self, event: AnyEvent, **kw: Any):
        super().__init__(**kw)
        self._event = event

    def __eq__(self, o: object):
        if not isinstance(o, type(self)):
            raise TypeError(f"Cannot compare {type(o)!r} with {type(self)!r}")

        return o.event() == self.event()

    def event(self) -> AnyEvent:
        """Returns the underlying event used by the model.

        Tip:
            You almost never need to use this method and it is only provided
            to calm type checkers from complaining about protected access.
        """
        return self._event


class MultiEventModel(ModelBase):
    def __init__(self, *events: AnyEvent, **kw: Any):
        super().__init__(**kw)
        self._events: dict[int, list[AnyEvent]] = {}
        self._events_tuple = events
        tmp: DefaultDict[int, list[AnyEvent]] = collections.defaultdict(list)

        for event in events:
            if event is not None:
                tmp[event.id].append(event)
        self._events.update(tmp)

    def __eq__(self, o: object):
        if not isinstance(o, type(self)):
            raise TypeError(f"Cannot compare {type(o)!r} with {type(self)!r}")

        return o.events_astuple() == self.events_astuple()

    def events_astuple(self):
        """Returns a tuple of events used by the model in their original order."""
        return self._events_tuple

    def events_asdict(self):
        """Returns a dictionary of event ID to a list of events."""
        return self._events


MT_co = TypeVar("MT_co", bound=ModelBase, covariant=True)
SEMT_co = TypeVar("SEMT_co", bound=SingleEventModel, covariant=True)


@runtime_checkable
class ModelCollection(Iterable[MT_co], Protocol[MT_co]):
    @overload
    def __getitem__(self, i: int) -> MT_co:
        ...

    @overload
    def __getitem__(self, i: str) -> MT_co:
        ...

    @overload
    def __getitem__(self, i: slice) -> Sequence[MT_co]:
        ...


def getslice(func: Callable[[Any, Any], MT_co]):
    """Wraps a :meth:`ModelCollection.__getitem__` to return a sequence if required."""

    @overload
    def wrapper(self: Any, i: int) -> MT_co:
        ...

    @overload
    def wrapper(self: Any, i: str) -> MT_co:
        ...

    @overload
    def wrapper(self: Any, i: slice) -> Sequence[MT_co]:
        ...

    @functools.wraps(func)
    def wrapper(self: Any, i: Any) -> MT_co | Sequence[MT_co]:
        if isinstance(i, slice):
            return [
                model
                for model in self
                if getattr(model, "__index__")() in range(i.start, i.stop)
            ]
        return func(self, i)

    return wrapper


class ModelReprMixin:
    """I am too lazy to make one `__repr__()` for every model."""

    def __repr__(self):
        mapping: dict[str, Any] = {}
        for var in [var for var in vars(type(self)) if not var.startswith("_")]:
            mapping[var] = getattr(self, var, None)

        params = ", ".join([f"{k}={v!r}" for k, v in mapping.items()])
        return f"{type(self).__name__} ({params})"


@dataclasses.dataclass(frozen=True, order=True)
class FLVersion:
    major: int
    minor: int = 0
    patch: int = 0
    build: int | None = None

    def __str__(self):
        version = f"{self.major}.{self.minor}.{self.patch}"
        if self.build is not None:
            return f"{version}.{self.build}"
        return version
