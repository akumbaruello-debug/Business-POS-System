"""Test package marker — makes test subpackages importable as
``tests.*`` so mypy can disambiguate the multiple ``conftest`` modules
(tests/conftest.py vs tests/api/conftest.py vs tests/integration/conftest.py).
"""
