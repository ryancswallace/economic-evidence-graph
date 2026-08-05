"""Run a bounded literature search through the public Python client."""

from economic_evidence_graph import EegClient
from economic_evidence_graph.api.schemas import SearchCreateRequest


def main() -> None:
    """Print stable identifiers from at most three result pages."""
    request = SearchCreateRequest(
        query="forward guidance inflation expectations",
        limit=10,
        save_run=True,
    )
    with EegClient(actor_id="ra-example") as client:
        for result in client.iter_search(request, max_pages=3):
            print(result.work_id, result.document_version_id, result.title)


if __name__ == "__main__":
    main()
