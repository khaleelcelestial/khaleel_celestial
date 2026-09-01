"""
Supervisor Capability - Routes between Backend/Frontend/Testing/Deployment
based on the explicit stage_status flags, the testing agent's full report,
and the last deployment status. The LLM decides sequencing (including
sending work back to an earlier agent) - not a fixed set of conditional
edges. Its own loop-back to itself after every other agent (wired in
graph.py) is what makes this a real loop, not a one-pass pipeline.
"""

import json
import os
import re

from core.state import ProjectState
from skills.text_utils import extract_code_block, repair_truncated_json
from skills.project_registry import project_dir_for, sync_workspace_from_disk, save_project_snapshot
from core.model_router import get_router
from core.logger import get_logger

MAX_SUPERVISOR_ROUNDS = 20  # absolute backstop - the supervisor decides when
                           # it's done, this only prevents a true infinite loop

MAX_CONSECUTIVE_FAILURES = 3  # if this many agent calls IN A ROW came back with
                           # every provider/fallback exhausted, quota is almost
                           # certainly out for the whole run - stop immediately
                           # instead of burning through the remaining rounds
                           # retrying a call that will keep failing identically


def _allowed_next_agents(plan: dict) -> set:
    allowed = {"done"}
    if plan.get("database") or plan.get("contract"):
        allowed.add("database")
    if plan.get("backend"):
        allowed.add("backend")
    if plan.get("frontend"):
        allowed.add("frontend")
    if plan.get("backend") or plan.get("frontend"):
        allowed.add("testing")
    if plan.get("release"):
        allowed.add("deployment")
    return allowed


def _prerequisites_met(stage: str, plan: dict, stage_status: dict) -> bool:
    """
    Deterministic sequencing guard, independent of what the LLM (or its
    fallback) chose: a stage's real prerequisites must actually be done or
    skipped before it runs. "database" has none (it's always safe to run
    first/again). "backend"/"frontend" need database's schema/contract to
    already exist if the plan calls for one - otherwise they build against
    an empty or stale contract. "testing" needs every stage that actually
    produced code to be done/skipped first - otherwise it trivially
    "passes" against a project with zero files (a real, observed bug: E2E
    marked "done" on round 1 before anything existed). "deployment" needs
    everything, including testing, done first.
    """
    def is_ready(s: str) -> bool:
        return stage_status.get(s) in ("done", "skipped")

    needs_database = bool(plan.get("database") or plan.get("contract"))

    if stage == "database":
        return True
    if stage in ("backend", "frontend"):
        return not needs_database or is_ready("database")
    if stage == "testing":
        needed = [s for s in ("database", "backend", "frontend")
                 if (s == "database" and needs_database) or (s != "database" and plan.get(s))]
        return all(is_ready(s) for s in needed)
    if stage == "deployment":
        needed = [s for s in ("database", "backend", "frontend", "testing")
                 if (s == "database" and needs_database) or (s == "testing" and (plan.get("backend") or plan.get("frontend")))
                 or (s in ("backend", "frontend") and plan.get(s))]
        return all(is_ready(s) for s in needed)
    return True


def _deterministic_fallback(stage_status: dict, allowed: set) -> str:
    """
    Used only when the LLM call itself is unavailable (e.g. every provider
    rate-limited) - a rules-based decision so a quota outage can never look
    like "everything finished successfully". Walks stages in build order and
    routes to the first one that isn't done/skipped yet. "database" MUST be
    checked first now: Planner hands off straight to the Supervisor (it no
    longer calls database_run directly - see core/graph.py), so this is the
    only thing standing between a Supervisor-LLM outage on round 1 and
    Backend/Frontend running against a schema/contract that doesn't exist yet.
    
    Stages with status "running", "validating", or "updating" are considered
    in-progress and are checked as "pending" - they need work but might be
    actively running in their RUN/UT/VAL loop.
    """
    for stage in ("database", "backend", "frontend", "testing", "deployment"):
        status = stage_status.get(stage)
        # Treat in-progress statuses as "still needs work"
        if stage in allowed and status in ("pending", "failed", "running", "validating", "updating", None):
            return stage
    return "done"



# Generic words that would trivially co-occur between a Supervisor's reason
# and almost any issue description without actually indicating the two are
# describing the SAME problem - excluded from the significant-token overlap
# check in _reason_matches_outstanding_issue so a shared "issue"/"backend"/
# "fix" doesn't count as a match.
_GENERIC_REASON_WORDS = {
    "backend", "frontend", "database", "testing", "deployment", "issue", "issues",
    "problem", "problems", "error", "errors", "critical", "resolve", "resolved",
    "fix", "fixed", "file", "files", "found", "report", "reported", "existing",
    "should", "already", "before", "after", "again", "still", "outstanding",
    "review", "static", "check", "checks", "route", "routing", "stage", "round",
    "naming", "cause", "causing", "which",
}


def _significant_tokens(text: str) -> set:
    return {
        w.lower() for w in re.findall(r"[A-Za-z_]{5,}", text or "")
        if w.lower() not in _GENERIC_REASON_WORDS
    }


def _reason_matches_outstanding_issue(stage: str, reason: str, review_issues: list) -> bool:
    """
    True only if the Supervisor's own stated reason for re-selecting an
    already-passed stage actually corresponds to one of THAT stage's
    currently outstanding review_issues - not just "some issue lives
    somewhere under {stage}/". A stage can have several unrelated outstanding
    issues at once (e.g. backend/database.py AND backend/Dockerfile); a bare
    directory-prefix check passes as long as ANY of them exist, even if the
    reason the Supervisor actually gave names a completely different,
    already-fixed file (e.g. a stale "syntax error in
    backend/routers/__init__.py" claim). That's not new evidence - it's the
    same loop rule 2 in the prompt already warns against, just not obeyed by
    the small routing model.

    Matching is two-pronged: the reason naming the issue's filename is the
    strongest signal, but the Supervisor often paraphrases a real, current
    issue in prose instead of citing its path (confirmed live: a genuinely
    still-broken DATABASE_URL fallback got described as "Resolve critical
    issue with DATABASE_URL environment variable not being set" - no
    filename at all - and a filename-only check wrongly treated that as a
    stale/unrelated reason and blocked a legitimate re-route). So this also
    accepts a significant-token overlap between the reason and the issue's
    own description (excluding generic words both would trivially share).
    """
    reason = reason or ""
    stage_issues = [i for i in review_issues if f"{stage}/" in i.get("file", "")]
    if any(os.path.basename(i.get("file", "")) in reason for i in stage_issues if i.get("file")):
        return True
    reason_tokens = _significant_tokens(reason)
    if not reason_tokens:
        return False
    return any(reason_tokens & _significant_tokens(i.get("description", "")) for i in stage_issues)


class SupervisorCapability:
    def run(self, state: ProjectState) -> dict:
        logger = get_logger()
        logger.node_start("supervisor")

        plan = state["runtime"]["execution_plan"]
        stage_status = state["runtime"].get("stage_status", {})
        rounds = state["runtime"].get("supervisor_rounds", 0) + 1
        quality_passed = state["runtime"].get("quality_passed")
        review_issues = state["runtime"].get("review_issues", [])
        testing_report = state["runtime"].get("testing_report", "")
        deployment_status = state["runtime"].get("deployment_status", "")
        consecutive_failures = state["runtime"].get("consecutive_agent_failures", 0)

        # completed_nodes/failed_nodes both start EMPTY at the top of every
        # --update invocation (initialize_update_state builds a fresh
        # RuntimeState, it doesn't replay a prior session's history) - so
        # "in completed_nodes" means "actually ran during THIS update run",
        # not "done" carried over from a previous session on disk. A stage
        # that ran this run and currently shows "done" has therefore already
        # been given a real shot at the CURRENT change request - rule 2 uses
        # this to stop re-selecting a stage whose part of the request it
        # already satisfied, instead of re-triggering it every round forever
        # just because the raw request text still mentions it.
        # A stage's VAL node only appends "<stage>_val" (not the bare stage
        # name) to completed_nodes on a real pass - see backend.py/
        # frontend.py/database.py's check_val.
        completed_nodes = state["runtime"].get("completed_nodes", [])
        already_addressed_this_run = sorted(
            stage for stage in ("database", "backend", "frontend")
            if f"{stage}_val" in completed_nodes and stage_status.get(stage) == "done"
        )

        allowed = _allowed_next_agents(plan)
        logger.info(f"Round {rounds}/{MAX_SUPERVISOR_ROUNDS} - allowed agents: {sorted(allowed)}")
        logger.info(f"Current stage_status: {stage_status}")
        if consecutive_failures:
            logger.info(f"Consecutive agent failures: {consecutive_failures}/{MAX_CONSECUTIVE_FAILURES}")

        if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
            logger.warning(
                f"{consecutive_failures} agent calls in a row came back with every provider/fallback "
                f"exhausted - quota is out. Stopping now instead of burning through the remaining "
                f"rounds retrying a call that will keep failing the same way. Whatever's been built "
                f"is already registered - re-run `--update <project_id> \"...\"` once quota resets to "
                f"finish the rest."
            )
            next_agent = "done"
        elif rounds > MAX_SUPERVISOR_ROUNDS:
            logger.warning(f"Supervisor hit its {MAX_SUPERVISOR_ROUNDS}-round backstop - forcing done.")
            next_agent = "done"
        elif (stage_status.get("database") in ("done", "skipped") and
              stage_status.get("backend") in ("done", "skipped") and  
              stage_status.get("frontend") in ("done", "skipped") and
              stage_status.get("testing") in ("done", "skipped") and
              stage_status.get("deployment") in ("done", "skipped")):
            # All stages are done - don't loop forever on leftover cached review issues
            # Note: deployment can be "skipped" if plan.release=False, but if it's needed,
            # it must be "done" not "validating" or any intermediate status
            logger.info("All stages complete - marking done")
            next_agent = "done"
        else:
            # severity:file only (never the full description) keeps this
            # compact regardless of round count; capped at 8 so a project
            # with many failing files can't grow this list unboundedly -
            # the count in the label still shows the true total.
            issue_labels = [f"{i['severity']}:{i['file']}" for i in review_issues[:8]]
            if len(review_issues) > 8:
                issue_labels.append(f"... and {len(review_issues) - 8} more")

            # Task checklist progress (see task_status in core/state.py,
            # self-reported by database/backend/frontend's RUN nodes) - open
            # task text capped at 8 for the same reason as issue_labels
            # above, the count still shows the true total.
            tasks = state["project"].get("tasks", [])
            task_status = state["runtime"].get("task_status", {})
            done_count = sum(1 for v in task_status.values() if v)
            open_tasks = [t for i, t in enumerate(tasks) if not task_status.get(i)]
            open_task_labels = open_tasks[:8]
            if len(open_tasks) > 8:
                open_task_labels.append(f"... and {len(open_tasks) - 8} more")

            status = f"""User request / change request: {state.get("user_request", "")}
Stage status: {stage_status}
Already given a real attempt at this change request THIS run: {already_addressed_this_run or 'none yet'}
Quality passed last testing pass: {quality_passed if quality_passed is not None else 'not tested yet'}
Task checklist: {done_count}/{len(tasks)} done. Open tasks: {open_task_labels or 'none'}
Outstanding issues ({len(review_issues)}): {issue_labels or 'none'}
Testing agent's last full report: {testing_report or 'not run yet'}
Deployment agent's last full report: {deployment_status or 'not deployed yet'}"""

            system_prompt = f"""You are the supervisor of a software build team. Given the current
status, decide which agent should act next.

Available agents right now: {sorted(allowed)}
- "database": (re)generates schema.sql/openapi.yaml. Only route here if the CURRENT change request
  itself asks for a schema/API contract change (a new table/column, a new resource, a changed field) -
  never for an ordinary code fix, since it rewrites the contract every backend/frontend file depends on.
- "backend": implements/fixes the backend API
- "frontend": implements/fixes the frontend UI
- "testing": runs static checks + LLM review + real tests over what's been built so far
- "deployment": packages and deploys (docker) what's been built
- "done": everything this plan needs is built, tested with no outstanding issues, and deployed

The "Task checklist" line shows Planner's concrete task list and how many are self-reported done so
far - use the open-task text as extra evidence for WHAT is actually still missing (e.g. an open task
naming a page/endpoint that stage_status alone wouldn't tell you about), not as a routing rule by
itself - stage_status/review_issues/the request text below still decide WHICH agent to route to.

Rules (in priority order - check them top to bottom, act on the FIRST one that applies):
1. "not tested yet" is NOT a failure - it means Testing has never run. If quality_passed shows
   "not tested yet" and backend/frontend stage_status is "done" (or "skipped"), you MUST route to
   "testing" next - never to "backend"/"frontend" just because quality_passed isn't literally true.
   There is nothing to fix yet if nothing has verified there's a problem.
2. Read the user request above carefully - it can name BOTH a backend change and a frontend change in
   one sentence, and it is the ONLY place a content/logic bug gets described (e.g. "the page calls the
   wrong API endpoint" or "the UI doesn't show real data") - Testing's static checks CANNOT detect that
   kind of bug, only a syntax/undefined-name/hardcoded-URL problem, so review_issues/quality_passed may
   show nothing wrong even though the request clearly asks for a frontend fix. If the request describes
   a frontend-specific change (UI, a page/component, "Home.jsx", what's displayed/created/edited), route
   to "frontend" for that part even if frontend's stage_status already says "done" and no review_issue
   mentions it. Same logic in reverse for a backend-specific part of the request -> route to "backend".
   Do not send backend-only instructions to frontend or vice versa - each agent only acts on its own part.
   BUT: check "Already given a real attempt at this change request THIS run" first - once a stage
   appears there, it has already had its shot at the CURRENT request this run, so do NOT send it there
   again just because the raw request text still describes that same change (the text doesn't change
   between rounds, so re-reading it is not new evidence). Only route back to an already-addressed stage
   for a NEW, specific reason: a review_issue naming one of its files, a testing_report describing a
   concrete bug there, or a deployment failure tracing to its code - not a bare re-read of the original
   request.
3. docker-compose.yml, any root-level Dockerfile/compose config, container env vars injected AT
   deploy time (e.g. "VITE_API_BASE_URL in docker-compose.yml", "the backend container can't reach the
   database"), and anything about containers/networking/"the stack won't start in Docker" belongs to
   "deployment" - NEVER "backend" or "frontend". Backend/frontend can only write under backend/ or
   frontend/ respectively; docker-compose.yml lives at the project root and is generated by the
   deployment stage, so sending this to backend/frontend sends them somewhere they're
   PHYSICALLY UNABLE to fix it (confirmed, observed: backend correctly reported "already correct,
   nothing to fix" twice, then made zero changes a third time, because the one remaining ask was a
   compose-file env var it has no write access to) - it will just report "already correct" or make no
   changes forever, never actually reaching the stage that can fix it. If the request is PURELY about
   Docker/compose/deployment config with no actual backend/frontend code change described, route
   straight to "deployment" even if backend/frontend show "pending"/"failed" from an unrelated earlier
   round - there's nothing for them to do.
4. If the deployment report describes a specific CONTAINER (database, backend, or frontend) failing to
   start/crash/stay unhealthy, route to the stage that OWNS that container - never to "testing" or a
   different stage, and never just retry "deployment" again hoping it goes away on its own:
   - Database container failing to start/exit (e.g. "dependency db failed to start", "db-1 exited", a
     Postgres init/startup error) -> route to "database". Almost always schema.sql itself being broken
     (e.g. a FOREIGN KEY naming a column that doesn't exist), which only "database" can fix.
   - Backend or frontend container reported "unhealthy" WITH real container log output included in the
     report (e.g. "Error: could not resolve import X", a missing dependency, an unhandled exception,
     "connection refused" meaning the process never actually started) -> route to "backend" or
     "frontend" respectively, whichever container the logs are for. This is real application code
     failing at runtime, not a testing/static-check gap - only the owning stage can fix it. Quote or
     summarize the SPECIFIC error from the logs in your reason, not just "container unhealthy" - you
     have the real log text, use it.
   Re-running "testing" or "deployment" against the same broken container will just fail identically
   forever (confirmed, observed: this happened with the db container 3 times running across 2 rounds,
   and separately with the frontend container crash-looping on a missing npm dependency, before this
   rule existed).
5. Only choose from the agents listed above - others are excluded because the plan doesn't need them.
6. A stage marked "skipped" in stage_status needs nothing from you - never route to it.
7. If you route to "database", ALWAYS follow it with "backend" (and "frontend" too if the request
   touches what the UI shows/sends) before "testing" - their code needs to catch up to the new
   schema/contract, otherwise they're now inconsistent with it.
8. Run "testing" once backend/frontend stage_status is "done", before "deployment".
9. If quality_passed is LITERALLY false (a real Testing pass already ran and found problems) or
   there are outstanding issues, check whether those issues are NEW (fresh failures from THIS round)
   or STALE (from a previous Testing pass, not updated after the most recent backend/frontend run).
   Only send it back to the owner agent (backend/frontend) if the issues are genuinely NEW - don't
   re-route on stale cached findings that haven't been re-verified against the current code yet. Then
   route to "testing" again to confirm the fix, before "deployment".
10. If the deployment report describes a failure that traces to backend/frontend code, send it back
   there, then "testing", then "deployment" again.
11. If deployment stage_status is "done", then ALL work is complete regardless of what quality_passed
   says - deployment passing means testing already passed too (testing is a prerequisite), so cached
   quality_passed/review_issues from an earlier round are now stale. Say "done" immediately.
12. Don't say "done" while any required stage is still "pending"/"failed"/"running"/"validating"/
   "updating", or while quality_passed isn't LITERALLY true AND deployment isn't done yet (if
   deployment is done, quality must have passed to get there, so ignore stale quality_passed).

Output ONLY a JSON object: {{"next": "...", "reason": "..."}}"""

            router = get_router()
            next_agent = "done"
            decision_reason = ""
            try:
                response = router.invoke("supervisor", [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": status},
                ])
                response = extract_code_block(response, "json")
                try:
                    decision = json.loads(response)
                except json.JSONDecodeError:
                    try:
                        decision = json.loads(repair_truncated_json(response))
                    except json.JSONDecodeError:
                        # Small/local models (e.g. via the Gateway) sometimes
                        # ignore "output ONLY JSON" and wrap the object in
                        # prose, or skip fencing it - neither extract_code_block
                        # nor repair_truncated_json can help if there's no
                        # fence AND the text doesn't start with '{'. Scan for
                        # the first brace-delimited object anywhere in the
                        # response before giving up - the same tolerance
                        # already applied to Gateway tool-call parsing in
                        # agent_runtime.py for this exact failure mode.
                        brace_match = re.search(r"\{.*\}", response, re.DOTALL)
                        if not brace_match:
                            raise
                        decision = json.loads(brace_match.group(0))
                next_agent = decision.get("next", "done")
                
                # Handle case where LLM returns a list instead of single agent
                if isinstance(next_agent, list):
                    if len(next_agent) > 0:
                        logger.warning(f"Supervisor returned list {next_agent} - using first: {next_agent[0]}")
                        next_agent = next_agent[0]
                    else:
                        next_agent = "done"
                
                decision_reason = decision.get("reason", "")
                logger.info(f"Supervisor -> {next_agent}: {decision_reason}")
            except Exception as e:
                next_agent = _deterministic_fallback(stage_status, allowed)
                logger.warning(f"Supervisor LLM unavailable ({str(e)[:150]}) - "
                              f"falling back to rules-based routing -> {next_agent}")

            if next_agent not in allowed:
                logger.warning(f"Supervisor chose '{next_agent}', which isn't valid right now - defaulting to done")
                next_agent = "done"
            elif next_agent != "done" and not _prerequisites_met(next_agent, plan, stage_status):
                # Real, observed failure mode: the Supervisor's own small
                # routing model sent work to "testing" before backend/
                # frontend/database had ever run (misreading rule 1 as "not
                # tested yet" alone, ignoring "AND backend/frontend is done
                # first"), and separately sent "backend" before "database"
                # despite rule 5 - both would have backend/frontend building
                # against an empty/stale schema, or E2E trivially "passing"
                # against zero files. Override to the earliest stage that's
                # actually ready, rather than trust a routing choice that
                # skips a real prerequisite.
                fallback = _deterministic_fallback(stage_status, allowed)
                logger.warning(f"Supervisor chose '{next_agent}' but its prerequisites aren't done yet "
                              f"(stage_status={stage_status}) - overriding to '{fallback}'")
                next_agent = fallback
            elif (next_agent in ("database", "backend", "frontend")
                  and next_agent in already_addressed_this_run
                  and not _reason_matches_outstanding_issue(next_agent, decision_reason, review_issues)):
                # Real, observed loop: the Supervisor kept re-selecting a
                # stage that had ALREADY passed twice this run, citing the
                # same original request text each time (rule 2 in the prompt
                # above tells it not to do this, but a small model doesn't
                # reliably follow that instruction on its own - confirmed
                # live, 3+ rounds in a row). Deterministic backstop: if the
                # chosen stage is already in "addressed this run" and its
                # stated reason doesn't actually match one of the files an
                # outstanding review_issue names (i.e. there's no genuine NEW
                # evidence, just a re-read of the unchanged request text or a
                # stale/hallucinated reason unrelated to what's actually still
                # broken), override to the next stage that still actually
                # needs work instead of re-running a satisfied one.
                #
                # NOTE: this used to just check "does ANY outstanding issue
                # live under {stage}/" - too loose. Confirmed live: Supervisor
                # kept citing a long-fixed "syntax error in
                # backend/routers/__init__.py" every other round, and the
                # check passed anyway because OTHER real issues
                # (backend/database.py, backend/Dockerfile) also happened to
                # live under backend/ - so backend re-ran and re-wrote the
                # identical already-fixed file for ~90s while the actual
                # outstanding issues were never addressed. Matching the
                # reason text against the outstanding files themselves closes
                # that loophole.
                fallback = _deterministic_fallback(stage_status, allowed)
                logger.warning(f"Supervisor chose '{next_agent}' again, but it already passed THIS run and its "
                              f"stated reason ('{decision_reason}') doesn't match any outstanding review_issue "
                              f"file - overriding to '{fallback}' to stop re-looping on a stale/unrelated reason")
                next_agent = fallback

        logger.node_complete("supervisor")

        runtime_update = {
            "next_agent": next_agent,
            "supervisor_rounds": rounds,
            "current_stage": "supervisor",
            "completed_nodes": ["supervisor"],
            "logs": [f"Supervisor: next={next_agent}"]
        }

        # A fresh dispatch to a stage is a NEW attempt at the current
        # request, not a continuation of whatever internal RUN/UT/VAL retry
        # count that stage was at the last time it ran (possibly several
        # rounds ago, for an unrelated reason) - reset both before the
        # stage's own RUN/UT/VAL sub-loop (core/graph.py) takes over.
        if next_agent in ("database", "backend", "frontend", "testing", "deployment"):
            runtime_update["stage_attempts"] = {next_agent: 0}
            runtime_update["stage_feedback"] = {next_agent: ""}

        # Persist a resumable snapshot after EVERY round, not just once at
        # the very end of CI/CD (cicd.py already does its own save too, this
        # doesn't replace that) - otherwise a run killed/crashed anywhere
        # before deployment (the common case - self-heal can take many
        # rounds) leaves nothing for `--update` to load, even though real,
        # working files already exist on disk. Confirmed by direct testing:
        # `--update` raised "No saved project found" on a project that had
        # a fully-passing database and frontend, just because it never
        # reached deployment.
        project_id = state["project"].get("project_id", "")
        if project_id:
            project_dir = project_dir_for(project_id)
            workspace = sync_workspace_from_disk(project_dir, state["project"].get("workspace", {}))
            save_project_snapshot(
                project_dir=project_dir,
                project_id=project_id,
                user_request=state.get("user_request", ""),
                execution_plan=plan,
                requirements=state["project"].get("requirements", ""),
                architecture=state["project"].get("architecture", ""),
                acceptance_criteria=state["project"].get("acceptance_criteria", []),
                tasks=state["project"].get("tasks", []),
                workspace=workspace,
                stage_status=stage_status,
            )

        return {"runtime": runtime_update}
