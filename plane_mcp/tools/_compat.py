from typing import Any


def paginated_payload(results: list[Any]) -> dict[str, Any]:
    count = len(results)
    return {
        "grouped_by": None,
        "sub_grouped_by": None,
        "total_count": count,
        "next_cursor": "",
        "prev_cursor": "",
        "next_page_results": False,
        "prev_page_results": False,
        "count": count,
        "total_pages": 1 if count else 0,
        "total_results": count,
        "results": results,
    }
