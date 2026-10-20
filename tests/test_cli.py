from pathlib import Path

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


def vizcontrol(tmp_path, *args):
    import subprocess
    import sys

    return subprocess.run([sys.executable, "-m", "vizcontrol", *args], cwd=tmp_path, capture_output=True, text=True,
                          timeout=60)


def test_run_says_what_to_do_next_and_when_nothing_was_traced(tmp_path):
    (tmp_path / "hello.py").write_text("def hi():\n    return 1\n\n\nhi()\n")
    err = vizcontrol(tmp_path, "run", "hello.py").stderr
    assert "trace written to .vizcontrol/runs/" in err and "explore it with: vizcontrol serve " in err
    err = vizcontrol(tmp_path, "run", "-o", "out/h.vizc", "hello.py").stderr
    assert "vizcontrol serve out" in err  # not the default directory: say which
    (tmp_path / "data.json").write_text("[1]")
    err = vizcontrol(tmp_path, "run", "-m", "json.tool", "data.json").stderr  # the stdlib is never traced
    assert "nothing was traced" in err and "--include" in err


def test_run_serve_opens_the_new_run(tmp_path, monkeypatch):
    import sys

    from vizcontrol import server

    served = []
    monkeypatch.setattr(server, "serve", lambda path, **kw: served.append((path, kw)) or 0)
    monkeypatch.setattr(sys, "argv", list(sys.argv))
    monkeypatch.setattr(sys, "path", list(sys.path))
    monkeypatch.chdir(tmp_path)
    (tmp_path / "boom.py").write_text("raise SystemExit(3)\n")
    assert main(["run", "--serve", "-o", "runs/b.vizc", "boom.py"]) == 3  # the program's exit code
    assert served == [(Path("runs"), {"run": "b.vizc"})]


def test_serve_without_runs_says_how_to_make_one(tmp_path):
    proc = vizcontrol(tmp_path, "serve", "--no-browser")
    assert proc.returncode == 1 and "no runs yet" in proc.stderr and "vizcontrol run" in proc.stderr
