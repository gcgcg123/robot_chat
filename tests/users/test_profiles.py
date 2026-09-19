import pytest
from pydantic import ValidationError

from services.users.schemas import ProfileInput


def test_self_reported_age_is_optional_and_bounded():
    assert ProfileInput(display_name="測試甲", gender="不透露").age_at_registration is None
    with pytest.raises(ValidationError):
        ProfileInput(display_name="測試甲", gender="不透露", age_at_registration=121)
    with pytest.raises(ValidationError):
        ProfileInput(display_name="測試甲", gender="不透露", age_at_registration=20.5)

