"""Retarget an old-style plug-in project to SDK-style ``net462`` (ADR 0034).

`plan_csproj` is pure: project text in, the SDK-style text and the sites it cannot
prove out. It keeps every explicit item (``Compile``, ``None``, ``EmbeddedResource``,
``ProjectReference``, foreign ``Reference``), the conditional property groups and the
strong-name signing, and replaces the SDK 5.0 ``Microsoft.Xrm.Sdk`` and
``Microsoft.Crm.Sdk.Proxy`` references with the ``Microsoft.CrmSdk.CoreAssemblies``
package. Default item globbing and generated assembly info are switched off, because
the old project lists its own files and holds its own ``AssemblyInfo.cs``: a glob would
pull in files the project never built, and a second assembly-info would not compile.
The build itself stays outside convert's gate.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from xml.etree.ElementTree import Element, ParseError, indent, tostring

from crm.utils.safe_xml import fromstring

#: The ``Microsoft.CrmSdk.CoreAssemblies`` release the retarget pins; its ``lib/net462``
#: holds ``Microsoft.Xrm.Sdk.dll`` and ``Microsoft.Crm.Sdk.Proxy.dll`` and nothing else.
CORE_ASSEMBLIES_VERSION = "9.0.2.60"
CORE_ASSEMBLIES = "Microsoft.CrmSdk.CoreAssemblies"
FLOOR = (4, 6, 2)
#: The assemblies the package replaces.
_REPLACED = frozenset(("microsoft.xrm.sdk", "microsoft.crm.sdk.proxy"))
#: References no package carries: kept as written, the code using them is manual work.
_REVIEWED = {
    "microsoft.xrm.client": (
        "references Microsoft.Xrm.Client, which Microsoft.CrmSdk.CoreAssemblies does not carry:"
        " the reference is kept as written and the code that uses it needs a manual rewrite"
    ),
    "microsoft.xrm.sdk.workflow": (
        "references Microsoft.Xrm.Sdk.Workflow, which Microsoft.CrmSdk.CoreAssemblies does not"
        " carry: the reference is kept as written; the Microsoft.CrmSdk.Workflow package"
        " replaces it"
    ),
}
#: Properties the SDK supplies or the retarget replaces.
_DROPPED = frozenset(
    (
        "configuration",
        "platform",
        "projectguid",
        "productversion",
        "schemaversion",
        "appdesignerfolder",
        "targetframeworkversion",
        "targetframeworkprofile",
        "filealignment",
        "outputtype",
    )
)
#: Imports the SDK makes itself.
_SDK_IMPORT = re.compile(r"Microsoft\.(Common\.props|CSharp\.targets)", re.IGNORECASE)
_VERSION = re.compile(r"v(\d+(?:\.\d+)*)", re.IGNORECASE)


@dataclass(frozen=True)
class Review:
    """A site the retarget does not prove. *line* is None for the whole file."""

    line: int | None
    reason: str


@dataclass(frozen=True)
class CsprojPlan:
    """The SDK-style text, or None when the project is left alone, and what needs a human."""

    patched: str | None
    review: tuple[Review, ...]


def _whole(reason: str) -> CsprojPlan:
    return CsprojPlan(None, (Review(None, reason),))


def _line_of(text: str, needle: str) -> int | None:
    needle = needle.lower()
    return next((i for i, line in enumerate(text.splitlines(), 1) if needle in line.lower()), None)


def _local(root: Element) -> None:
    """Strip the MSBuild namespace from every tag: an SDK-style project declares none."""
    for el in root.iter():
        el.tag = el.tag.rpartition("}")[2]


def _target(version: str | None) -> str | None:
    """The SDK ``TargetFramework`` for an old ``TargetFrameworkVersion``, or None if unreadable."""
    if version is None:
        return "net" + "".join(map(str, FLOOR))
    match = _VERSION.fullmatch(version.strip())
    if match is None:
        return None
    parts = tuple(int(p) for p in match.group(1).split("."))
    return "net" + "".join(map(str, max(parts, FLOOR)))


def plan_csproj(text: str) -> CsprojPlan:
    """Convert one old-style project's *text* (BOM and line endings are not significant)."""
    try:
        root = fromstring(text.lstrip("﻿").encode("utf-8"))
    except ParseError:
        return _whole("the project is not well-formed XML")
    _local(root)
    if root.tag != "Project":
        return _whole("not an MSBuild project: the root element is not Project")
    if root.get("Sdk") is not None:
        return _whole("already SDK-style: the retarget reads old-style projects only")
    props = {p.tag.lower(): p for g in root.iter("PropertyGroup") for p in g}
    if "projecttypeguids" in props:
        return _whole("ProjectTypeGuids marks a project type other than a plain class library")
    output = (props["outputtype"].text or "").strip() if "outputtype" in props else "Library"
    if output.lower() != "library":
        return _whole(f"the project type is {output}, not a class library")
    declared = [
        p
        for g in root.iter("PropertyGroup")
        for p in g
        if p.tag.lower() == "targetframeworkversion"
    ]
    plain = [
        p
        for g in root.findall("PropertyGroup")
        if "Condition" not in g.attrib
        for p in g
        if p.tag.lower() == "targetframeworkversion" and "Condition" not in p.attrib
    ]
    if len(declared) != len(plain) or len(plain) > 1:
        return _whole(
            "TargetFrameworkVersion is declared under a condition or more than once: the"
            " retarget cannot pick one framework for every configuration"
        )
    version = plain[0] if plain else None
    framework = _target(version.text if version is not None else None)
    if framework is None:
        return _whole(
            f"TargetFrameworkVersion {version.text if version is not None else ''!r}"
            " is not a version"
        )

    review: list[Review] = []
    if "ilmerge" in text.lower():
        review.append(
            Review(
                _line_of(text, "ilmerge"),
                "uses ILMerge: the merge step is carried over unchanged and is not proven"
                " under the SDK-style build",
            )
        )
    replaced = False
    # Descendants too: a Choose/When block nests item groups an old project may use.
    for group in root.iter("ItemGroup"):
        for ref in group.findall("Reference"):
            name = ref.get("Include", "").split(",")[0].strip()
            if name.lower() in _REPLACED:
                group.remove(ref)
                replaced = True
            elif name.lower() in _REVIEWED:
                review.append(Review(_line_of(text, name), _REVIEWED[name.lower()]))

    out = Element("Project", {"Sdk": "Microsoft.NET.Sdk"})
    base = Element("PropertyGroup")
    out.append(base)
    base.append(_leaf("TargetFramework", framework))
    for group in root.findall("PropertyGroup"):
        if "Condition" not in group.attrib:
            base.extend(p for p in group if p.tag.lower() not in _DROPPED)
    base.append(_leaf("EnableDefaultItems", "false"))
    base.append(_leaf("GenerateAssemblyInfo", "false"))
    for group in root.findall("PropertyGroup"):
        if "Condition" in group.attrib:
            out.append(group)
    if replaced:
        packages = Element("ItemGroup")
        packages.append(
            Element(
                "PackageReference", {"Include": CORE_ASSEMBLIES, "Version": CORE_ASSEMBLIES_VERSION}
            )
        )
        out.append(packages)
    for child in root:
        if child.tag == "ItemGroup" and len(child):
            out.append(child)
        elif child.tag not in ("PropertyGroup", "ItemGroup") and not (
            child.tag == "Import" and _SDK_IMPORT.search(child.get("Project", ""))
        ):
            out.append(child)
    indent(out, "  ")
    return CsprojPlan(tostring(out, encoding="unicode") + "\n", tuple(review))


def _leaf(tag: str, text: str) -> Element:
    el = Element(tag)
    el.text = text
    return el
