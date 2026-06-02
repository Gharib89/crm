from setuptools import setup, find_packages

setup(
    name="crm",
    version="0.7.0",
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
    ],
    extras_require={
        "dev": ["pytest>=7.0", "requests_mock>=1.10", "pyinstaller>=6.0", "pyright>=1.1.380"],
        "kerberos": ["requests_negotiate_sspi"],
        "docs": ["mkdocs>=1.6", "mkdocs-material>=9.5", "mkdocs-click>=0.8"],
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
