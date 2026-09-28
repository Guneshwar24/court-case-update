from courtwatch.cli import main
from courtwatch.db import Database


def test_import_and_list(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("COURTWATCH_DB", str(tmp_path / "t.db"))
    (tmp_path / "cases.csv").write_text(
        "name,phone,case,bench,label\n"
        "S K Gupta,9876543210,WP(C) 1234/2023,jammu,Ram Kumar\n"
        "S K Gupta,9876543210,OWP No. 55 of 2019,srinagar,\n"
        "Bad Row,9876543211,nonsense,,\n"
    )
    assert main(["import", "cases.csv"]) == 0
    out = capsys.readouterr()
    assert "imported 2 case(s)" in out.out
    assert "line 4: skipped" in out.err

    db = Database(str(tmp_path / "t.db"))
    cases = db.list_cases()
    assert [(c.case_type, c.case_no, c.case_year, c.bench) for c in cases] == [
        ("WP(C)", "1234", 2023, "jammu"), ("OWP", "55", 2019, "srinagar"),
    ]
    assert len(db.list_advocates()) == 1
