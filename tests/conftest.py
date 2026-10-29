import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from fake_comfy import FakeComfy, load_object_info  # noqa: E402


@pytest.fixture(scope="session")
def oi():
    return load_object_info()


@pytest.fixture()
def project(tmp_path):
    """Cópia do projeto (só `defaults/`; `custom/` começa vazio) para testes que escrevem."""
    shutil.copytree(ROOT / "defaults", tmp_path / "defaults")
    (tmp_path / "data").mkdir()
    (tmp_path / "web").mkdir()
    (tmp_path / "web" / "index.html").write_text("<html></html>")
    return tmp_path


@pytest.fixture()
def comfy(oi):
    c = FakeComfy(oi)
    yield c
    c.stop()


@pytest.fixture(autouse=True)
def _no_real_comfyui_on_this_machine(monkeypatch):
    """Os testes nunca podem achar (e mexer em) o ComfyUI de verdade de quem roda a suíte."""
    monkeypatch.setattr("pickflow.setup._heuristic_roots", lambda: [])
