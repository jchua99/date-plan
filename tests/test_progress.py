"""Issue #15: the page says what's happening while a plan is made."""

import threading
import time

import pytest

import progress as stages
from app import create_app
from itinerary import Itinerary, Stop
from jobs import DONE, Jobs
from planner import PlanError, suggest_plan
from reservations import PlaceCheck
from tests.test_planner import GOOD, REQUEST, FakeClient
from tests.test_app import FORM


# --- Messages ---


@pytest.mark.parametrize(
    "places, said",
    [
        (["Muchacho"], "Muchacho has closed. Finding somewhere else…"),
        (["Muchacho", "Atlas"], "Muchacho and Atlas have closed. Finding somewhere else…"),
        (["A", "B", "C"], "A, B and C have closed. Finding somewhere else…"),
    ],
)
def test_closed_places_are_named(places, said):
    assert stages.replacing(places) == said


# --- The planner reports each step ---


def record():
    said = []
    return said, said.append


def test_planner_reports_writing_checking_and_booking_in_order():
    bookable = GOOD.model_copy(update={"stops": [GOOD.stops[0].model_copy(update={"reservable": True}), *GOOD.stops[1:]]})
    said, progress = record()
    suggest_plan(
        REQUEST,
        client=FakeClient(reply=bookable.model_dump_json()),
        link_checker=lambda it: it,
        place_checker=lambda it, area: [PlaceCheck(i, s.place) for i, s in enumerate(it.stops)],
        progress=progress,
    )
    assert said == [stages.WRITING, stages.CHECKING, stages.BOOKING]


def test_no_booking_step_when_nothing_is_bookable():
    said, progress = record()
    suggest_plan(REQUEST, client=FakeClient(), link_checker=lambda it: it, progress=progress)
    assert said == [stages.WRITING]


def test_planner_says_which_place_closed_before_writing_again():
    closed_plan = Itinerary(title="T", stops=[
        *GOOD.stops[:2],
        Stop(time="16:30", activity="Dinner", place="Muchacho, 60 Tras Street", note="n"),
        GOOD.stops[2].model_copy(update={"time": "18:00"}),
    ])
    replies = [closed_plan.model_dump_json(), GOOD.model_dump_json()]
    client = FakeClient()
    original = client._create

    def create(**kwargs):
        client._reply = replies.pop(0)
        return original(**kwargs)

    client.chat.completions.create = create

    def google(it, area):
        return [PlaceCheck(i, s.place, "has closed for good" if s.place.startswith("Muchacho") else "") for i, s in enumerate(it.stops)]

    said, progress = record()
    suggest_plan(REQUEST, client=client, link_checker=lambda it: it, place_checker=google, progress=progress)
    assert said == [stages.WRITING, stages.CHECKING, "Muchacho has closed. Finding somewhere else…", stages.CHECKING]


# --- Jobs ---


def test_job_reports_progress_then_done():
    jobs = Jobs(run_in_background=False)
    seen = []

    def work(report):
        report("step one")
        seen.extend(job.message for job in jobs._jobs.values())  # what /plan/status would show now
        return {"form": {}}, 200

    job = jobs.start(work, fallback_page={})
    assert seen == ["step one"]
    assert job.state == DONE and job.message == stages.DONE and job.status_code == 200


def test_background_job_finishes_on_its_own():
    jobs = Jobs()
    job = jobs.start(lambda report: (report("working"), ({"form": {}}, 200))[1], fallback_page={})
    for _ in range(100):
        if job.state == DONE:
            break
        time.sleep(0.01)
    assert job.state == DONE and jobs.get(job.id) is job


def test_crashing_job_ends_with_a_friendly_error():
    def boom(report):
        raise RuntimeError("bug")

    job = Jobs(run_in_background=False).start(boom, fallback_page={"form": {"area": "x"}})
    assert job.state == DONE and job.status_code == 500
    assert job.page["form"] == {"area": "x"} and "Something went wrong" in job.page["error"]


@pytest.mark.parametrize("stop", [KeyboardInterrupt, SystemExit])
def test_interrupted_job_still_ends_then_lets_the_interrupt_through(stop):
    """Issue #31: a BaseException used to leave the job running forever."""
    jobs = Jobs(run_in_background=False)

    def interrupted(report):
        raise stop()

    with pytest.raises(stop):
        jobs.start(interrupted, fallback_page={"form": {"area": "x"}})
    [job] = jobs._jobs.values()
    assert job.state == DONE and job.status_code == 500 and "Something went wrong" in job.page["error"]


def slow_app(time_limit):
    """An app whose plans block until `release` is set."""
    release = threading.Event()

    def planner(request, progress):
        progress(stages.WRITING)
        release.wait(5)
        return GOOD

    jobs = Jobs(time_limit=time_limit)
    return create_app(planner=planner, travel=lambda it, req: None, jobs=jobs).test_client(), release


def test_plan_past_the_time_limit_ends_with_a_friendly_error():
    client, release = slow_app(time_limit=0.1)
    try:
        urls = client.post("/plan/start", data=FORM).get_json()
        assert client.get(urls["status_url"]).get_json()["done"] is False
        time.sleep(0.2)
        status = client.get(urls["status_url"])
        assert status.status_code == 200 and status.get_json() == {"done": True, "message": "This is taking too long. Please try again."}
        page = client.get(urls["result_url"])
        assert page.status_code == 504 and "This is taking too long" in page.get_data(as_text=True)
    finally:
        release.set()


def test_late_result_after_the_time_limit_is_ignored():
    jobs = Jobs(time_limit=0.1)
    release = threading.Event()
    job = jobs.start(lambda report: (release.wait(5), report("late step"), ({"form": {}, "late": True}, 200))[2], fallback_page={"form": {}})
    time.sleep(0.2)
    assert jobs.get(job.id).status_code == 504
    release.set()
    for _ in range(100):
        time.sleep(0.01)
    assert job.status_code == 504 and "late" not in job.page and job.message == "This is taking too long. Please try again."


def test_plan_within_the_time_limit_is_untouched():
    client, release = slow_app(time_limit=60)
    release.set()
    urls = client.post("/plan/start", data=FORM).get_json()
    for _ in range(200):
        if client.get(urls["status_url"]).get_json()["done"]:
            break
        time.sleep(0.01)
    page = client.get(urls["result_url"])
    assert page.status_code == 200 and GOOD.title in page.get_data(as_text=True)


def test_page_gives_up_waiting_after_a_few_minutes():
    page = client_with().get("/").get_data(as_text=True)
    assert "GIVE_UP_MS = 5 * 60 * 1000" in page and "This is taking too long. Please try again." in page


def test_job_ids_are_unguessable_and_old_jobs_are_forgotten():
    jobs = Jobs(keep_seconds=-1, run_in_background=False)  # every earlier job counts as old
    first = jobs.start(lambda r: ({}, 200), fallback_page={})
    assert len(first.id) >= 20
    jobs.start(lambda r: ({}, 200), fallback_page={})
    assert jobs.get(first.id) is None


# --- Routes ---


def planner_that_reports(request, progress):
    progress(stages.WRITING)
    progress(stages.CHECKING)
    return GOOD


def client_with(planner=planner_that_reports, travel=lambda it, req: None):
    return create_app(planner=planner, travel=travel, jobs=Jobs(run_in_background=False)).test_client()


def test_start_returns_where_to_follow_the_plan():
    reply = client_with().post("/plan/start", data=FORM)
    assert reply.status_code == 202
    urls = reply.get_json()
    assert urls["status_url"].startswith("/plan/status/") and urls["result_url"].startswith("/plan/result/")


def test_status_then_result_show_the_finished_plan():
    client = client_with()
    urls = client.post("/plan/start", data=FORM).get_json()
    status = client.get(urls["status_url"])
    assert status.status_code == 200 and status.get_json() == {"done": True, "message": stages.DONE}
    page = client.get(urls["result_url"])
    assert page.status_code == 200 and GOOD.title in page.get_data(as_text=True)


def test_progress_messages_reach_the_status_endpoint():
    jobs = Jobs(run_in_background=False)
    app = create_app(planner=planner_that_reports, travel=lambda it, req: None, jobs=jobs)
    messages = []
    original_start = jobs.start

    def spying_start(work, fallback_page):
        return original_start(lambda report: work(lambda m: (messages.append(m), report(m))), fallback_page)

    jobs.start = spying_start
    app.test_client().post("/plan/start", data=FORM)
    assert messages == [stages.WRITING, stages.CHECKING, stages.TRAVEL]


def test_planner_problem_shows_on_the_result_page_with_its_status():
    def failing(request, progress):
        raise PlanError("Couldn't make a plan this time. Please try again.")

    client = client_with(planner=failing)
    urls = client.post("/plan/start", data=FORM).get_json()
    page = client.get(urls["result_url"])
    assert page.status_code == 502 and "Couldn&#39;t make a plan" in page.get_data(as_text=True)


def test_missing_fields_show_on_the_result_page():
    client = client_with()
    urls = client.post("/plan/start", data={**FORM, "area": " "}).get_json()
    page = client.get(urls["result_url"])
    assert page.status_code == 400 and "Please fill in" in page.get_data(as_text=True)


def test_unknown_job_says_so():
    client = client_with()
    status = client.get("/plan/status/nope")
    assert status.status_code == 404 and status.get_json()["done"] is True
    page = client.get("/plan/result/nope")
    assert page.status_code == 404 and "no longer available" in page.get_data(as_text=True)


def test_plain_form_post_still_works_without_the_script():
    reply = client_with().post("/plan", data=FORM)
    assert reply.status_code == 200 and GOOD.title in reply.get_data(as_text=True)


def test_page_has_the_live_status_and_hint():
    page = client_with().get("/").get_data(as_text=True)
    assert 'data-start-url="/plan/start"' in page
    assert 'id="plan-status" role="status" aria-live="polite"' in page
    assert "This can take about a minute" in page
    assert "'Planning…'" not in page
