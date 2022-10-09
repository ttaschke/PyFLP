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

"""Contains the types used by the mixer, inserts and effect slots."""

from __future__ import annotations

import dataclasses
import enum
import sys
import warnings
from collections import defaultdict
from typing import Any, DefaultDict, Iterator, List, NamedTuple, cast

if sys.version_info >= (3, 8):
    from typing import TypedDict
else:
    from typing_extensions import TypedDict

if sys.version_info >= (3, 11):
    from typing import NotRequired, Unpack
else:
    from typing_extensions import NotRequired, Unpack

import colour
import construct as c
import construct_typed as ct

from ._descriptors import (
    EventProp,
    FlagProp,
    KWProp,
    NamedPropMixin,
    ROProperty,
    RWProperty,
)
from ._events import (
    DATA,
    DWORD,
    TEXT,
    WORD,
    AnyEvent,
    ColorEvent,
    DataEventBase,
    EventEnum,
    I16Event,
    I32Event,
    ListEventBase,
    StdEnum,
    StructEventBase,
    T,
    U16Event,
)
from ._models import FLVersion, ModelBase, ModelCollection, MultiEventModel, getslice
from .controller import RemoteController
from .exceptions import ModelNotFound, NoModelsFound, PropertyCannotBeSet
from .plugin import (
    FruityBalance,
    FruityBalanceEvent,
    FruityFastDist,
    FruityFastDistEvent,
    FruityNotebook2,
    FruityNotebook2Event,
    FruitySend,
    FruitySendEvent,
    FruitySoftClipper,
    FruitySoftClipperEvent,
    FruityStereoEnhancer,
    FruityStereoEnhancerEvent,
    PluginID,
    PluginProp,
    Soundgoodizer,
    SoundgoodizerEvent,
    VSTPlugin,
    VSTPluginEvent,
)

__all__ = [
    "Insert",
    "InsertDock",
    "InsertEQ",
    "InsertEQBand",
    "Mixer",
    "Slot",
]


@enum.unique
class _InsertFlags(enum.IntFlag):
    None_ = 0
    PolarityReversed = 1 << 0
    SwapLeftRight = 1 << 1
    EnableEffects = 1 << 2
    Enabled = 1 << 3
    DisableThreadedProcessing = 1 << 4
    U5 = 1 << 5
    DockMiddle = 1 << 6
    DockRight = 1 << 7
    U8 = 1 << 8
    U9 = 1 << 9
    SeparatorShown = 1 << 10
    Locked = 1 << 11
    Solo = 1 << 12
    U13 = 1 << 13
    U14 = 1 << 14
    AudioTrack = 1 << 15  # Whether insert is linked to an audio track


@enum.unique
class _MixerParamsID(ct.EnumBase):
    SlotEnabled = 0
    SlotMix = 1
    RouteVolStart = 64  # 64 - 191 are send level events
    Volume = 192
    Pan = 193
    StereoSeparation = 194
    LowGain = 208
    MidGain = 209
    HighGain = 210
    LowFreq = 216
    MidFreq = 217
    HighFreq = 218
    LowQ = 224
    MidQ = 225
    HighQ = 226


class InsertFlagsEvent(StructEventBase):
    STRUCT = c.Struct(
        "_u1" / c.Optional(c.Bytes(4)),  # 4
        "flags" / c.Optional(StdEnum[_InsertFlags](c.Int32ul)),  # 8
        "_u2" / c.Optional(c.Bytes(4)),  # 12
    ).compile()


class InsertRoutingEvent(ListEventBase):
    STRUCT = c.Struct("is_routed" / c.Flag).compile()


@dataclasses.dataclass
class _InsertItems:
    slots: DefaultDict[int, dict[int, dict[str, Any]]] = dataclasses.field(
        default_factory=lambda: defaultdict(dict)
    )
    own: dict[int, dict[str, Any]] = dataclasses.field(default_factory=dict)


class MixerParamsEvent(DataEventBase):
    STRUCT = c.Struct(
        "_u4" / c.Bytes(4),  # 4
        "id" / StdEnum[_MixerParamsID](c.Byte),  # 5
        "_u1" / c.Byte,  # 6
        "channel_data" / c.Int16ul,  # 8
        "msg" / c.Int32sl,  # 12
    ).compile()
    STRUCT_SIZE = STRUCT.sizeof()

    def __init__(self, id: int, data: bytearray):
        super().__init__(id, data)
        self.items: DefaultDict[int, _InsertItems] = defaultdict(_InsertItems)
        self.unparsed = False

        if not len(data) % self.STRUCT_SIZE:
            for i in range(len(data) // self.STRUCT_SIZE):
                offset = self.STRUCT_SIZE * i
                item = self.STRUCT.parse(data[offset : offset + self.STRUCT_SIZE])
                insert_idx = (item["channel_data"] >> 6) & 0x7F
                slot_idx = item["channel_data"] & 0x3F
                insert = self.items[insert_idx]
                id = item["id"]

                if id in (_MixerParamsID.SlotEnabled, _MixerParamsID.SlotMix):
                    insert.slots[slot_idx][id] = item
                else:
                    insert.own[id] = item
        else:
            self.unparsed = True
            warnings.warn(
                f"Cannot parse event {id} as event "
                "size is not a multiple of struct size"
            )


@enum.unique
class InsertID(EventEnum):
    Icon = (WORD + 31, I16Event)
    Output = (DWORD + 19, I32Event)
    Color = (DWORD + 21, ColorEvent)  #: 4.0+
    Input = (DWORD + 26, I32Event)
    Name = TEXT + 12  #: 3.5.4+
    Routing = (DATA + 27, InsertRoutingEvent)
    Flags = (DATA + 28, InsertFlagsEvent)


@enum.unique
class MixerID(EventEnum):
    APDC = 29
    Params = (DATA + 17, MixerParamsEvent)


@enum.unique
class SlotID(EventEnum):
    Index = (WORD + 34, U16Event)


# ? Maybe added in FL Studio v6.0.1
class InsertDock(enum.Enum):
    """![](https://bit.ly/3eLum9D)

    See Also:
        :attr:`Insert.dock`
    """  # noqa

    Left = enum.auto()
    Middle = enum.auto()
    Right = enum.auto()


class _InsertEQBandKW(TypedDict, total=False):
    gain: dict[str, Any]
    freq: dict[str, Any]
    reso: dict[str, Any]


class _InsertEQBandProp(NamedPropMixin, RWProperty[int]):
    def __get__(self, instance: InsertEQBand, owner: Any = None) -> int | None:
        if owner is None:
            return NotImplemented
        return instance._kw[self._prop]["msg"]

    def __set__(self, instance: InsertEQBand, value: int):
        instance._kw[self._prop]["msg"] = value


class InsertEQBand(ModelBase):
    def __init__(self, **kw: Unpack[_InsertEQBandKW]):
        super().__init__(**kw)

    def __repr__(self):
        return f"InsertEQ band (gain={self.gain}, freq={self.freq}, q={self.reso})"

    @property
    def size(self) -> int:
        return MixerParamsEvent.STRUCT_SIZE * len(self._kw)

    gain = _InsertEQBandProp()
    """
    | Min   | Max  | Default |
    |-------|------|---------|
    | -1800 | 1800 | 0       |
    """

    freq = _InsertEQBandProp()
    """
    | Min | Max   | Default             |
    |-----|-------|:--------------------|
    | 0   | 65536 | Depends on the band |
    """

    reso = _InsertEQBandProp()
    """
    | Min | Max   | Default |
    |-----|-------|---------|
    | 0   | 65536 | 17500   |
    """


class _InsertEQPropArgs(NamedTuple):
    freq: int
    gain: int
    reso: int


class _InsertEQProp(NamedPropMixin, ROProperty[InsertEQBand]):
    def __init__(self, ids: _InsertEQPropArgs) -> None:
        super().__init__()
        self._ids = ids

    def __get__(self, instance: InsertEQ, owner: Any = None) -> InsertEQBand:
        if owner is None:
            return NotImplemented

        items: _InsertEQBandKW = {}
        for id, param in cast(_InsertItems, instance._kw["params"]).own.items():
            if id == self._ids.freq:
                items["freq"] = param
            elif id == self._ids.gain:
                items["gain"] = param
            elif id == self._ids.reso:
                items["reso"] = param
        return InsertEQBand(**items)


# Stored in MixerID.Params event.
class InsertEQ(ModelBase):
    """Post-effect :class:`Insert` EQ with 3 adjustable bands.

    ![](https://bit.ly/3RUCQt6)

    See Also:
        :attr:`Insert.eq`
    """

    def __init__(self, params: _InsertItems):
        super().__init__(params=params)

    def __repr__(self):
        low = f"freq={self.low.freq}, gain={self.low.gain}, reso={self.low.reso}"
        mid = f"freq={self.mid.freq}, gain={self.mid.gain}, reso={self.mid.reso}"
        high = f"freq={self.high.freq}, gain={self.high.gain}, reso={self.high.reso}"
        return f"InsertEQ (low={low}, mid={mid}, high={high})"

    @property
    def size(self) -> int:
        return MixerParamsEvent.STRUCT_SIZE * self._kw["param"]

    low = _InsertEQProp(
        _InsertEQPropArgs(
            _MixerParamsID.LowFreq, _MixerParamsID.LowGain, _MixerParamsID.LowQ
        )
    )
    """Low shelf band. Default frequency - 5777 (90 Hz)."""

    mid = _InsertEQProp(
        _InsertEQPropArgs(
            _MixerParamsID.MidFreq, _MixerParamsID.MidGain, _MixerParamsID.MidQ
        )
    )
    """Middle band. Default frequency - 33145 (1500 Hz)."""

    high = _InsertEQProp(
        _InsertEQPropArgs(
            _MixerParamsID.HighFreq, _MixerParamsID.HighGain, _MixerParamsID.HighQ
        )
    )
    """High shelf band. Default frequency - 55825 (8000 Hz)."""


class _MixerParamProp(RWProperty[T]):
    def __init__(self, id: int):  # pylint: disable=super-init-not-called
        self._id = id

    def __get__(self, instance: Insert, owner: object = None) -> T | None:
        if owner is None:
            return NotImplemented

        for id, item in cast(_InsertItems, instance._kw["params"]).own.items():
            if id == self._id:
                return item["msg"]

    def __set__(self, instance: Insert, value: T):
        for id, item in cast(_InsertItems, instance._kw["params"]).own.items():
            if id == self._id:
                item["msg"] = value
        else:
            raise PropertyCannotBeSet(self._id)  # type: ignore


class Slot(MultiEventModel):
    """Represents an effect slot in an `Insert` / mixer channel.

    ![](https://bit.ly/3RUDtTu)
    """

    def __init__(self, *events: AnyEvent, params: list[dict[str, Any]] | None = None):
        super().__init__(*events, params=params or [])

    def __repr__(self) -> str:
        return f"Slot (name={self.name}, index={self.index}, plugin={self.plugin!r})"

    def __index__(self) -> int:
        if SlotID.Index not in self._events:
            return NotImplemented
        return self._events[SlotID.Index][0].value

    color = EventProp[colour.Color](PluginID.Color)
    controllers = KWProp[List[RemoteController]]()  # TODO
    internal_name = EventProp[str](PluginID.InternalName)
    """'Fruity Wrapper' for VST/AU plugins or factory name for native plugins."""

    enabled = _MixerParamProp[bool](_MixerParamsID.SlotEnabled)
    """![](https://bit.ly/3dqDzUA)"""

    icon = EventProp[int](PluginID.Icon)
    index = EventProp[int](SlotID.Index)
    mix = _MixerParamProp[int](_MixerParamsID.SlotMix)
    """Dry/Wet mix. Defaults to maximum value.

    | Type    | Value | Representation |
    |---------|-------|----------------|
    | Min     | -6400 | 100% left      |
    | Max     | 6400  | 100% right     |
    | Default | 0     | Centred        |
    """

    name = EventProp[str](PluginID.Name)
    plugin = PluginProp(
        {
            VSTPluginEvent: VSTPlugin,
            FruityBalanceEvent: FruityBalance,
            FruityFastDistEvent: FruityFastDist,
            FruityNotebook2Event: FruityNotebook2,
            FruitySendEvent: FruitySend,
            FruitySoftClipperEvent: FruitySoftClipper,
            FruityStereoEnhancerEvent: FruityStereoEnhancer,
            SoundgoodizerEvent: Soundgoodizer,
        }
    )
    """The effect loaded into the slot."""


class _InsertKW(TypedDict):
    index: int
    max_slots: int
    params: NotRequired[_InsertItems]


# TODO Need to make a `load()` method which will be able to parse preset files
# (by looking at Project.format) and use `MixerParameterEvent.items` to get
# remaining data. Normally, the `Mixer` passes this information to the Inserts
# (and Inserts to the `Slot`s directly).
class Insert(MultiEventModel, ModelCollection[Slot]):
    """Represents a mixer track to which channel from the rack are routed to.

    ![](https://bit.ly/3LeGKuN)
    """

    def __init__(self, *events: AnyEvent, **kw: Unpack[_InsertKW]):
        super().__init__(*events, **kw)

    # TODO Add number of used slots
    def __repr__(self):
        return f"Insert (name={self.name!r}, index={self.__index__()})"

    @getslice
    def __getitem__(self, i: int | str):
        """Returns an effect slot of the specified index or name.

        Args:
            i (int): An index in the range of 0 to :attr:`Mixer.max_slots`
                or the name of the :class:`Slot`.

        Raises:
            ModelNotFound: An effect :class:`Slot` with the specified index
                or name isn't found.
        """
        for idx, slot in enumerate(self):
            if (isinstance(i, int) and idx == i) or i == slot.name:
                return slot
        raise ModelNotFound(i)

    def __index__(self) -> int:
        """-1 for "current" insert, 0 for master and upto :attr:`Mixer.max_inserts`."""
        return self._kw["index"]

    def __iter__(self) -> Iterator[Slot]:
        """Iterator over the effect empty and used slots."""
        for i in range(self._kw["max_slots"] + 1):
            events: list[AnyEvent] = []

            for id, subevents in self._events.items():
                if id in (*SlotID, *PluginID) and i < len(subevents):
                    events.append(subevents[i])

            yield Slot(*events, params=self._kw["params"][i])

    def __len__(self):
        if SlotID.Index in self._events:
            return len(self._events[SlotID.Index])
        return len(list(self))

    bypassed = FlagProp(_InsertFlags.EnableEffects, InsertID.Flags, inverted=True)
    """Whether all slots are bypassed."""

    channels_swapped = FlagProp(_InsertFlags.SwapLeftRight, InsertID.Flags)
    """Whether the left and right channels are swapped."""

    color = EventProp[colour.Color](InsertID.Color)
    """*New in FL Studio v4.0*.

    Defaults to #636C71 (granite gray).
    Values below 20 for any color component (R, G or B) are ignored by FL.
    """

    @property
    def dock(self) -> InsertDock | None:
        """The position (left, middle or right) where insert is docked in mixer.

        ![](https://bit.ly/3eLum9D)
        """
        events = self._events.get(InsertID.Flags)
        if events is not None:
            event = cast(InsertFlagsEvent, events[0])
            flags = _InsertFlags(event["flags"])
            if _InsertFlags.DockMiddle in flags:
                return InsertDock.Middle
            if _InsertFlags.DockRight in flags:
                return InsertDock.Right
            return InsertDock.Left

    enabled = FlagProp(_InsertFlags.Enabled, InsertID.Flags)
    """Whether an insert in the mixer is enabled or disabled.

    ![](https://bit.ly/3BoRBOj)
    """

    @property
    def eq(self) -> InsertEQ:
        """3-band post EQ.

        ![](https://bit.ly/3RUCQt6)
        """
        return InsertEQ(self._kw["params"])

    icon = EventProp[int](InsertID.Icon)
    input = EventProp[int](InsertID.Input)
    """![](https://bit.ly/3RO0ckC)"""

    is_solo = FlagProp(_InsertFlags.Solo, InsertID.Flags)
    """Whether the insert is solo'd."""

    locked = FlagProp(_InsertFlags.Locked, InsertID.Flags)
    """Whether an insert in the mixer is in locked state.

    ![](https://bit.ly/3SdPbc2)
    """

    name = EventProp[str](InsertID.Name)
    """*New in FL Studio v3.5.4*."""

    output = EventProp[int](InsertID.Output)
    """![](https://bit.ly/3LjWjBD)"""

    pan = _MixerParamProp[int](_MixerParamsID.Pan)
    """Linear.

    | Type    | Value | Representation |
    |---------|-------|----------------|
    | Min     | -6400 | 100% left      |
    | Max     | 6400  | 100% right     |
    | Default | 0     | Centred        |

    ![](https://bit.ly/3DsZRj4)
    """

    polarity_reversed = FlagProp(_InsertFlags.PolarityReversed, InsertID.Flags)
    """Whether phase / polarity is reversed / inverted."""

    @property
    def routes(self) -> Iterator[int]:
        """Send volumes to routed inserts.

        *New in FL Studio v4.0*.
        """
        items = iter(cast(InsertRoutingEvent, self._events[InsertID.Routing][0]))
        for id, item in cast(_InsertItems, self._kw["params"]).own.items():
            if id >= _MixerParamsID.RouteVolStart and next(items)["is_routed"]:
                yield item["msg"]

    separator_shown = FlagProp(_InsertFlags.SeparatorShown, InsertID.Flags)
    """Whether separator is shown before the insert."""

    stereo_separation = _MixerParamProp[int](_MixerParamsID.StereoSeparation)
    """Linear.

    | Type    | Value | Representation |
    |---------|-------|----------------|
    | Min     | 64    | 100% merged    |
    | Max     | -64   | 100% separated |
    | Default | 0     | No effect      |
    """

    volume = _MixerParamProp[int](_MixerParamsID.Volume)
    """Post volume fader. Logarithmic.

    | Type    | Value | Representation      |
    |---------|-------|---------------------|
    | Min     | 0     | 0% / -INFdB / 0.00  |
    | Max     | 16000 | 125% / 5.6dB / 1.90 |
    | Default | 12800 | 100% / 0.0dB / 1.00 |
    """


class _MixerKW(TypedDict):
    version: FLVersion


# TODO FL Studio version in which slots were increased to 10
# TODO A move() method to change the placement of Inserts; it's difficult!
class Mixer(MultiEventModel, ModelCollection[Insert]):
    """Represents the mixer which contains :class:`Insert` instances.

    ![](https://bit.ly/3eOsblF)
    """

    _MAX_INSERTS = {
        (1, 6, 5): 5,
        (2, 0, 1): 8,
        (3, 0, 0): 18,
        (3, 3, 0): 20,
        (4, 0, 0): 64,
        (9, 0, 0): 105,
        (12, 9, 0): 127,
    }

    _MAX_SLOTS = {(1, 6, 5): 4, (3, 0, 0): 8}

    def __init__(self, *events: AnyEvent, **kw: Unpack[_MixerKW]):
        super().__init__(*events, **kw)

    # Inserts don't store their index internally.
    @getslice
    def __getitem__(self, i: int | str | slice):
        """Returns an insert with the specified index or name.

        Args:
            i (int | str | slice): An index between 0 to :attr:`Mixer.max_inserts`
                resembling the one shown by FL Studio or the name of the insert.
                Use 0 for master and -1 for "current" insert.

        Raises:
            ModelNotFound: An :class:`Insert` with the specifcied name or index
                isn't found.
        """
        for idx, insert in enumerate(self):
            if (isinstance(i, int) and idx == i + 1) or i == insert.name:
                return insert
        raise ModelNotFound(i)

    def __iter__(self) -> Iterator[Insert]:
        index = 0
        events: list[AnyEvent] = []
        params: dict[int, _InsertItems] = {}

        if MixerID.Params in self._events:
            params = cast(MixerParamsEvent, self._events[MixerID.Params][0]).items

        for event in self._events_tuple:
            if event.id in (*InsertID, *PluginID, *SlotID):
                events.append(event)

            if event.id == InsertID.Output:
                try:
                    items = params[index]
                except IndexError:
                    yield Insert(*events, index=index, max_slots=self.max_slots)
                else:
                    yield Insert(
                        *events,
                        index=index,
                        max_slots=self.max_slots,
                        params=items,
                    )
                events = []
                index += 1

    def __len__(self):
        """Returns the number of inserts present in the project.

        Raises:
            NoModelsFound: No inserts could be found.
        """
        if InsertID.Flags not in self._events:
            raise NoModelsFound
        return len(self._events[InsertID.Flags])

    def __repr__(self):
        return f"Mixer: {len(self)} inserts"

    apdc = EventProp[bool](MixerID.APDC)
    """Whether automatic plugin delay compensation is enabled for the inserts."""

    @property
    def max_inserts(self) -> int:
        """Estimated max number of inserts including sends, master and current.

        Maximum number of slots w.r.t. FL Studio:

        * 1.6.5: 4 inserts + master, 5 in total
        * 2.0.1: 8
        * 3.0.0: 16 inserts, 2 sends.
        * 3.3.0: +2 sends.
        * 4.0.0: 64
        * 9.0.0: 99 inserts, 105 in total.
        * 12.9.0: 125 + master + current.
        """
        version = dataclasses.astuple(self._kw["version"])
        for k, v in self._MAX_INSERTS.items():
            if version <= k:
                return v
        return 127

    @property
    def max_slots(self) -> int:
        """Estimated max number of effect slots per insert.

        Maximum number of slots w.r.t. FL Studio:

        * 1.6.5: 4
        * 3.3.0: 8
        """
        version = dataclasses.astuple(self._kw["version"])
        for k, v in self._MAX_SLOTS.items():
            if version <= k:
                return v
        return 10
