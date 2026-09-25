from analyze_expenses_workflow_app.main import main


def test_app_entry_point_imports() -> None:
    assert callable(main)
