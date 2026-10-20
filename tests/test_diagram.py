from vizcontrol import diagram
from vizcontrol.cli import main
from vizcontrol.store import Reader


def test_level_1_and_2_as_mermaid(runs_dir):
    r = Reader(runs_dir / "vip.vizc")
    l1 = diagram.stages(r)
    assert l1.startswith("flowchart LR\n") and "classDef raised" in l1
    assert '["app.pricing<br/>' in l1 and ":::raised" in l1
    assert "decimal" not in l1  # the standard library is left out unless it raised
    assert "==>" in l1  # the main path
    l2 = diagram.stage(r, "app.services")
    assert l2.startswith("flowchart TB\n") and 'subgraph stage ["app.services"]' in l2
    assert '["class OrderService"]' in l2 and '["place_order<br/>' in l2 and '["app.repo<br/>stage"]' in l2
    assert l2.count("subgraph") == l2.count("\n  end") + l2.count("\n    end") + l2.count("\n      end")
    assert diagram.stage(r, "nope") is None


def test_diagram_command(runs_dir, capsys):
    assert main(["diagram", str(runs_dir / "basic.vizc"), "--stage", "app.api"]) == 0
    assert capsys.readouterr().out.startswith("flowchart TB")
    assert main(["diagram", str(runs_dir / "basic.vizc"), "--stage", "nope"]) == 1
    assert "app.services" in capsys.readouterr().err  # lists the stages there are
