"""Read a plug-in assembly's .NET metadata tables: its target framework and assembly references.

Metadata only, no decompiling: the ``TargetFrameworkAttribute`` on the assembly and the rows of
its ``AssemblyRef`` table. `dnfile` is imported on the first read, so a command that reads no
assembly does not pay for it.
"""

from __future__ import annotations

import logging
import struct
from dataclasses import dataclass
from typing import Any

_TARGET_FRAMEWORK = "System.Runtime.Versioning.TargetFrameworkAttribute"
_UNREADABLE = "the binary is not a readable .NET assembly"


@dataclass(frozen=True)
class AssemblyMetadata:
    """What an assembly's metadata says it was built against.

    *target_framework* is the ``TargetFrameworkAttribute`` value (``.NETFramework,Version=v4.0``),
    None when the assembly carries none, as one built for a runtime before 4.0 does.
    *references* holds each ``AssemblyRef`` row as (name, ``major.minor.build.revision``).
    """

    target_framework: str | None
    references: tuple[tuple[str, str], ...]


def _ser_string(blob: bytes) -> str | None:
    """The first fixed argument of a custom attribute blob (prolog 0x0001), a SerString."""
    if blob[:2] != b"\x01\x00" or len(blob) < 3:
        return None
    first = blob[2]
    # ECMA-335 II.23.2 compressed length: 1, 2 or 4 bytes by the top bits of the first byte.
    if first & 0x80 == 0:
        length, start = first, 3
    elif first & 0xC0 == 0x80:
        length, start = ((first & 0x3F) << 8) | blob[3], 4
    else:
        length, start = int.from_bytes(blob[2:6], "big") & 0x1FFFFFFF, 6
    text = blob[start : start + length]
    return text.decode("utf-8") if len(text) == length else None


def _attribute_name(row: Any) -> str | None:
    """The full name of the type whose constructor a CustomAttribute row calls, when it is a
    TypeRef or TypeDef (a reference to the framework's attribute, or a local one).
    """
    ctor = row.Type.row
    parent = getattr(getattr(ctor, "Class", None), "row", None)
    if parent is None or not hasattr(parent, "TypeName"):
        return None
    return f"{parent.TypeNamespace}.{parent.TypeName}"


def read_assembly(data: bytes) -> AssemblyMetadata | str:
    """The metadata of the assembly in *data*, or why it cannot be read.

    A payload dnfile reads only in part (a stream or table it could not parse) is unreadable:
    a table it skipped would read as an assembly with no such reference.
    """
    import dnfile
    import pefile

    # dnfile logs what it cannot parse, partly through the root logger, whose first warning
    # would install a stderr handler for the whole process; the reason returned says it instead.
    root = logging.getLogger()
    quiet = logging.NullHandler()
    root.addHandler(quiet)
    try:
        return _read(data, dnfile, pefile)
    # dnfile reads metadata leniently and documents no exception of its own; a truncated or
    # garbled payload surfaces as pefile's format error, a failed assert, index or unpack.
    except (
        pefile.PEFormatError,
        AssertionError,
        AttributeError,
        IndexError,
        KeyError,
        TypeError,
        ValueError,
        struct.error,
    ) as exc:
        return f"{_UNREADABLE}: {exc}"
    finally:
        root.removeHandler(quiet)


def _read(data: bytes, dnfile: Any, pefile: Any) -> AssemblyMetadata | str:
    # Parse the CLR header alone and each table on first use: a full parse of an assembly's
    # PE structures and every table cost about 0.3 s per real plug-in assembly, this ~1 ms.
    pe = dnfile.dnPE(data=data, fast_load=True, clr_lazy_load=True)
    pe.parse_data_directories(
        directories=[pefile.DIRECTORY_ENTRY["IMAGE_DIRECTORY_ENTRY_COM_DESCRIPTOR"]]
    )
    net = getattr(pe, "net", None)
    tables = None if net is None else net.mdtables
    if tables is None:
        return f"{_UNREADABLE}: it has no CLR metadata"
    framework: str | None = None
    for row in tables.CustomAttribute or ():
        if _attribute_name(row) == _TARGET_FRAMEWORK:
            # A heap item: its bytes are on `.value`. One that does not decode is not an absent
            # attribute, which would read as a runtime before 4.0.
            framework = _ser_string(bytes(row.Value.value or b""))
            if framework is None:
                return f"{_UNREADABLE}: its TargetFrameworkAttribute value does not decode"
            break
    references = tuple(
        (str(r.Name), f"{r.MajorVersion}.{r.MinorVersion}.{r.BuildNumber}.{r.RevisionNumber}")
        for r in tables.AssemblyRef or ()
    )
    # dnfile's own warnings follow pefile's in get_warnings(); pefile's are about the PE layout.
    partial = pe.get_warnings()[len(pefile.PE.get_warnings(pe)) :]
    if partial:
        return f"{_UNREADABLE}: read in part only: {partial[-1]}"
    return AssemblyMetadata(framework, references)
