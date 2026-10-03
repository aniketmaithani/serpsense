"""A search provider that answers from replay recordings (`SERPSENSE_MODE=replay`): no SerpApi
key, no network, no searches spent (adapters/replay/recording.py).

Each request has one answer per recorded scan, by the time the scan ran: the one it got then, or
where that scan didn't ask it, the answer it got last before. A search is answered as of the
clock: with the request's answer from the latest recorded scan at or before now. The replay
loader sets the clock to each recorded scan's time, so every replayed scan gets that scan's
answers whichever order its searches run in, with no counting and so no race between scans; a
scan run later (Scan now) gets the last answers recorded. Answers come back as SerpApi's cache
serves them (`serpapi_cache`), which is never billed. A request with no answer by then fails
like a 404, so its surface ends up partial, as it would in a live scan.
"""

import copy
from bisect import bisect_right
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any

from serpsense.adapters.replay.recording import Recording
from serpsense.domain.enums import SerpErrorCode, ServedFrom
from serpsense.ports.clock import Clock
from serpsense.ports.search_provider import SearchFailed, SearchRequest, SearchResponse


class ReplaySearchProvider:
    def __init__(self, recordings: Sequence[Recording], clock: Clock) -> None:
        answers: dict[str, list[tuple[datetime, Mapping[str, Any]]]] = {}
        for recording in recordings:
            for scan in recording.scans:
                for answer in scan.answers:
                    payload = recording.payloads[answer.payload]
                    answers.setdefault(answer.request_hash, []).append((scan.recorded_at, payload))
        self._answers = {key: sorted(found, key=lambda a: a[0]) for key, found in answers.items()}
        self._clock = clock

    def search(self, request: SearchRequest) -> SearchResponse:
        recorded = self._answers.get(request.params_hash, [])
        latest = bisect_right([at for at, _ in recorded], self._clock.now())
        if latest == 0:
            raise SearchFailed(SerpErrorCode.HTTP_4XX, http_status=404, latency_ms=0)
        return SearchResponse(
            payload=copy.deepcopy(recorded[latest - 1][1]),  # each caller gets its own copy
            served_from=ServedFrom.SERPAPI_CACHE,
            http_status=200,
            latency_ms=0,
        )
