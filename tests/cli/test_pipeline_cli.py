"""WI-09a: the pipeline's preflight against the REAL doctor (pairs the doctor stub of the contract cases).

The environment decides whether the real doctor passes (klode 0.5.0 is not in the venv until WI-15a), so the
case compares the pipeline with the doctor on the same machine: same verdict, 0 -> 0 and 40 -> 40, and the
doctor's own receipt becomes the preflight product.
"""

from __future__ import annotations

import json
import subprocess

import pytest

from _support import pipeline_fx as F
from _support.flows import REPO

pytestmark = pytest.mark.cli_integration


def test_preflight_is_the_real_doctor_verdict(tmp_path) -> None:
    rig = F.Rig(tmp_path)
    env = dict(rig.env)
    for var in ("KLODE_KIT_ENGINE_REPLAY", "KLODE_KIT_TEST_SEAMS"):
        env.pop(var, None)
    doctor = subprocess.run([str(REPO / "bin" / "klode-kit"), "doctor", "--project", str(rig.project)],
                            env=env, capture_output=True, timeout=300)
    assert doctor.returncode in (0, 40)
    res = subprocess.run(rig.argv("--stage", "preflight", launcher=False), env=env, capture_output=True,
                         timeout=300, cwd=str(rig.project))
    assert res.returncode == doctor.returncode, res.stderr.decode()
    rec = rig.receipt("preflight")
    assert rec["status"] == ("done" if doctor.returncode == 0 else "failed")
    assert rec["exit_code"] == doctor.returncode
    if doctor.returncode == 0:
        product = rig.project / rec["outputs"][0]["path"]
        assert json.loads(product.read_text(encoding="utf-8"))["schema"] == "klode-kit.doctor/1"
    else:
        assert not (rig.receipts_dir / "ocr.json").exists()  # nothing past a failed preflight
