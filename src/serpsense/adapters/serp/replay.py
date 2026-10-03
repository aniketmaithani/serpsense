"""A search provider that answers from replay recordings (`SERPSENSE_MODE=replay`): no SerpApi
key, no network, no searches spent (adapters/replay/recording.py).

Each request is answered with what the same request got in the recorded scans, in the order they
ran: the first time with the first scan's answer, then the next scan's, and once the recording
runs out, with its last answer again, so a replay plays once and then holds. How often a request
was answered before is read from the ledger (`times_answered`), so the place in the recording is
kept in Postgres and survives restarts; it belongs to the request, so whoever asks it next moves
it on. Answers come back as SerpApi's cache serves them
(`serpapi_cache`), which is never billed. A request no recording has fails like a 404, so its
surface ends up partial, as it would in a live scan.
"""

import copy
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from serpsense.adapters.replay.recording import Recording
from serpsense.domain.enums import SerpErrorCode, ServedFrom
from serpsense.ports.search_provider import SearchFailed, SearchRequest, SearchResponse


class ReplaySearchProvider:
    def __init__(
        self, recordings: Sequence[Recording], times_answered: Callable[[SearchRequest], int]
    ) -> None:
        answers: dict[str, list[Mapping[str, Any]]] = {}
        for recording in recordings:
            for scan in recording.scans:
                for answer in scan.answers:
                    payload = recording.payloads[answer.payload]
                    answers.setdefault(answer.request_hash, []).append(payload)
        self._answers = {key: tuple(payloads) for key, payloads in answers.items()}
        self._times_answered = times_answered

    def search(self, request: SearchRequest) -> SearchResponse:
        recorded = self._answers.get(request.params_hash)
        if not recorded:
            raise SearchFailed(SerpErrorCode.HTTP_4XX, http_status=404, latency_ms=0)
        place = min(self._times_answered(request), len(recorded) - 1)
        return SearchResponse(
            payload=copy.deepcopy(recorded[place]),  # each caller gets its own copy
            served_from=ServedFrom.SERPAPI_CACHE,
            http_status=200,
            latency_ms=0,
        )
