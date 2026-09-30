import pytest

from .manifest import ManifestError
from .matrix import validate_matrix


def _rows():
    return (
        [{"id": "cap.cli", "blocking": True}],
        [{"id": "AC-1", "capability_ids": ["cap.cli"]}],
        [{"id": "smoke", "acceptance_ids": ["AC-1"]}],
    )


def test_matrix_closure_accepts_complete_chain():
    validate_matrix(capabilities=_rows()[0], acceptances=_rows()[1], commands=_rows()[2])


@pytest.mark.parametrize("which", ["acceptance", "test"])
def test_matrix_closure_rejects_gaps(which):
    capabilities, acceptances, commands = _rows()
    if which == "acceptance":
        acceptances = []
    else:
        commands = []
    with pytest.raises(ManifestError):
        validate_matrix(capabilities=capabilities, acceptances=acceptances, commands=commands)
