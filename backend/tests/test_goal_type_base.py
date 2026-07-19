"""
Tests for the GoalTypeBase abstract contract.

The base class defines the interface that all goal-type plugins must satisfy:
- ``verify(proof_data, criteria_data) -> dict`` (abstract)
- ``submit_proof`` (raises RuntimeError if not overridden)
"""

import pytest
from app.goal_types.base import GoalTypeBase


class MinimalGoalType(GoalTypeBase):
    """A concrete implementation used only by these tests."""

    def verify(self, proof_data: dict, criteria_data: dict) -> dict:
        return {"verification_status": "verified", "verification_details": {}}


class TestGoalTypeBaseContract:
    def test_cannot_instantiate_abstract_base(self):
        """GoalTypeBase is abstract and cannot be instantiated directly."""
        with pytest.raises(TypeError):
            GoalTypeBase()  # type: ignore[abstract]

    def test_concrete_subclass_with_verify_can_be_instantiated(self):
        """A subclass implementing verify() should be instantiable."""
        instance = MinimalGoalType()
        assert isinstance(instance, GoalTypeBase)

    def test_subclass_missing_verify_cannot_be_instantiated(self):
        """A subclass that does NOT implement verify() should not be instantiable."""

        class Incomplete(GoalTypeBase):
            pass  # no verify()

        with pytest.raises(TypeError):
            Incomplete()  # type: ignore[abstract]

    def test_verify_returns_dict(self):
        """verify() must return a dict with verification_status."""
        instance = MinimalGoalType()
        result = instance.verify(
            proof_data={"video_id": "abc123"},
            criteria_data={"min_duration_seconds": 300},
        )
        assert isinstance(result, dict)
        assert "verification_status" in result

    def test_default_submit_proof_raises_runtime_error(self):
        """submit_proof should raise RuntimeError if the subclass does not override it."""
        instance = MinimalGoalType()
        with pytest.raises(RuntimeError, match="has no submit_proof implementation"):
            instance.submit_proof({}, {})

    def test_submit_proof_can_be_overridden(self):
        """A subclass may override submit_proof."""

        class WithSubmit(MinimalGoalType):
            def submit_proof(self, proof_data, criteria_data):
                return {"submission_id": "sub-1", "verification_status": "pending"}

        instance = WithSubmit()
        result = instance.submit_proof({}, {})
        assert result["submission_id"] == "sub-1"


class TestGoalTypeBaseAttributes:
    def test_name_is_accessible(self):
        """A concrete goal type exposes its name."""
        instance = MinimalGoalType()
        instance.name = "test_type"
        assert instance.name == "test_type"
