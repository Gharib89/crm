from setuptools import setup, find_packages

setup(
    name="crm",
    version="4.21.0",
    description="Stateful CLI harness for Microsoft Dynamics 365 Customer Engagement — on-prem v9.x (NTLM) or Dataverse online (OAuth), over the Web API",
    long_description=open("README.md", encoding="utf-8").read() if __import__("os").path.exists("README.md") else "",
    long_description_content_type="text/markdown",
    author="Ahmed Gharib",
    license="MIT",
    python_requires=">=3.9",
    packages=find_packages(),
    package_data={
        "crm": ["skills/*.md", "skills/reference/*.md", "README.md"],
    },
    install_requires=[
        # 8.4.0 first ships click.exceptions.NoSuchCommand and its
        # possibilities-based "Did you mean ...?" suggestion, which the root
        # group's resolve_command override relies on (crm/cli.py).
        "click>=8.4",
        "requests>=2.28",
        "requests_ntlm>=1.2",
        "prompt_toolkit>=3.0",
        # Inline arrow-key pickers (profile use / add wizard). Builds on
        # prompt_toolkit (above); lazy-imported so it stays off the fast path.
        "questionary>=2.0",
        "msal>=1.20",
        "PyYAML>=6.0",
        # Core (not an extra): the binary and `uv tool install` distribution
        # paths can't add an extra after the fact, so `connection set-password`
        # would be unreachable there. On Windows this pulls only pywin32-ctypes
        # (auto) and the always-present Credential Manager backend.
        "keyring>=24",
    ],
    extras_require={
        "dev": ["pytest>=7.0", "requests_mock>=1.10", "pyinstaller>=6.0", "pyright>=1.1.380"],
        "kerberos": ["requests_negotiate_sspi"],
        # Back-compat no-op: keyring is now a core dependency (above). Kept so
        # any existing `crm[keyring]` install command still resolves.
        "keyring": [],
        "docs": [
            # Upper-bounded against the breaking mkdocs 2.0 rewrite (incompatible
            # with Material, removes the plugin system) — see docs/adr/0005.
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
        "License :: OSI Approved :: MIT License",
        "Operating System :: OS Independent",
        "Environment :: Console",
    ],
)
