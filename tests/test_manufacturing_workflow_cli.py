from pathlib import Path
import pytest
from scripts.accept_manufacturing_workflow import main


@pytest.mark.parametrize('extra',[[],['--apply']])
def test_paid_workflow_phase_requires_explicit_authorization_before_reading_private_files(extra):
    root=Path(__file__).resolve().parents[1]
    with pytest.raises(SystemExit) as error:
        main(['ask','--run-dir',str(root/'tmp/nonexistent-authorization-probe'),*extra])
    assert error.value.code==2
    assert not (root/'tmp/nonexistent-authorization-probe').exists()
