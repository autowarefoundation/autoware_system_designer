from setuptools import setup, find_packages

setup(
    name="autoware_system_designer_pre_commit_dummy",
    version="0.0.0",
    description="Dummy package to satisfy pre-commit pip install .",
    packages=[],  # Explicitly empty to avoid auto-discovery errors
    py_modules=[], # Explicitly empty
)
