"""
Setup script for Smart Telegram Bot
"""

from setuptools import setup, find_packages

with open("README.md", "r", encoding="utf-8") as fh:
    long_description = fh.read()

with open("requirements.txt", "r", encoding="utf-8") as fh:
    requirements = [line.strip() for line in fh if line.strip() and not line.startswith("#")]

setup(
    name="BydlanBot",
    version="1.0.0",
    author="R0D10Nq",
    author_email="your.email@example.com",
    description="Умный Telegram бот с искусственным интеллектом и долговременной памятью",
    long_description=long_description,
    long_description_content_type="text/markdown",
    url="https://github.com/R0D10Nq/BydlanBot",
    packages=find_packages(),
    classifiers=[
        "Development Status :: 5 - Production/Stable",
        "Intended Audience :: Developers",
        "License :: OSI Approved :: MIT License",
        "Operating System :: OS Independent",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.8",
        "Programming Language :: Python :: 3.9",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
        "Topic :: Communications :: Chat",
        "Topic :: Scientific/Engineering :: Artificial Intelligence",
    ],
    python_requires=">=3.8",
    install_requires=requirements,
    entry_points={
        "console_scripts": [
            "BydlanBot=bot:main",
        ],
    },
    keywords="telegram bot ai memory nlp chatbot assistant",
    project_urls={
        "Bug Reports": "https://github.com/R0D10Nq/BydlanBot/issues",
        "Source": "https://github.com/R0D10Nq/BydlanBot",
        "Documentation": "https://github.com/R0D10Nq/BydlanBot#readme",
    },
)
