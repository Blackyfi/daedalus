from daedalus.api.routes.internal import _idea_to_task_fields


def test_idea_to_task_fields_splits_title_description_and_acceptance() -> None:
    title, description, acceptance = _idea_to_task_fields(
        "Ship dashboard\nAdd project and task views\nAcceptance: Users can launch runs from the browser"
    )

    assert title == "Ship dashboard"
    assert description == "Add project and task views"
    assert acceptance == "Users can launch runs from the browser"


def test_idea_to_task_fields_handles_empty_input() -> None:
    title, description, acceptance = _idea_to_task_fields("   ")

    assert title == "Untitled task"
    assert description == ""
    assert acceptance == "Deliver the requested change."
