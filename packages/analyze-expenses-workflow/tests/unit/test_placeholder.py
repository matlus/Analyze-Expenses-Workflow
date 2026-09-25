from analyze_expenses_workflow import __doc__ as library_doc


def test_library_package_imports() -> None:
    assert library_doc is not None
