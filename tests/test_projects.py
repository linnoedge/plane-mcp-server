"""Tests for project compatibility helpers."""

from plane_mcp.tools.projects import _filter_projects_from_pages


def test_filter_projects_from_pages_scans_across_cursors_and_searches_like():
    pages = [
        {
            "results": [
                {"id": "project-1", "name": "Alpha", "identifier": "ALP", "description": "First"},
            ],
            "next_cursor": "page-2",
            "next_page_results": True,
            "total_count": 3,
        },
        {
            "results": [
                {"id": "project-2", "name": "BanhMi", "identifier": "BAM", "description": "Slack gratitude app"},
                {"id": "project-3", "name": "Famina", "identifier": "FAM", "description": "Family care"},
            ],
            "next_cursor": "page-3",
            "next_page_results": True,
            "total_count": 3,
        },
    ]
    requested_cursors = []

    def fetch_page(cursor):
        requested_cursors.append(cursor)
        return pages[len(requested_cursors) - 1]

    result = _filter_projects_from_pages(
        fetch_page=fetch_page,
        query="banh",
        identifier=None,
        is_member=None,
        archived=None,
        lead_id=None,
        member_id=None,
        limit=1,
        max_pages=5,
    )

    assert [project["id"] for project in result["results"]] == ["project-2"]
    assert requested_cursors == [None, "page-2"]
    assert result["pages_scanned"] == 2
    assert result["total_scanned"] == 3
    assert result["next_cursor"] == "page-3"
    assert result["has_more"] is True
