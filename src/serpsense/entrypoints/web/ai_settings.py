"""Settings → AI (BUILD_PLAN §7.2, §13): the user's preset and, per task, a model and effort of
their own. CSRF-checked; combinations a model refuses are explained and not saved."""

from collections.abc import Mapping
from typing import Annotated

from fastapi import APIRouter, Form, Request, Response, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

from serpsense.domain.enums import LlmTask
from serpsense.domain.llm_capabilities import Effort, LlmPreset
from serpsense.entrypoints.web.pages import signed_in_page
from serpsense.entrypoints.web.session import container, current_user, require_csrf
from serpsense.services.ai_settings import Saved
from serpsense.services.sessions import CurrentUser

router = APIRouter(prefix="/settings/ai")
SEE_OTHER = status.HTTP_303_SEE_OTHER
SAID: Mapping[str, str] = {
    Saved.SAVED: "Saved: the next calls use these settings.",
    Saved.UNCHANGED: "Nothing changed.",
}
TASK_NAMES: Mapping[LlmTask, str] = {
    LlmTask.LABEL_MENTIONS: "Label mentions",
    LlmTask.CLASSIFY_AUTOCOMPLETE: "Classify autocomplete",
    LlmTask.ASSESS_AI_OVERVIEW: "Assess the AI Overview",
    LlmTask.GROUP_NARRATIVES: "Group stories",
    LlmTask.EXPLAIN_CRISIS: "Explain a crisis",
    LlmTask.DRAFT_RESPONSE: "Draft a response",
}
NOT_USED_YET = frozenset({LlmTask.CLASSIFY_AUTOCOMPLETE, LlmTask.ASSESS_AI_OVERVIEW})  # no prompt
MODEL_NAMES = {
    "claude-opus-5-5": "Opus 5.5",
    "claude-sonnet-5-5": "Sonnet 5.5",
    "claude-haiku-4-5": "Haiku 4.5",
}


class AiForm(BaseModel):
    """The form as posted: per task, an empty model means "the preset's"."""

    csrf_token: str = ""
    preset: LlmPreset
    model_label_mentions: str = ""
    effort_label_mentions: str = ""
    model_classify_autocomplete: str = ""
    effort_classify_autocomplete: str = ""
    model_assess_ai_overview: str = ""
    effort_assess_ai_overview: str = ""
    model_group_narratives: str = ""
    effort_group_narratives: str = ""
    model_explain_crisis: str = ""
    effort_explain_crisis: str = ""
    model_draft_response: str = ""
    effort_draft_response: str = ""

    def picked(self) -> dict[LlmTask, tuple[str, str]]:
        fields = self.model_dump()
        return {
            task: (fields[f"model_{task.value}"], fields[f"effort_{task.value}"])
            for task in LlmTask
        }


@router.get("")
def ai_page(request: Request, saved: str = "") -> Response:
    user = current_user(request)
    if user is None:
        return RedirectResponse("/login", SEE_OTHER)
    return _page(request, user, notice=SAID.get(saved))


@router.post("")
def save_ai(request: Request, form: Annotated[AiForm, Form()]) -> Response:
    user = current_user(request)
    if user is None:
        return RedirectResponse("/login", SEE_OTHER)
    require_csrf(request, user, form.csrf_token)
    outcome = container(request).ai_settings.save(user.user_id, form.preset, form.picked())
    if outcome.reason is not None:
        return _page(request, user, notice=f"Not saved: {outcome.reason}", status_code=400)
    return RedirectResponse(f"/settings/ai?saved={outcome.saved.value}", SEE_OTHER)


def _page(
    request: Request, user: CurrentUser, *, notice: str | None, status_code: int = 200
) -> Response:
    view = container(request).ai_settings.view(user.user_id)
    return signed_in_page(
        request,
        user,
        "ai_settings.html",
        view=view,
        notice=notice,
        presets=list(LlmPreset),
        efforts=list(Effort),
        models=MODEL_NAMES,
        tasks=TASK_NAMES,
        not_used=NOT_USED_YET,
        status_code=status_code,
    )
