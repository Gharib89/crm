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
#: The assemblies the package replaces, lowercased.
SDK_ASSEMBLIES = frozenset(("microsoft.xrm.sdk", "microsoft.crm.sdk.proxy"))
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
_REFERENCE = re.compile(
    r"<(?:\w+:)?Reference\b[^>]*?\bInclude\s*=\s*([\"'])\s*([^,\"']*)", re.IGNORECASE
)


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


def _parse_version(version: str) -> tuple[int, ...] | None:
    match = _VERSION.fullmatch(version.strip())
    return None if match is None else tuple(int(p) for p in match.group(1).split("."))


def _below_floor(version: str | None) -> bool:
    """True when *version*, a ``TargetFrameworkVersion`` text, is under `FLOOR` (absent counts)."""
    parts = None if version is None else _parse_version(version)
    return parts is None or parts < FLOOR


def _target(version: str | None) -> str:
    """The SDK ``TargetFramework`` for a validated version text, the floor if below."""
    parts = None if _below_floor(version) else _parse_version(version or "")
    return "net" + "".join(map(str, parts or FLOOR))


def _read(text: str) -> Element | str:
    """The project's root, namespace stripped, or why it is not an MSBuild project."""
    try:
        root = fromstring(text.lstrip("\ufeff").encode("utf-8"))
    except ParseError:
        return "the project is not well-formed XML"
    _local(root)
    if root.tag != "Project":
        return "not an MSBuild project: the root element is not Project"
    return root


def _declared_version(root: Element) -> tuple[str | None, str | None]:
    """The one unconditional ``TargetFrameworkVersion`` text (None: absent), or None and why not."""
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
        return None, (
            "TargetFrameworkVersion is declared under a condition or more than once: the"
            " retarget cannot pick one framework for every configuration"
        )
    version = plain[0].text if plain else None
    if version is not None and _parse_version(version) is None:
        return None, f"TargetFrameworkVersion {version!r} is not a version"
    return version, None


def _sdk_references(root: Element) -> list[tuple[Element, Element]]:
    """Each SDK 5.0 ``Reference`` the package replaces, with its item group.

    Descendants too: a Choose/When block nests item groups an old project may use.
    """
    return [
        (group, ref)
        for group in root.iter("ItemGroup")
        for ref in group.findall("Reference")
        if _include(ref).lower() in SDK_ASSEMBLIES
    ]


def _include(ref: Element) -> str:
    """The assembly name a ``Reference`` includes, without its version and token."""
    return ref.get("Include", "").split(",")[0].strip()


@dataclass(frozen=True)
class Detection:
    """What a project holds that the retarget acts on: the one decision assess and convert share.

    *reason* is set when the project cannot be judged. *declared* is its
    ``TargetFrameworkVersion`` text (None when absent), *below_floor* whether that is under
    4.6.2 and *sdk_references* the ``Microsoft.Xrm.Sdk`` and ``Microsoft.Crm.Sdk.Proxy``
    references, in file order. *sites* are the 1-based (line, column) of the framework
    declaration, when it is below the floor, and of each SDK reference, found in the text as
    written, outside comments. An SDK-style project holds none of it.
    """

    reason: str | None = None
    declared: str | None = None
    below_floor: bool = False
    sdk_references: tuple[str, ...] = ()
    sites: tuple[tuple[int, int], ...] = ()


def _position(text: str, offset: int) -> tuple[int, int]:
    return text.count("\n", 0, offset) + 1, offset - (text.rfind("\n", 0, offset) + 1) + 1


def _blank_comments(text: str) -> str:
    """*text* with each XML comment replaced by spaces, so offsets and lines stay put."""
    return re.sub(r"<!--.*?-->", lambda m: re.sub(r"[^\n]", " ", m[0]), text, flags=re.DOTALL)


def _sites(text: str, framework: bool) -> tuple[tuple[int, int], ...]:
    text = _blank_comments(text)
    found: list[int] = []
    declaration = re.search(r"<(?:\w+:)?TargetFrameworkVersion\b", text, re.IGNORECASE)
    if framework and declaration:
        found.append(declaration.start())
    for m in _REFERENCE.finditer(text):
        if m.group(2).strip().lower() in SDK_ASSEMBLIES:
            found.append(m.start())
    return tuple(_position(text, i) for i in sorted(found))


def detect_csproj(text: str) -> Detection:
    """Judge one project's *text* the way `plan_csproj` does, without rewriting it."""
    root = _read(text)
    if isinstance(root, str):
        return Detection(reason=root)
    if root.get("Sdk") is not None:
        return Detection()
    declared, problem = _declared_version(root)
    if problem is not None:
        return Detection(reason=problem)
    names = tuple(_include(ref) for _, ref in _sdk_references(root))
    below = _below_floor(declared)
    return Detection(None, declared, below, names, _sites(text, below))


def plan_csproj(text: str) -> CsprojPlan:
    """Convert one old-style project's *text* (BOM and line endings are not significant)."""
    root = _read(text)
    if isinstance(root, str):
        return _whole(root)
    if root.get("Sdk") is not None:
        return _whole("already SDK-style: the retarget reads old-style projects only")
    props = {p.tag.lower(): p for g in root.iter("PropertyGroup") for p in g}
    if "projecttypeguids" in props:
        return _whole("ProjectTypeGuids marks a project type other than a plain class library")
    output = (props["outputtype"].text or "").strip() if "outputtype" in props else "Library"
    if output.lower() != "library":
        return _whole(f"the project type is {output}, not a class library")
    version, problem = _declared_version(root)
    if problem is not None:
        return _whole(problem)
    framework = _target(version)

    review: list[Review] = []
    if "ilmerge" in text.lower():
        review.append(
            Review(
                _line_of(text, "ilmerge"),
                "uses ILMerge: the merge step is carried over unchanged and is not proven"
                " under the SDK-style build",
            )
        )
    sdk = _sdk_references(root)
    for group, ref in sdk:
        group.remove(ref)
    replaced = bool(sdk)
    for group in root.iter("ItemGroup"):
        for ref in group.findall("Reference"):
            name = _include(ref)
            if name.lower() in _REVIEWED:
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
