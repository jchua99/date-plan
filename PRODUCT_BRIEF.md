# Date plan: product brief
**In one line:** helps two people who have just met turn a vague next meet-up into a shared plan they both had a hand in.

## Problem
Two people who have just met want to get to know each other well enough to work out whether there is a long-term relationship in it. They only know each other at surface level and find it hard to find common activities to bond over. They met on a dating platform with the intention of marrying, and if nothing changes they will go back to the platform and look for someone new.

## Evidence
- Feedback and a survey from members of the public saying it is hard to know people beyond the surface level.
- Couples spend about 5 hours together today, though your notes don't say over what period.
- Not checked yet: whether choosing activities is the real blocker, rather than something the pair would sort out themselves.

## Success
- **Metric:** time spent with each other (unit not set)
- **Today:** about 5 hours
- **Target:** not set
- **Must not get worse:** not set

## Riskiest bet
Couples who have just met see choosing shared activities as their real blocker, not something they would rather sort out themselves.
- **Test:** make itineraries by hand for 5 newly met pairs and send each as a note. No code needed; about a week.
- **Pass mark:** 3 of 5 pairs use the plan and say it helped.
- **Result:** not run yet

## First version
1. As one of two people who have just met, I open a suggested plan for our next meet-up. Done when I see a short itinerary with a few activities and times.
2. As one of the pair, I add my own input. Done when my changes show in the itinerary.
3. As one of the pair, I can follow the plan on the day. Done when the itinerary says what we are doing and when, so neither of us has to sort it out then.

## Walkthrough
1. The two of them open the planner together and talk about small things from their day.
2. The screen shows a sample itinerary for their next meet-up, with a few activities and times.
3. They adjust it with their own input so it fits them both.
4. They agree on it and leave with something to follow.

If it goes wrong: they see random date ideas that don't fit either of them, and your notes don't say what they do next.

## Build decisions
- A small Python (Flask) web page, run on a laptop, that the pair look at together on one screen. No logins.
- The GLM-5.3 Flash model (`glm-5.3-flash`), reached through OpenCode, writes the suggested itinerary. This needs an OpenCode API key. A plan that came back from any other model is rejected. (Changed from Claude, then Kimi K3, on 2026-11-11.)
- Story 1 asks the pair for only the day, the time they're free from, and an area in Singapore. It doesn't ask about their interests yet; fitting the plan to them both is story 2.
- A suggested plan has 3–5 stops, with times in order, over about 3–4 hours. Nothing is saved.
- Story 2 starts with food: the pair can pick a cuisine from a dropdown (No preference, Chinese, Malay, Indian, Peranakan, Japanese, Korean, Thai, Western, Mexican, Italian). "No preference" is the default and leaves the plan as before. When they pick one, the plan must include a meal of that cuisine, and the app loosely checks this: the cuisine's name must appear in some stop's activity, place or note, or the pair are asked to try again. Activity preferences are not built yet. (Decided 2026-11-11, issue #2.)
- **Travel by public transport (issue 3, decided 2026-11-11).** Supports story 3 (follow the plan on the day).
  - Travel comes from OneMap, Singapore's free government map service, not Google: Google needs a billed key and we chose not to set up billing. The app uses a OneMap token pasted into the git-ignored `.env` as `ONEMAP_API_TOKEN`. A token lasts 3 days; when it expires, travel info says to paste a new one. (Changed on 2026-11-11 from the app signing in with a OneMap email and password itself.)
  - The lookups run in Python on the server after the plan comes back, so they can be tested with a fake OneMap.
  - Below the plan is an interactive map (OneMap tiles) with numbered pins for each stop and the public-transport route drawn between them.
  - Each leg between stops shows one line: leave-by time, trip time, and the route with MRT lines and bus numbers (e.g. "Leave by 14:32 · 28 min · Walk 10 min → MRT NE Farrer Park → HarbourFront → Walk 8 min"), plus an "Open in Google Maps" transit link (needs no key).
  - "Leave by" is an estimate: the next stop's time minus the trip time. OneMap can't plan "arrive by". If leave-by is earlier than the current stop's start, the leg says the connection is tight. The model's stop times are never changed.
  - The form gets one optional, shared "Starting from" box. If filled in, there is also a leg from there to stop 1. It doesn't change which places the model picks.
  - Travel problems never block the plan. If a place isn't found, there's no route, the date is more than a month ahead (OneMap's limit), OneMap is down or the login isn't set up, that leg says it couldn't get the travel time and keeps its Google Maps link. The map shows the stops that were found and is hidden if none were.

- **Structured output (decided 2026-11-11).** The model's plan comes back already in our shape, instead of free text we hunt through for JSON.
  - The planner asks OpenCode for strict JSON-schema output (`response_format` `json_schema`, `strict`). This was checked with real calls to `glm-5.3-flash`: OpenCode really enforces the schema, even when the prompt asks for something else.
  - The schema comes from separate pydantic models that hold only what the model writes: title, and for each stop the time, activity, place, note, reservable and booking_url. Times must look like HH:MM, there must be 3–5 stops, and no extra keys are allowed. The model can't write `maps_url` or `checked`.
  - Cuisine stays a loose text check, not a schema enum.
  - `check_itinerary` stays as the safety net for rules a schema can't express: times in order, nothing before "free from", and the picked cuisine mentioned.
  - If OpenCode rejects the strict-schema option ("bad request"), that request falls back once to the old free-text JSON way and logs a warning. Nothing else triggers the fallback.
  - The model can still run out of room while reasoning. A truncated or empty reply is retried once. If that fails too, or the model refuses, the pair sees the usual "Couldn't make a plan this time. Please try again." and the real reason is logged.
  - The reservation check's tools (find_place, fetch_page, submit) are marked strict too. If OpenCode rejects that, the check retries that call without strict. Travel uses OneMap only, with no model call, so it is unchanged.
  - Tests use a fake client and run offline. One live test calls OpenCode for real and runs only with `pytest --live`.

## Not building
- Anything that matches people or replaces the platform they met on.
- Messaging between the pair. The plan itself is the shared object.
- Any judgement of whether the relationship will last.

## Open questions
- What is the target, and what must not get worse? (Left open on 2026-11-11; doesn't block story 1.)
- How was the 5 hours measured, and over what period? (Left open on 2026-11-11.)
