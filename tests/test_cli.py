from vizcontrol.cli import main


def test_diff_command(runs_dir, capsys):
    basic, vip = str(runs_dir / "basic.vizc"), str(runs_dir / "vip.vizc")
    assert main(["diff", basic, vip]) == 1
    out = capsys.readouterr().out
    assert "first divergence in compute" in out and "only in B load_vip_rules" in out
    assert "~ order.customer.tier 'basic' -> 'vip'" in out and "# B" in out
    assert main(["diff", basic, basic]) == 0
    assert "different result 0" in capsys.readouterr().out
    assert main(["diff", basic, str(runs_dir / "nope.vizc")]) == 2
