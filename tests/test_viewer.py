import json
import re

from sessionfish.viewer import render_viewer


def test_report_is_embedded_and_cannot_break_out_of_the_script_tag(demo_report):
    report = {k: v for k, v in demo_report.items() if not k.startswith("_")}
    report["session"] = {**report["session"], "task": "</script><script>alert(1)</script>"}
    html = render_viewer(report)
    assert "</script><script>alert(1)" not in html
    data = re.search(r'<script id="data" type="application/json">(.*?)</script>', html, re.DOTALL).group(1)
    assert json.loads(data)["session"]["task"].startswith("</script>")
    assert "/*__DATA__*/" not in html
