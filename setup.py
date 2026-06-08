from setuptools import setup, find_packages

setup(
    name="crm",
    version="1.2.0",
    description="Stateful CLI harness for Microsoft Dynamics 365 Customer Engagement (on-premises) v9.x Web API",
    long_description=open("README.md", encoding="utf-8").read() if __import__("os").path.exists("README.md") else "",
    long_description_content_type="text/markdown",
    author="Ahmed Gharib",
    license="MIT",
    python_requires=">=3.9",
    packages=find_packages(),
    package_data={
        "crm": ["skills/*.md", "README.md"],
    },
    install_requires=[
        "click>=8.0",
        "requests>=2.28",
        "requests_ntlm>=1.2",
        "prompt_toolkit>=3.0",
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
            "mkdocs>=1.6",
            "mkdocs-material>=9.5",
            "mkdocs-click>=0.8",
            "mkdocs-include-markdown-plugin>=7",
            "mkdocs-llmstxt>=0.2",
        ],
    },
    entry_points={
        "console_scripts": [
            "crm = crm.cli:cli",
        ],
    },
    classifiers=[
        "Programming Language :: Python :: 3",
        "License :: OSI Approved :: MIT License",
        "Operating System :: OS Independent",
        "Environment :: Console",
    ],
)
