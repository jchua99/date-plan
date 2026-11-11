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

## Not building
- Anything that matches people or replaces the platform they met on.
- Messaging between the pair. The plan itself is the shared object.
- Any judgement of whether the relationship will last.

## Open questions
- What is the target, and what must not get worse? (Left open on 2026-11-11; doesn't block story 1.)
- How was the 5 hours measured, and over what period? (Left open on 2026-11-11.)
