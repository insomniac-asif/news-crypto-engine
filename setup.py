from setuptools import setup, find_packages

setup(
    name="news-crypto-engine",
    version="0.1.0",
    description="News-driven crypto research engine",
    author="Your Name",
    python_requires=">=3.10",
    packages=find_packages(where="src"),
    package_dir={"": "src"},
    install_requires=[
        "requests>=2.31.0",
        "feedparser>=6.0.10",
        "praw>=7.7.0",
        "schedule>=1.2.0",
        "aiohttp>=3.9.0",
        "spacy>=3.7.0",
        "nltk>=3.8.0",
        "pandas>=2.1.0",
        "numpy>=1.26.0",
        "streamlit>=1.29.0",
        "scipy>=1.11.0",
        "matplotlib>=3.8.0",
        "pyyaml>=6.0",
    ],
    extras_require={
        "dev": ["pytest>=7.4.0", "black>=23.0", "ruff>=0.1.0"],
    },
    entry_points={
        "console_scripts": [
            "nce-ingest=scripts.ingest:main",
            "nce-analyze=scripts.analyze:main",
            "nce-backtest=scripts.backtest:main",
        ],
    },
)
