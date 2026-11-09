"""The csproj retarget transform: old-style plug-in project to SDK-style (ADR 0034)."""

# pyright: basic

from __future__ import annotations

import xml.etree.ElementTree as ET

import pytest

from crm.core.inventory_csproj import CORE_ASSEMBLIES_VERSION, plan_csproj

OLD_STYLE = """﻿<?xml version="1.0" encoding="utf-8"?>
<Project ToolsVersion="4.0" DefaultTargets="Build" xmlns="http://schemas.microsoft.com/developer/msbuild/2003">
  <Import Project="$(MSBuildExtensionsPath)\\Microsoft.Common.props"
          Condition="Exists('$(MSBuildExtensionsPath)\\Microsoft.Common.props')" />
  <PropertyGroup>
    <Configuration Condition=" '$(Configuration)' == '' ">Debug</Configuration>
    <Platform Condition=" '$(Platform)' == '' ">AnyCPU</Platform>
    <ProjectGuid>{11111111-2222-3333-4444-555555555555}</ProjectGuid>
    <OutputType>Library</OutputType>
    <RootNamespace>Contoso.Plugins</RootNamespace>
    <AssemblyName>Contoso.Plugins</AssemblyName>
    <TargetFrameworkVersion>v4.0</TargetFrameworkVersion>
    <FileAlignment>512</FileAlignment>
    <SignAssembly>true</SignAssembly>
    <AssemblyOriginatorKeyFile>Contoso.snk</AssemblyOriginatorKeyFile>
  </PropertyGroup>
  <PropertyGroup Condition=" '$(Configuration)|$(Platform)' == 'Debug|AnyCPU' ">
    <DebugSymbols>true</DebugSymbols>
    <OutputPath>bin\\Debug\\</OutputPath>
    <DefineConstants>DEBUG;TRACE</DefineConstants>
  </PropertyGroup>
  <ItemGroup>
    <Reference Include="Microsoft.Crm.Sdk.Proxy, Version=5.0.0.0, PublicKeyToken=31bf3856ad364e35">
      <SpecificVersion>False</SpecificVersion>
      <HintPath>..\\sdk\\bin\\Microsoft.Crm.Sdk.Proxy.dll</HintPath>
    </Reference>
    <Reference Include="Microsoft.Xrm.Sdk, Version=5.0.0.0, PublicKeyToken=31bf3856ad364e35">
      <SpecificVersion>False</SpecificVersion>
      <HintPath>..\\sdk\\bin\\Microsoft.Xrm.Sdk.dll</HintPath>
    </Reference>
    <Reference Include="System" />
    <Reference Include="System.Runtime.Serialization" />
  </ItemGroup>
  <ItemGroup>
    <Compile Include="AccountPlugin.cs" />
    <Compile Include="Properties\\AssemblyInfo.cs" />
  </ItemGroup>
  <ItemGroup>
    <None Include="Contoso.snk" />
  </ItemGroup>
  <Import Project="$(MSBuildToolsPath)\\Microsoft.CSharp.targets" />
</Project>
"""


def _ns(text: str) -> ET.Element:
    root = ET.fromstring(text)
    for el in root.iter():
        el.tag = el.tag.rpartition("}")[2]
    return root


def _plan(text: str = OLD_STYLE):
    plan = plan_csproj(text)
    assert plan.patched is not None, plan.review
    return plan, _ns(plan.patched)


def _props(root: ET.Element) -> dict[str, str]:
    return {
        p.tag: (p.text or "")
        for g in root.findall("PropertyGroup")
        if "Condition" not in g.attrib
        for p in g
    }


def _refs(root: ET.Element) -> list[str]:
    return [r.get("Include", "") for g in root.iter("ItemGroup") for r in g.findall("Reference")]


def test_fixture_converts_to_sdk_style_net462() -> None:
    plan, root = _plan()
    assert plan.review == ()
    assert root.get("Sdk") == "Microsoft.NET.Sdk"
    assert root.get("ToolsVersion") is None
    props = _props(root)
    assert props["TargetFramework"] == "net462"
    assert "TargetFrameworkVersion" not in props
    assert props["AssemblyName"] == props["RootNamespace"] == "Contoso.Plugins"
    # Strong-name signing is kept.
    assert (props["SignAssembly"], props["AssemblyOriginatorKeyFile"]) == (
        "true",
        "Contoso.snk",
    )
    # Explicit items stay the only items, and the project's AssemblyInfo.cs stays its own.
    assert (props["EnableDefaultItems"], props["GenerateAssemblyInfo"]) == ("false", "false")
    for dropped in ("Configuration", "Platform", "ProjectGuid", "FileAlignment", "OutputType"):
        assert dropped not in props


def test_sdk_hintpath_references_become_core_assemblies_package() -> None:
    plan, root = _plan()
    assert _refs(root) == ["System", "System.Runtime.Serialization"]
    (package,) = root.iter("PackageReference")
    assert package.attrib == {
        "Include": "Microsoft.CrmSdk.CoreAssemblies",
        "Version": CORE_ASSEMBLIES_VERSION,
    }
    assert plan.patched is not None and "HintPath" not in plan.patched


def test_items_conditions_and_foreign_imports_carry_over() -> None:
    plan, root = _plan()
    compiles = [c.get("Include") for c in root.iter("Compile")]
    assert compiles == ["AccountPlugin.cs", "Properties\\AssemblyInfo.cs"]
    assert [n.get("Include") for n in root.iter("None")] == ["Contoso.snk"]
    (debug,) = [g for g in root.findall("PropertyGroup") if "Condition" in g.attrib]
    assert {p.tag: p.text for p in debug} == {
        "DebugSymbols": "true",
        "OutputPath": "bin\\Debug\\",
        "DefineConstants": "DEBUG;TRACE",
    }
    # The SDK supplies Common.props and Microsoft.CSharp.targets itself.
    assert list(root.iter("Import")) == []


def test_output_is_well_formed_xml_without_a_namespace_or_bom() -> None:
    plan, _ = _plan()
    assert plan.patched is not None
    assert not plan.patched.startswith("﻿")
    assert "xmlns" not in plan.patched
    assert plan.patched.endswith("</Project>\n")


@pytest.mark.parametrize(
    ("version", "want"),
    [("v4.0", "net462"), ("v4.5", "net462"), ("v4.6.1", "net462"), ("v3.5", "net462")],
)
def test_projects_below_462_retarget_to_net462(version: str, want: str) -> None:
    _, root = _plan(OLD_STYLE.replace("v4.0", version))
    assert _props(root)["TargetFramework"] == want


@pytest.mark.parametrize(
    ("version", "want"), [("v4.6.2", "net462"), ("v4.7.2", "net472"), ("v4.8", "net48")]
)
def test_projects_at_or_above_462_keep_their_framework(version: str, want: str) -> None:
    _, root = _plan(OLD_STYLE.replace("v4.0", version))
    assert _props(root)["TargetFramework"] == want


def test_missing_target_framework_version_retargets_to_net462() -> None:
    text = OLD_STYLE.replace("    <TargetFrameworkVersion>v4.0</TargetFrameworkVersion>\n", "")
    _, root = _plan(text)
    assert _props(root)["TargetFramework"] == "net462"


def test_xrm_client_reference_is_manual_review_and_kept() -> None:
    text = OLD_STYLE.replace(
        '<Reference Include="System" />',
        '<Reference Include="Microsoft.Xrm.Client, Version=5.0.0.0">\n'
        "      <HintPath>..\\sdk\\bin\\Microsoft.Xrm.Client.dll</HintPath>\n"
        "    </Reference>\n"
        '    <Reference Include="System" />',
    )
    plan, root = _plan(text)
    (item,) = plan.review
    assert "Microsoft.Xrm.Client" in item.reason and item.line is not None
    assert text.splitlines()[item.line - 1].count("Microsoft.Xrm.Client") == 1
    assert "Microsoft.Xrm.Client, Version=5.0.0.0" in _refs(root)


def test_workflow_reference_is_manual_review_and_kept() -> None:
    text = OLD_STYLE.replace(
        '<Reference Include="System" />',
        '<Reference Include="Microsoft.Xrm.Sdk.Workflow, Version=5.0.0.0" />\n'
        '    <Reference Include="System" />',
    )
    plan, root = _plan(text)
    (item,) = plan.review
    assert "Microsoft.Xrm.Sdk.Workflow" in item.reason
    assert "Microsoft.Xrm.Sdk.Workflow, Version=5.0.0.0" in _refs(root)


def test_ilmerge_is_manual_review_and_the_merge_step_is_kept() -> None:
    text = OLD_STYLE.replace(
        '<Import Project="$(MSBuildToolsPath)\\Microsoft.CSharp.targets" />',
        '<Import Project="$(MSBuildToolsPath)\\Microsoft.CSharp.targets" />\n'
        '  <Import Project="..\\.build\\ILMerge.targets" />',
    )
    plan, root = _plan(text)
    (item,) = plan.review
    assert "ILMerge" in item.reason and item.line is not None
    assert [i.get("Project") for i in root.iter("Import")] == ["..\\.build\\ILMerge.targets"]


def test_a_project_with_no_sdk_reference_gets_no_package() -> None:
    text = OLD_STYLE.replace("Microsoft.Xrm.Sdk,", "Contoso.Other,").replace(
        "Microsoft.Crm.Sdk.Proxy,", "Contoso.Third,"
    )
    _, root = _plan(text)
    assert list(root.iter("PackageReference")) == []


@pytest.mark.parametrize(
    ("text", "reason"),
    [
        (OLD_STYLE.replace("<OutputType>Library", "<OutputType>Exe"), "class library"),
        (
            OLD_STYLE.replace("<Project ", '<Project Sdk="Microsoft.NET.Sdk" ', 1),
            "already SDK-style",
        ),
        (OLD_STYLE.replace("v4.0", "four"), "TargetFrameworkVersion"),
        (
            OLD_STYLE.replace(
                "<OutputType>", "<ProjectTypeGuids>{3AC096D0}</ProjectTypeGuids><OutputType>"
            ),
            "ProjectTypeGuids",
        ),
        ("<Project", "well-formed"),
        ('<Other xmlns="x"/>', "not an MSBuild project"),
    ],
)
def test_unprovable_projects_are_untouched_and_flagged_as_a_whole(text: str, reason: str) -> None:
    plan = plan_csproj(text)
    assert plan.patched is None
    (item,) = plan.review
    assert item.line is None and reason in item.reason


def test_entity_declarations_are_refused_not_expanded() -> None:
    text = '<!DOCTYPE p [<!ENTITY x "y">]><Project xmlns="http://schemas.microsoft.com/developer/msbuild/2003"/>'
    plan = plan_csproj(text)
    assert plan.patched is None and "well-formed" in plan.review[0].reason


def test_references_inside_a_choose_block_are_replaced_and_reviewed() -> None:
    text = OLD_STYLE.replace(
        '<Import Project="$(MSBuildToolsPath)\\Microsoft.CSharp.targets" />',
        "<Choose><When Condition=\" '$(X)' == '1' \"><ItemGroup>\n"
        '<Reference Include="Microsoft.Xrm.Sdk, Version=5.0.0.0" />\n'
        '<Reference Include="Microsoft.Xrm.Client, Version=5.0.0.0" />\n'
        "</ItemGroup></When></Choose>\n"
        '<Import Project="$(MSBuildToolsPath)\\Microsoft.CSharp.targets" />',
    )
    plan, root = _plan(text)
    assert "Microsoft.Xrm.Sdk, Version=5.0.0.0" not in _refs(root)
    assert "Microsoft.Xrm.Client, Version=5.0.0.0" in _refs(root)
    (item,) = plan.review
    assert "Microsoft.Xrm.Client" in item.reason


def test_a_project_type_guid_inside_a_choose_block_is_still_a_whole_file_review() -> None:
    text = OLD_STYLE.replace(
        '<Import Project="$(MSBuildToolsPath)\\Microsoft.CSharp.targets" />',
        "<Choose><When Condition=\" '$(X)' == '1' \"><PropertyGroup>"
        "<ProjectTypeGuids>{3AC096D0}</ProjectTypeGuids>"
        "</PropertyGroup></When></Choose>\n"
        '<Import Project="$(MSBuildToolsPath)\\Microsoft.CSharp.targets" />',
    )
    plan = plan_csproj(text)
    assert plan.patched is None and "ProjectTypeGuids" in plan.review[0].reason
