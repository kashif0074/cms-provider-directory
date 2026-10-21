import sys
from search_service import search_web, SearchError

engine = sys.argv[1] if len(sys.argv) > 1 else "tavily"

try:
    results = search_web(
        "Mayo Clinic physician directory",
        engine=engine,
    )
    print(f"Engine: {engine}")
    print(f"Results returned: {len(results)}")
    for result in results:
        print()
        print(result["title"])
        print(result["url"])
except (SearchError, ValueError) as error:
    print(f"Search test failed: {error}")
    sys.exit(1)
