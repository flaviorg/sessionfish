import subprocess
import sys

from tests.conftest import ROOT


def run(script, *args):
    return subprocess.run([sys.executable, str(ROOT / "scripts" / script), *args], capture_output=True, text=True)


def test_docs_embed_the_current_graphs():
    result = run("gen_graph.py", "--check")
    assert result.returncode == 0, result.stderr


def test_demo_transcript_matches_its_generator():
    result = run("make_fixture.py", "--check")
    assert result.returncode == 0, result.stderr
