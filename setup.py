"""Packaging for crm.

The why behind each requirement, which the code cannot show:

- ``evals/`` is excluded from the packages: it is the skill-eval harness
  (ADR 0015), maintainer tooling run on demand, never shipped in the wheel.
- click's floor is 8.4.1. 8.4.0 first ships ``click.exceptions.NoSuchCommand``
  and its "Did you mean ...?" suggestions, which the root group's
  ``resolve_command`` override relies on (crm/cli.py); 8.4.0 also broke
  ``get_parameter_source()`` inside eager callbacks (pallets/click#3458), which
  silently disabled leaf-position global options such as
  ``crm profile list --json`` (ADR 0025).
- questionary drives the inline arrow-key pickers (profile use, the add
  wizard); it builds on prompt_toolkit and is imported lazily.
- keyring is core, not an extra: the binary and ``uv tool install`` paths cannot
  add an extra later, so ``connection set-password`` would be unreachable
  there. On Windows it pulls only pywin32-ctypes and the always-present
  Credential Manager backend. The empty ``keyring`` extra is a back-compat
  no-op so an existing ``crm[keyring]`` install command still resolves.
- jq is the libjq binding behind the global ``--jq`` shaper (#736), imported
  only when ``--jq`` is passed; its wheels cover Python 3.13 on all three
  release OSes (verified at v1.11.0).
- defusedxml parses org-supplied XML (solution archives, formxml, fetchxml,
  CSDL, CLI inputs) so a hostile document cannot carry an entity-expansion
  bomb (crm/utils/safe_xml.py, #838).
- tree-sitter and its grammars give ``crm inventory convert`` real syntax trees
  (ADR 0034), imported lazily in crm/core/code_parse.py; their wheels cover
  Python 3.13 on Linux, Windows and macOS. tree-sitter is capped
  below 0.26 because 0.26.0 segfaults the JavaScript binding intermittently
  (#1151); lift the cap only after a repeated ``_plan_global_context`` probe on
  the newer release runs clean.
- dnfile reads the .NET metadata tables of a registered plug-in assembly
  (``crm inventory assess``, rule plugin-binary-sdk-net462), imported lazily in
  crm/core/inventory_assembly.py.
- Every lazy import named here keeps the CLI's cold start cheap (ADR 0029).
- In ``dev``: pyright is deliberately absent and ruff is pinned exact, in
  lockstep with the ruff-pre-commit rev; see ADR 0038. pytest-testmon selects
  the affected tests on the harness turn rung. pytest-xdist runs CI's suite in
  parallel with ``-n auto`` in ci.yml only, since testmon does not support
  xdist, so ``-n`` never goes in addopts. jsonschema validates the shipped
  inventory manifest schema in tests only; the reader in crm/core/inventory.py
  checks structure without it.
- mkdocs is bounded below 2 against the 2.0 rewrite, which is incompatible
  with Material and removes the plugin system (ADR 0005).
"""

from setuptools import find_packages, setup

setup(
    name="crm",
    version="1.128.10",
    description=(
        "Stateful CLI harness for Microsoft Dynamics 365 Customer Engagement — on-prem "
        "v9.x (NTLM) or Dataverse online (OAuth), over the Web API"
    ),
    long_description=open("README.md", encoding="utf-8").read()
    if __import__("os").path.exists("README.md")
    else "",
    long_description_content_type="text/markdown",
    author="Ahmed Gharib",
    license="PolyForm-Noncommercial-1.0.0",
    license_files=["LICENSE"],
    python_requires=">=3.13",
    packages=find_packages(exclude=["evals", "evals.*"]),
    package_data={
        "crm": [
            "skills/*.md",
            "skills/reference/*.md",
            "migration-skill/*.md",
            "migration-skill/reference/*.md",
            "README.md",
            "schemas/*.json",
            "rules/*.json",
        ],
    },
    install_requires=[
        "click>=8.4.1",
        "requests>=2.28",
        "requests_ntlm>=1.2",
        "prompt_toolkit>=3.0",
        "questionary>=2.0",
        "msal>=1.20",
        "PyYAML>=6.0",
        "keyring>=24",
        "jq>=1.8",
        "defusedxml>=0.7",
        "tree-sitter>=0.25,<0.26",
        "tree-sitter-javascript>=0.25",
        "tree-sitter-c-sharp>=0.23",
        "dnfile>=0.18",
    ],
    extras_require={
        "dev": [
            "pytest>=7.0",
            "pytest-cov>=5.0",
            "requests_mock>=1.10",
            "pyinstaller>=6.0",
            "pytest-testmon==2.2.0",
            "pytest-xdist==3.8.0",
            "ruff==0.15.21",
            "pre-commit>=4.0",
            "jsonschema>=4.18",
        ],
        "kerberos": ["requests_negotiate_sspi"],
        "keyring": [],
        "docs": [
            "mkdocs>=1.6,<2",
            "mkdocs-material>=9.5,<10",
            "mkdocs-click>=0.8",
            "mkdocs-include-markdown-plugin>=7",
            "mkdocs-llmstxt>=0.2",
        ],
    },
    entry_points={
        "console_scripts": [
            "crm = crm.cli:main",
        ],
    },
    classifiers=[
        "Programming Language :: Python :: 3",
        "License :: Other/Proprietary License",
        "Operating System :: OS Independent",
        "Environment :: Console",
    ],
)
