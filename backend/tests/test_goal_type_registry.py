"""
Tests for the goal-type registry: auto-discovery, validation, list_types(),
get_type(), and get_celery_include_modules().
"""

import pytest
from app.goal_types.registry import (
    get_celery_include_modules,
    get_type,
    list_types,
)


class TestRegistryListTypes:
    def test_list_types_returns_list_of_strings(self):
        """list_types() returns a sorted list of registered goal-type names."""
        types = list_types()
        assert isinstance(types, list)
        assert all(isinstance(t, str) for t in types)

    def test_all_four_core_types_are_registered(self):
        """The four core goal types must be present in the registry."""
        types = list_types()
        assert "youtube_video" in types
        assert "api_endpoint" in types
        assert "dev_sandbox" in types
        assert "github_repo" in types

    def test_list_types_is_sorted(self):
        """list_types() returns a sorted list."""
        types = list_types()
        assert types == sorted(types)


class TestRegistryGetType:
    def test_get_type_returns_instance_for_valid_name(self):
        """get_type() returns an object for each registered type."""
        for name in list_types():
            gt = get_type(name)
            assert gt is not None
            assert hasattr(gt, "name")
            assert gt.name == name

    def test_get_type_raises_keyerror_for_unknown_name(self):
        """get_type() raises KeyError for unregistered names."""
        with pytest.raises(KeyError):
            get_type("nonexistent_type_xyz")

    def test_each_type_has_description(self):
        """Every registered type should have a non-empty description."""
        for name in list_types():
            gt = get_type(name)
            assert hasattr(gt, "description")
            assert isinstance(gt.description, str)
            assert len(gt.description) > 0

    def test_each_type_has_sample_prompts(self):
        """Every registered type should have a list of sample_prompts."""
        for name in list_types():
            gt = get_type(name)
            assert hasattr(gt, "sample_prompts")
            assert isinstance(gt.sample_prompts, list)
            # sample_prompts may be empty but must be present

    def test_each_type_has_criteria_schema(self):
        """Every registered type should expose a criteria_schema dict."""
        for name in list_types():
            gt = get_type(name)
            assert hasattr(gt, "criteria_schema")
            assert isinstance(gt.criteria_schema, dict)

    def test_each_type_has_callable_verify(self):
        """Every registered type must have a callable verify."""
        for name in list_types():
            gt = get_type(name)
            assert hasattr(gt, "verify")
            assert callable(gt.verify)


class TestRegistryCeleryIncludeModules:
    def test_get_celery_include_modules_returns_list_of_strings(self):
        """get_celery_include_modules() returns a list of module path strings."""
        modules = get_celery_include_modules()
        assert isinstance(modules, list)
        assert all(isinstance(m, str) for m in modules)
        # Worker module paths follow the app.workers.X pattern
        for m in modules:
            assert m.startswith("app.workers.")

    def test_celery_modules_are_sorted(self):
        """get_celery_include_modules() returns a sorted list."""
        modules = get_celery_include_modules()
        assert modules == sorted(modules)