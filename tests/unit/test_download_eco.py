import pytest

from scripts.download_eco import BASE, ECO_SOURCE_COMMIT, ECO_SOURCE_REPOSITORY


@pytest.mark.unit
def test_eco_download_source_is_an_immutable_commit() -> None:
    assert ECO_SOURCE_REPOSITORY == "https://github.com/lichess-org/chess-openings"
    assert ECO_SOURCE_COMMIT == "4b8622759e7ae6f93f011cc6c83a3823401ab45e"
    raw_repository = ECO_SOURCE_REPOSITORY.replace("github.com", "raw.githubusercontent.com")
    assert BASE == f"{raw_repository}/{ECO_SOURCE_COMMIT}"
    assert "/master" not in BASE
    assert "/main" not in BASE
