#!/usr/bin/env bash
#
# review-record-check.sh — decide whether a PR carries a CODE REVIEW RECORD, and FAIL
#                          CLOSED when it cannot tell.
#
# ── WHY THIS IS A SCRIPT AND NOT A HABIT ─────────────────────────────────────────────
#
# Copilot's PR review left a machine-generated artifact on every PR; when review moved
# in-session (adversarial review before the PR opens, findings recorded in the PR body),
# nothing enforced that the review HAPPENED. Measured 2026-08-30: PRs #146-#150 merged
# with zero reviews and zero review comments — `gh pr view --json reviews` returns `[]`
# on all five. The in-session review is the stronger position (its findings become
# executable checks); this script adds back the structural half: a merge path that
# refuses when no review artifact exists, so forgetting is loud instead of invisible.
#
# ── WHAT COUNTS AS A REVIEW RECORD (the contract, in precedence order) ───────────────
#
#   R-1  a PR REVIEW — anything in pulls/<n>/reviews (a human review, Copilot,
#        or a review-typed API submission). Any state counts; COMMENTED included.
#   R-2  an INLINE review comment — anything in pulls/<n>/comments. This is what
#        `/code-review --comment` posts, so the Claude review flow satisfies the
#        gate with no extra step.
#   R-3  an ISSUE comment whose body contains a line beginning `Review-Record:` —
#        the explicit manual convention, for a pasted review summary (e.g. after
#        `/code-review ultra`) or a human record. Ordinary PR chatter does NOT
#        count: a comment must opt in with the marker.
#
# A PR body section is deliberately NOT accepted: the body is written by the author
# before any review exists, so its presence proves authorship, not review.
#
# Each class is fetched ONCE, its exit status checked, and counted from that captured
# response. An empty ARRAY is a real answer (that class is absent — try the next);
# a failed or unparseable CALL is VOID, never absence.
#
# ── EXIT CODES ───────────────────────────────────────────────────────────────────────
#   0 = a review record exists (says which kind and by whom)
#   1 = NO review record — run /code-review --comment on the PR, or post a comment
#       with a `Review-Record: <what/where>` line
#   2 = VOID — could not evaluate (offline, no access, bad JSON). NEVER treat as 0.
#
# ── USAGE ────────────────────────────────────────────────────────────────────────────
#   review-record-check.sh --repo owner/name --pr 151
#   review-record-check.sh --repo owner/name --resolve-pr -- pr merge <argv...>
#       prints the ONE PR number that argv merges (flag-arity-aware; URL and branch
#       selectors resolved against the TARGET repo) or VOIDs — used by the gh() wrapper
#   review-record-check.sh --repo owner/name --pr 151 --record-waiver --reason '<why>'
#       posts the waive to the PR as a durable, HEAD-SCOPED Review-Record comment
#       (rc 2, never 1, when it cannot — a waive must keep working offline; the
#       caller warns and proceeds)
#   review-record-check.sh --repo owner/name --pr 151 --status-verdict --head <sha>
#       the server backstop's brain (review-record.yml): success for a real record,
#       success for a WAIVED record scoped to THIS head, failure otherwise, VOID
#       when it cannot evaluate (the workflow job then fails — no status = blocked)
#   review-record-check.sh --repo owner/name --pr 151 --await-status [--timeout 90]
#       bounded poll for the review-record commit status (wrapper waive path)
#   review-record-check.sh --selftest        # prove every verdict direction, hermetically
#
# ── PROVING IT WORKS ─────────────────────────────────────────────────────────────────
# --selftest stubs `gh` via a PATH shim and drives every verdict: record-absent (rc 1),
# each record class present (rc 0), chatter-only (rc 1 — the anti-vacuity arm), API
# failure and garbage JSON (rc 2), missing args (rc 2). Each arm asserts the rc AND the
# reason line. Runs as a pre-push check, so an edit to this file must re-prove all of it.
set -uo pipefail

void() { printf 'VOID: %s\n' "$1" >&2; exit 2; }
deny() { printf 'STOP: %s\n' "$1" >&2; exit 1; }

# ── selftest ─────────────────────────────────────────────────────────────────────────
if [ "${1:-}" = "--selftest" ]; then
	SELF="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/$(basename "${BASH_SOURCE[0]}")"
	command -v jq >/dev/null 2>&1 || void "selftest needs jq"
	WORK="$(mktemp -d)"; trap 'rm -rf "$WORK"' EXIT
	mkdir -p "$WORK/bin"
	# The shim answers the record endpoints, PR-view lookups, and (for the G-4 arms)
	# pr-merge-guard's head/check-runs/base reads, all from fixture files;
	# GH_FIXTURE_DIR selects the scenario. GH_FIXTURE_RC simulates a dead API.
	# Order matters: `pr view --json headRefOid` contains both "pr view" and
	# "headRefOid", so the specific cases sit above the generic one.
	cat > "$WORK/bin/gh" <<'SHIM'
#!/usr/bin/env bash
[ "${GH_FIXTURE_RC:-0}" != "0" ] && exit "$GH_FIXTURE_RC"
case "$*" in
	*"/reviews"*)  cat "$GH_FIXTURE_DIR/reviews.json" ;;
	*"/pulls/"*"/comments"*) cat "$GH_FIXTURE_DIR/inline.json" ;;
	*"api repos/"*"/issues/"*"/comments -f body=Review-Record: WAIVED"*) echo '{"id": 777}' ;;
	*"-f"*"body="*) exit 64 ;;   # any OTHER post shape is a bug: wrong endpoint or a body R-3 cannot audit (#154: the old any-post shim made these arms vacuous)
	*"/issues/"*"/comments"*) cat "$GH_FIXTURE_DIR/issue.json" ;;
	*"check-runs"*) cat "$GH_FIXTURE_DIR/checkruns.json" ;;
	*"contents/.github/workflows/review-record.yml"*) cat "$GH_FIXTURE_DIR/wf.json" 2>/dev/null || exit 1 ;;
	*"/commits/"*"/status"*) cat "$GH_FIXTURE_DIR/status.json" ;;
	*"/commits/"*) cat "$GH_FIXTURE_DIR/commit.json" ;;
	*"headRefOid"*) echo "fixturehead123" ;;
	*"baseRefName"*) echo "main" ;;
	*"pr view"*) cat "$GH_FIXTURE_DIR/prnum.txt" ;;
	*) exit 64 ;;
esac
SHIM
	# A git shim beside it lets pr-merge-guard's G-3 run offline: a repo exists,
	# the fetch succeeds, and the head is never behind.
	cat > "$WORK/bin/git" <<'SHIM'
#!/usr/bin/env bash
case "$1" in rev-parse) echo .git ;; branch) echo fixture-branch ;; esac
exit 0
SHIM
	chmod +x "$WORK/bin/gh" "$WORK/bin/git"
	mkfix() { # <name> <reviews> <inline> <issue>
		mkdir -p "$WORK/$1"
		printf '%s' "$2" > "$WORK/$1/reviews.json"
		printf '%s' "$3" > "$WORK/$1/inline.json"
		printf '%s' "$4" > "$WORK/$1/issue.json"
		printf '42\n' > "$WORK/$1/prnum.txt"
		printf '{"check_runs":[{"name":"ci","status":"completed","conclusion":"success"}]}' \
			> "$WORK/$1/checkruns.json"
		printf '{"statuses":[]}' > "$WORK/$1/status.json"
		printf '{"commit":{"committer":{"date":"2020-01-01T00:00:00Z"}}}' > "$WORK/$1/commit.json"
		printf '{"name":"review-record.yml"}' > "$WORK/$1/wf.json"
	}
	EMPTY='[]'
	REV='[{"user":{"login":"copilot-pull-request-reviewer"},"state":"COMMENTED","submitted_at":"2021-06-01T00:00:00Z"}]'
	INL='[{"user":{"login":"Bralabee"},"body":"finding: off-by-one in the loop bound","created_at":"2021-06-01T00:00:00Z"}]'
	MRK='[{"user":{"login":"Bralabee"},"author_association":"OWNER","created_at":"2021-06-01T00:00:00Z","body":"Review-Record: /code-review ultra, findings in session link"}]'
	CHAT='[{"user":{"login":"Bralabee"},"body":"will do the code review tomorrow"}]'
	WVD='[{"user":{"login":"Bralabee"},"author_association":"OWNER","created_at":"2021-06-01T00:00:00Z","body":"Review-Record: WAIVED sha=fixturehead1 — docs-only (merge waived via DOTFILES_REVIEW_WAIVE; this comment is the durable record)"}]'
	mkfix absent  "$EMPTY" "$EMPTY" "$EMPTY"
	mkfix review  "$REV"   "$EMPTY" "$EMPTY"
	mkfix inline  "$EMPTY" "$INL"   "$EMPTY"
	mkfix marker  "$EMPTY" "$EMPTY" "$MRK"
	mkfix chatter "$EMPTY" "$EMPTY" "$CHAT"
	mkfix garbage "not json at all" "$EMPTY" "$EMPTY"
	mkfix waived  "$EMPTY" "$EMPTY" "$WVD"
	# a WAIVED comment from a NON-owner (the #158 mutation showed the assoc
	# filter's fail direction was unproven: deleting it left 50/50 green)
	WVDEXT='[{"user":{"login":"driveby"},"author_association":"CONTRIBUTOR","created_at":"2021-06-01T00:00:00Z","body":"Review-Record: WAIVED sha=fixturehead1 — sneaky (merge waived via DOTFILES_REVIEW_WAIVE; this comment is the durable record)"}]'
	mkfix waivedext "$EMPTY" "$EMPTY" "$WVDEXT"
	# a real review whose timestamp PREDATES the head commit (freshness arm)
	STALEREV='[{"user":{"login":"x"},"state":"COMMENTED","submitted_at":"2019-01-01T00:00:00Z"}]'
	mkfix stale "$STALEREV" "$EMPTY" "$EMPTY"
	# a marker comment from a NON-owner/member web passer-by (association arm)
	mkfix markerext "$EMPTY" "$EMPTY" \
		'[{"user":{"login":"driveby"},"author_association":"CONTRIBUTOR","created_at":"2021-06-01T00:00:00Z","body":"Review-Record: totally reviewed, trust me"}]'
	# a STALE record PLUS a fresh head-scoped waive (the live-probe T4 shape:
	# reviewed, then pushed, then waived — the waiver must win)
	mkfix stalewaived "$STALEREV" "$EMPTY" "$WVD"
	# backstop workflow absent (await short-circuit arm)
	mkfix nowf "$EMPTY" "$EMPTY" "$EMPTY"; rm -f "$WORK/nowf/wf.json"
	# a STANDING failure status (stale pre-waive verdict: await must wait THROUGH it)
	mkfix statred "$EMPTY" "$EMPTY" "$EMPTY"
	printf '{"statuses":[{"context":"review-record","state":"failure"}]}' > "$WORK/statred/status.json"
	# --paginate emits ONE ARRAY PER PAGE, concatenated — 3 inline comments across
	# two pages must count as 3, not break the integer test (the pre-fix behaviour).
	mkfix paged "$EMPTY"$'\n'"$EMPTY" \
		'[{"user":{"login":"a"},"body":"x"}]'$'\n''[{"user":{"login":"b"},"body":"y"},{"user":{"login":"c"},"body":"z"}]' \
		"$EMPTY"

	PASSED=0; FAILED=0
	arm() { # <fixture|-> <rc-override|-> <expected-rc> <reason-regex> <label> [args...]
		local fix="$1" ghrc="$2" exp="$3" want="$4" label="$5"; shift 5
		local out rc
		out=$(PATH="$WORK/bin:$PATH" GH_FIXTURE_DIR="$WORK/$fix" GH_FIXTURE_RC="${ghrc/-/0}" \
			bash "$SELF" "$@" 2>&1); rc=$?
		if [ "$rc" = "$exp" ] && grep -Eq "$want" <<< "$out"; then
			printf '  ok    %-44s rc=%s\n' "$label" "$rc"; PASSED=$((PASSED+1))
		else
			printf '  NOT OK %-43s rc=%s (wanted rc=%s + /%s/)\n' "$label" "$rc" "$exp" "$want"
			FAILED=$((FAILED+1)); printf '%s\n' "$out" | sed 's/^/          /' | head -4
		fi
	}
	A=(--repo o/r --pr 1)
	arm absent  - 1 'STOP: NO review record'            'no artifacts anywhere -> STOP'        "${A[@]}"
	arm review  - 0 'R-1: PR review'                    'a PR review -> GO'                    "${A[@]}"
	arm inline  - 0 'R-2: inline review comment'        'an inline review comment -> GO'       "${A[@]}"
	arm marker  - 0 'R-3: Review-Record'                'a Review-Record comment -> GO'        "${A[@]}"
	arm chatter - 1 'STOP: NO review record'            'chatter without marker -> STOP'       "${A[@]}"
	arm garbage - 2 'VOID: .*not parseable'             'garbage JSON -> VOID'                 "${A[@]}"
	arm absent  1 2 'VOID: .*fetch failed'              'dead API -> VOID, never a pass'       "${A[@]}"
	arm absent  - 2 'VOID: --repo'                      'missing --repo -> VOID'               --pr 1
	arm absent  - 2 'VOID: --pr'                        'missing --pr -> VOID'                 --repo o/r
	arm paged   - 0 'R-2: .*\(3,'                       'paginated pages merge: 3 counted'     "${A[@]}"

	# ── resolver arms: the argv -> PR-number contract the wrapper trusts ─────────────
	R=(--repo o/r --resolve-pr --)
	arm absent - 0 '^151$'                'bare number is the selector'            "${R[@]}" pr merge 151 --squash
	arm absent - 0 '^151$'                'boolean flag before the number'         "${R[@]}" pr merge --squash 151
	arm absent - 0 '^42$'                 'a flag VALUE is never the selector'     "${R[@]}" pr merge --body 151
	arm absent - 0 '^151$'                'PR URL, trailing path and all'          "${R[@]}" pr merge https://github.com/o/r/pull/151/files
	arm absent - 0 '^42$'                 'branch selector resolves via the repo'  "${R[@]}" pr merge 35-fix-widths
	arm absent 1 2 'VOID: cannot resolve' 'no selector + dead API -> VOID'         "${R[@]}" pr merge

	arm absent - 0 '^42$'                 'no selector resolves the CURRENT BRANCH'  "${R[@]}" pr merge
	# Third review round: every value-taking flag `gh pr merge` has, and pflag's
	# shorthand bundles, read as the wrapper's arity table reads them. Each of these
	# VOIDed on the flag's VALUE before (the list lacked -A and read `-dR` as boolean).
	arm absent - 0 '^151$'                '-A value is never the selector'            "${R[@]}" pr merge -A bot@x.com 151
	arm absent - 0 '^151$'                '--author-email value is never the selector' "${R[@]}" pr merge --author-email bot@x.com 151
	arm absent - 0 '^151$'                'bundle -dR takes the NEXT word'            "${R[@]}" pr merge -dR o/r 151
	arm absent - 0 '^151$'                'bundle -dRo/r keeps its value attached'    "${R[@]}" pr merge -dRo/r 151
	arm absent - 0 '^151$'                'bundle -sA takes the next word'            "${R[@]}" pr merge -sA bot@x.com 151 --squash
	arm absent - 0 '^151$'                '--repo=o/r form is one word'               "${R[@]}" pr merge --repo=o/r 151

	# ── waiver-record arms: a waive must become a forge artifact, or say it could not.
	# The shim answers ONLY the exact endpoint+body shape the gate can later audit —
	# a typoed URL or a reworded body prefix now fails these arms (#154 anti-vacuity).
	arm absent - 0 'recorded waiver on o/r#1 \(comment id 777\)' 'waiver posts as a Review-Record comment' \
		--repo o/r --pr 1 --record-waiver --reason 'docs-only'
	arm waived - 0 'already recorded.*not re-posted' 'identical waiver is deduped, not re-posted' \
		--repo o/r --pr 1 --record-waiver --reason 'docs-only'
	arm absent 1 2 'VOID: cannot read.*head sha' 'waiver record offline -> VOID, caller proceeds' \
		--repo o/r --pr 1 --record-waiver --reason 'docs-only'
	arm absent - 2 'VOID: --reason'        'waiver without a reason -> VOID'       --repo o/r --pr 1 --record-waiver

	# ── the waived record must NEVER satisfy the gate (#154's top finding) ───────────
	arm waived - 1 'only WAIVED record'    'a WAIVED record is NOT a review -> STOP' "${A[@]}"
	arm absent - 0 'PROVING IT WORKS'      '--help prints the whole header'        -h

	# ── status-verdict arms: the server backstop's brain, every direction ────────────
	V=(--repo o/r --pr 1 --status-verdict)
	arm review  - 0 'success: .*R-1'          'verdict: real review -> success'        "${V[@]}" --head fixturehead123
	arm absent  - 1 'failure: no review record' 'verdict: nothing -> failure'          "${V[@]}" --head fixturehead123
	arm waived  - 0 'success: WAIVED for head fixturehead1' 'verdict: waive scoped to THIS head -> success' "${V[@]}" --head fixturehead123
	arm waived  - 1 'failure: no review record' 'verdict: waive for ANOTHER head -> failure' "${V[@]}" --head deadbeefdeadbeef
	arm garbage - 2 'VOID: record evaluation'  'verdict: unevaluable -> VOID (job fails, no status)' "${V[@]}" --head fixturehead123
	arm review  - 2 'VOID: --head'             'verdict without --head -> VOID'        "${V[@]}"
	arm stale   - 1 'failure: .*predates head' 'verdict: record OLDER than head -> failure (no stale green)' "${V[@]}" --head fixturehead123
	arm stalewaived - 0 'success: stale record superseded by an owner/member WAIVE' 'verdict: stale record + fresh head waive -> success (probe T4)' "${V[@]}" --head fixturehead123
	arm stalewaived - 1 'failure: .*predates head' 'verdict: stale record + waive for OTHER head -> failure' "${V[@]}" --head deadbeefdeadbeef
	arm waivedext - 1 'failure: no review record' 'verdict: a NON-owner WAIVED comment never counts' "${V[@]}" --head fixturehead123
	arm review  - 2 'VOID: --head must be a full sha' 'verdict: short --head -> VOID (prefix would widen the match)' "${V[@]}" --head abc1234

	# Writer/reader sha-prefix parity (#158 f7): the waiver BODY writer and the
	# verdict reader must slice the same 12 chars — extracted textually, and an
	# empty extraction is a FAILURE, never a skip (the list-parity idiom).
	wsha=$(grep -c 'sha=\${HEADSHA:0:12}' "$SELF"); rsha=$(grep -c 'sha=\${HEAD:0:12}' "$SELF")
	if [ "$wsha" = "1" ] && [ "$rsha" = "1" ]; then
		printf '  ok    %-44s writer=1 reader=1 [:0:12]\n' 'waiver sha prefix: writer and reader agree'; PASSED=$((PASSED+1))
	else
		printf '  NOT OK %-43s writer=%s reader=%s\n' 'waiver sha prefix: writer and reader agree' "$wsha" "$rsha"; FAILED=$((FAILED+1))
	fi
	arm markerext - 1 'STOP: NO review record' 'a passer-by marker comment is NOT a record -> STOP' "${A[@]}"
	arm review  - 2 'VOID: flag --head requires a value' 'trailing value-flag -> VOID, never a spin' "${V[@]}" --head

	# ── await-status arms ────────────────────────────────────────────────────────────
	mkdir -p "$WORK/statgreen"; cp "$WORK/review/"* "$WORK/statgreen/"
	printf '{"statuses":[{"context":"review-record","state":"success"}]}' > "$WORK/statgreen/status.json"
	arm statgreen - 0 'status is green'        'await: green status -> rc 0'           --repo o/r --pr 1 --await-status --timeout 5
	arm absent    - 2 "VOID: .*still 'absent'" 'await: absent status times out -> VOID (caller proceeds)' --repo o/r --pr 1 --await-status --timeout 0
	arm statred   - 2 "VOID: .*still 'failure'" 'await: STALE failure is waited through, not terminal' --repo o/r --pr 1 --await-status --timeout 0
	arm nowf      - 0 'not installed'          'await: repo without the workflow -> instant no-op'   --repo o/r --pr 1 --await-status --timeout 5

	# ── G-4 arms: pr-merge-guard's review dispatch, hermetic via the same shims ─────
	# (the guard needs a git too; the shim repo is always healthy and never behind)
	# VENDORED CONTEXT: a consumer repo carries only this script — no sibling
	# pr-merge-guard.sh, no ../bash/.bashrc — so the guard and parity arms are
	# SKIPPED there, loudly and counted (skipped-not-sampled: the canonical repo
	# always runs them; a consumer's skip line names what only canonical proves).
	GUARD="$(dirname "$SELF")/pr-merge-guard.sh"
	BASHRC="$(dirname "$SELF")/../bash/.bashrc"
	SKIPPED=0
	# How many arms a VENDORED copy skips is a constant here — and a constant nobody
	# compares drifts: it read 25 while the canonical-only tail was 35, then 38 (PR #199
	# review). The canonical run now measures the tail it stands for and fails on a
	# mismatch; _RR_TAIL_START marks where that tail begins.
	_RR_TAIL_START=$((PASSED + FAILED))
	_RR_VENDORED_SKIP=39
	if [ ! -f "$GUARD" ] || [ ! -f "$BASHRC" ]; then
		SKIPPED=$_RR_VENDORED_SKIP
		printf '  SKIP  %s arms (guard dispatch + ledger + wrapper behavioral + snapshot + slug fallback + list/ledger parity) — vendored context: no sibling pr-merge-guard.sh/.bashrc; canonical dotfiles runs them\n' "$SKIPPED"
		printf 'review-record-check selftest: passed=%s failed=%s skipped=%s\n' "$PASSED" "$FAILED" "$SKIPPED"
		[ "$FAILED" -eq 0 ] || exit 1
		exit 0
	fi
	# Every guard arm pins the ledger path (RRFILE_FIX) — by default at a path that
	# does not exist — so the machine's REAL review-repos.txt can never leak into an
	# arm and flip a control (an enrolled repo would satisfy 'unlisted stays opt-in').
	RRFILE_FIX="$WORK/no-ledger"
	armg() { # <fixture> <review-repos-env> <expected-rc> <reason-regex> <label> [guard-args...]
		local fix="$1" slug="$2" exp="$3" want="$4" label="$5"; shift 5
		local out rc
		out=$(PATH="$WORK/bin:$PATH" GH_FIXTURE_DIR="$WORK/$fix" GH_FIXTURE_RC=0 \
			DOTFILES_REVIEW_REPOS="$slug" DOTFILES_REVIEW_REPOS_FILE="$RRFILE_FIX" \
			DOTFILES_REPO_SLUG= \
			bash "$GUARD" "$@" 2>&1); rc=$?
		if [ "$rc" = "$exp" ] && grep -Eq "$want" <<< "$out"; then
			printf '  ok    %-44s rc=%s\n' "$label" "$rc"; PASSED=$((PASSED+1))
		else
			printf '  NOT OK %-43s rc=%s (wanted rc=%s + /%s/)\n' "$label" "$rc" "$exp" "$want"
			FAILED=$((FAILED+1)); printf '%s\n' "$out" | sed 's/^/          /' | head -6
		fi
	}
	G=(--repo o/r --pr 1)
	armg absent  x/y 1 'no review record.*G-4'  'guard --require-review denies on absence'  "${G[@]}" --require-review
	armg review  x/y 0 'GO:'                    'guard --require-review passes on a review' "${G[@]}" --require-review
	armg absent  o/r 1 'G-4 ON.*review-gated'   'guard AUTO-requires review for listed repo' "${G[@]}"
	armg absent  'a/b o/r' 1 'G-4 ON.*review-gated' 'guard auto-on matches ANY list member' "${G[@]}"
	armg absent  'a/b x/y' 0 'GO:'              'guard control: unlisted repos stay opt-in' "${G[@]}"
	armg absent  '' 1 'G-4 ON.*review-gated'    'guard DEFAULT list covers the OaaS slug'   --repo Bralabee/JToye_OaaS_2026 --pr 1
	armg absent  '' 1 'G-4 ON.*review-gated'    'guard DEFAULT list covers the dotfiles slug' --repo Bralabee/dotfiles --pr 1
	armg absent  '' 1 'G-4 ON.*review-gated'    'guard compare is case-insensitive'         --repo bralabee/jtoye_oaas_2026 --pr 1
	armg absent  x/y 1 'G-4 ON.*review-gated'   'guard env ADDS, never drops the built-ins' --repo Bralabee/dotfiles --pr 1
	armg garbage x/y 2 'VOID:'                  'guard G-4 VOIDs on unevaluable record'     "${G[@]}" --require-review

	# ── ledger arms: review-repos.txt enrolls at call time, additively ───────────────
	# Fail directions proven at introduction by a guard mutation (dropping
	# $FILE_REPOS from the list) flipping the two enrolling arms to NOT OK.
	printf 'o/r\n' > "$WORK/ledger-hit.txt"
	printf '# a comment\na/b\ndecline o/r 2026-08-30 scratch repo\no/r trailing junk\n' > "$WORK/ledger-miss.txt"
	RRFILE_FIX="$WORK/ledger-hit.txt"
	armg absent '' 1 'G-4 ON.*review-gated'     'guard ledger file enrolls a repo'          "${G[@]}"
	armg absent '' 1 'G-4 ON.*review-gated'     'guard ledger ADDS, built-ins stay gated'   --repo Bralabee/dotfiles --pr 1
	RRFILE_FIX="$WORK/ledger-miss.txt"
	armg absent '' 0 'GO:'                      'guard ledger comments/declines NOT enrolls' "${G[@]}"
	# CRLF is the dangerous shape (#161 review): [[:space:]] matches \r, so before
	# the tr -d '\r' fix a CRLF line passed the harvest but kept the \r in the
	# token and matched nothing — an enrolled repo merged UNGATED while every
	# green arm stayed green. Proven failing against the pre-fix guard.
	printf 'o/r\r\n' > "$WORK/ledger-crlf.txt"
	RRFILE_FIX="$WORK/ledger-crlf.txt"
	armg absent '' 1 'G-4 ON.*review-gated'     'guard CRLF ledger line still enrolls'      "${G[@]}"
	RRFILE_FIX="$WORK/no-ledger"
	armg absent '' 0 'GO:'                      'guard absent ledger changes nothing'       "${G[@]}"

	# DOTFILES_REPO_SLUG fallback: a fork's injected slug must stay auto-gated (the
	# #153 review's mutation run proved these default paths were previously untested
	# — a guard default missing dotfiles shipped with the selftest fully green).
	out=$(PATH="$WORK/bin:$PATH" GH_FIXTURE_DIR="$WORK/absent" GH_FIXTURE_RC=0 \
		DOTFILES_REVIEW_REPOS="" DOTFILES_REPO_SLUG="fork/dots" \
		DOTFILES_REVIEW_REPOS_FILE="$WORK/no-ledger" \
		bash "$GUARD" --repo fork/dots --pr 1 2>&1); rc=$?
	if [ "$rc" = "1" ] && grep -Eq 'G-4 ON.*review-gated' <<< "$out"; then
		printf '  ok    %-44s rc=%s\n' 'guard honours the DOTFILES_REPO_SLUG fallback' "$rc"; PASSED=$((PASSED+1))
	else
		printf '  NOT OK %-43s rc=%s (wanted rc=1 + G-4 ON)\n' 'guard honours the DOTFILES_REPO_SLUG fallback' "$rc"
		FAILED=$((FAILED+1)); printf '%s\n' "$out" | sed 's/^/          /' | head -4
	fi

	# List parity: the built-in review-repo pair is written twice (gh() wrapper and
	# the guard's G-4). "Keep them in step" was a comment; this arm makes it a check —
	# it extracts the literal list from BOTH lines and fails on ANY divergence,
	# including a reshaped line that would empty an extraction (never a silent skip).
	wl=$(sed -n 's/^ *local _review_repos="\$_slug \(.*\) \${DOTFILES_REVIEW_REPOS:-}"$/\1/p' "$BASHRC")
	gl=$(sed -n 's/^\tfor RSLUG in \${DOTFILES_REPO_SLUG:-Bralabee\/dotfiles} \(.*\) \${DOTFILES_REVIEW_REPOS:-} \$FILE_REPOS; do$/\1/p' "$GUARD")
	if [ -n "$wl" ] && [ "$wl" = "$gl" ]; then
		printf '  ok    %-44s [%s]\n' 'wrapper and guard default lists are IDENTICAL' "$wl"; PASSED=$((PASSED+1))
	else
		printf '  NOT OK %-43s wrapper=[%s] guard=[%s]\n' 'wrapper and guard default lists are IDENTICAL' "${wl:-EXTRACTION-EMPTY}" "${gl:-EXTRACTION-EMPTY}"
		FAILED=$((FAILED+1))
	fi

	# Wrapper BEHAVIORAL arms (#161 review round 2: a mutation deleting the
	# wrapper's whole harvest pipeline left every textual arm green — the parity
	# greps check that the lines EXIST, these prove the wrapper EXECUTES them).
	# Source the real .bashrc (non-interactive sourcing stops at the interactive
	# guard, past the gh() definition), ledger-enroll a fake repo, and point
	# DOTFILES_DIR at a gate-less dir: an enrolled repo must BLOCK fail-closed;
	# an unenrolled one must fall through to `command gh` (the shim, rc 64).
	printf 'wrap/led\n' > "$WORK/wrap-ledger.txt"
	mkdir -p "$WORK/emptydot/gates"
	# WLEDGER parametrizes the wrapper's ledger fixture (the RRFILE_FIX idiom);
	# DOTFILES_REVIEW_WAIVE is pinned EMPTY — an exported waive flipped the
	# sourced gh() into its waive branch and false-blocked every push (#161 r3).
	WLEDGER="$WORK/wrap-ledger.txt"
	# WSRC is WHAT the arm sources to obtain gh(): the real .bashrc by default, or
	# the snapshot rendering of it built below (the snapshot arms).
	WSRC="$BASHRC"
	armw() { # <target-repo> <expected-rc> <output-regex|!regex> <label>
		local tgt="$1" exp="$2" want="$3" label="$4" out rc okre=1
		out=$(PATH="$WORK/bin:$PATH" bash -c "source '$WSRC' 2>/dev/null
			DOTFILES_DIR='$WORK/emptydot' DOTFILES_REVIEW_REPOS_FILE='$WLEDGER' \
			DOTFILES_REVIEW_REPOS='' DOTFILES_REPO_SLUG= DOTFILES_REVIEW_WAIVE= \
			gh pr merge 1 -R '$tgt'" 2>&1); rc=$?
		case "$want" in
			!*) grep -Eq "${want#!}" <<< "$out" && okre=0 ;;   # must NOT match
			*)  grep -Eq "$want"     <<< "$out" || okre=0 ;;
		esac
		if [ "$rc" = "$exp" ] && [ "$okre" = 1 ]; then
			printf '  ok    %-44s rc=%s\n' "$label" "$rc"; PASSED=$((PASSED+1))
		else
			printf '  NOT OK %-43s rc=%s (wanted rc=%s + /%s/)\n' "$label" "$rc" "$exp" "$want"
			FAILED=$((FAILED+1)); printf '%s\n' "$out" | sed 's/^/          /' | head -4
		fi
	}
	armw wrap/led   1 'BLOCKED — review gate not found' 'wrapper EXECUTES the ledger harvest (gates)'
	# rc 64 is the gh SHIM's signature: the wrapper matched nothing and ran
	# `command gh` — proof of clean fall-through, with no BLOCK in sight.
	armw wrap/other 64 '!BLOCKED'                       'wrapper control: unenrolled falls through'
	# argv-shaped arms (PR #182 second review round). The target must be read
	# the way pflag and cobra read it — `-R=x`, -R inside a shorthand bundle
	# (`-dR x`, `-dRx`), a value-taking flag BEFORE the verb (`gh -t x pr merge`
	# is `pr merge` with -t as its --subject), a PR URL, a caller's IFS — and
	# DOTFILES_REPO_SLUG must ADD a slug, never replace the built-in one. Each
	# BLOCK arm reached the gh shim (rc 64) on the previous wrapper; the control
	# proves a -t VALUE shaped like -R is not read as a target.
	armv() { # <expected-rc> <output-regex|!regex> <label> -- <argv…>
		local exp="$1" want="$2" label="$3" out rc okre=1 argv; shift 3; [ "${1:-}" = -- ] && shift
		printf -v argv '%q ' "$@"
		out=$(PATH="$WORK/bin:$PATH" bash -c "source '$WSRC' 2>/dev/null
			DOTFILES_DIR='$WORK/emptydot' DOTFILES_REVIEW_REPOS_FILE='$WLEDGER' \
			DOTFILES_REVIEW_REPOS='' DOTFILES_REVIEW_WAIVE= DOTFILES_MACHINE_ROLE_FILE=/nonexistent/role \
			$argv" 2>&1); rc=$?
		case "$want" in
			!*) grep -Eq "${want#!}" <<< "$out" && okre=0 ;;
			*)  grep -Eq "$want"     <<< "$out" || okre=0 ;;
		esac
		if [ "$rc" = "$exp" ] && [ "$okre" = 1 ]; then
			printf '  ok    %-44s rc=%s\n' "$label" "$rc"; PASSED=$((PASSED+1))
		else
			printf '  NOT OK %-43s rc=%s (wanted rc=%s + /%s/)\n' "$label" "$rc" "$exp" "$want"
			FAILED=$((FAILED+1)); printf '%s\n' "$out" | sed 's/^/          /' | head -4
		fi
	}
	armv 1  'review gate not found' 'wrapper: -R=slug (= attached)'          -- gh pr merge 1 -R=wrap/led
	armv 1  'review gate not found' 'wrapper: -R inside a shorthand bundle'  -- gh pr merge 1 -dR wrap/led
	armv 1  'review gate not found' 'wrapper: bundled -dRslug attached'      -- gh pr merge 1 -dRwrap/led
	armv 1  'review gate not found' 'wrapper: value flag before the verb'    -- gh -t x pr merge 1 -R wrap/led
	armv 1  'review gate not found' 'wrapper: a PR URL fixes the repo'       -- gh pr merge https://github.com/wrap/led/pull/1
	armv 1  'review gate not found' 'wrapper: caller IFS cannot un-gate'     -- IFS=: gh pr merge 1 -R wrap/led
	armv 1  'PRIMARY machine'       'wrapper: DOTFILES_REPO_SLUG adds, never replaces' -- DOTFILES_REPO_SLUG=x/y gh pr merge 1 -R Bralabee/dotfiles
	armv 64 '!BLOCKED'              'wrapper control: a -t value shaped like -R'  -- gh pr merge 1 -R wrap/other -t -Rwrap/led
	# The dangerous harvest shapes must be proven through the WRAPPER's copy of
	# the duplicated pipeline too, not only the guard's (#161 round 3: a
	# wrapper-only regression would leave every guard-side arm green).
	printf 'wrap/led\r\n' > "$WORK/wrap-crlf.txt"
	printf '# c\ndecline wrap/led 2026-08-30 x\n' > "$WORK/wrap-decl.txt"
	WLEDGER="$WORK/wrap-crlf.txt"
	armw wrap/led   1 'BLOCKED — review gate not found' 'wrapper harvest strips CRLF too'
	WLEDGER="$WORK/wrap-decl.txt"
	armw wrap/led   64 '!BLOCKED'                       'wrapper: decline lines do not enroll'
	WLEDGER="$WORK/wrap-ledger.txt"

	# SNAPSHOT arms (measured 2026-09-04). Claude Code's Bash tool never sources
	# .bashrc: it replays a SNAPSHOT of the interactive shell's functions, built by
	# its own snapshot script (2.1.257) as
	#     declare -F | cut -d' ' -f3 | grep -vE '^_[^_]'
	# — every single-underscore function is dropped as completion noise. gh()
	# survived that filter; the helpers it calls did not. So in exactly the shells
	# that run merges, `_gh_verb_pair "$@"` was rc 127 + empty, the wrapper's first
	# test was always false, and every merge fell through to `command gh` ungated —
	# an unreviewed asao#43 merged that way, the only symptom a line reading
	# `_gh_verb_pair: command not found`. Every armw above sources the WHOLE file,
	# so all of them stayed green while the deployed gate could not fire.
	# These two arms render .bashrc through the SAME pipeline the snapshot uses
	# (nothing else from the file survives) and load gh() from that rendering. The
	# gate must still fire; the control proves the rendered wrapper is a working
	# gh() that falls through when it should, so the BLOCK is the gate and not a
	# broken shell. Proven failing (rc 64, the shim ran) against the pre-fix .bashrc,
	# where the helpers were defined BESIDE gh() instead of inside it.
	bash -c "source '$BASHRC' 2>/dev/null
		declare -F | cut -d' ' -f3 | grep -vE '^_[^_]' | while read -r f; do
			printf 'eval %q > /dev/null 2>&1\n' \"\$(declare -f \"\$f\")\"
		done" > "$WORK/snapshot.sh"
	if grep -Eq "^eval \\\$'gh \\(\\)" "$WORK/snapshot.sh"; then
		printf '  ok    %-44s\n' 'snapshot rendering carries gh() (fixture is real)'; PASSED=$((PASSED+1))
	else
		printf '  NOT OK %-43s (the two arms below would be vacuous)\n' 'snapshot rendering carries gh() (fixture is real)'; FAILED=$((FAILED+1))
	fi
	WSRC="$WORK/snapshot.sh"
	armw wrap/led   1  'BLOCKED — review gate not found' 'wrapper gate fires from the shell SNAPSHOT'
	armw wrap/other 64 '!BLOCKED'                        'snapshot control: unenrolled still falls through'
	armv 1  'review gate not found' 'snapshot: bundled -dR still gates'       -- gh pr merge 1 -dR wrap/led
	armv 1  'review gate not found' 'snapshot: value flag before the verb'    -- gh -t x pr merge 1 -R wrap/led
	WSRC="$BASHRC"

	# zsh parity (2026-09-13). macOS logs in to zsh, whose ~/.zshrc TRAMPOLINES
	# into the bash gh() rather than porting it. Prove the hop reaches the same
	# verdicts as the bash arms above — enrolled BLOCKS, unenrolled falls through
	# to the shim — and that it fails CLOSED when ~/.bashrc yields no gh(): VOID,
	# never the shim. Hermetic: a scratch HOME whose ~/.bashrc symlinks the real
	# one (the trampoline reads $HOME/.bashrc, as a Mac would). Skipped-not-
	# sampled where zsh is absent: this box has none; ubuntu-latest and macOS
	# both run these.
	ZSHRC="$(dirname "$SELF")/../zsh/.zshrc"
	if command -v zsh >/dev/null 2>&1 && [ -f "$ZSHRC" ]; then
		mkdir -p "$WORK/zhome" "$WORK/zhome-bare"
		ln -sfn "$BASHRC" "$WORK/zhome/.bashrc"
		armz() { # <home> <target-repo> <expected-rc> <output-regex|!regex> <label>
			local home="$1" tgt="$2" exp="$3" want="$4" label="$5" out rc okre=1
			out=$(HOME="$home" PATH="$WORK/bin:$PATH" zsh -c "source '$ZSHRC' 2>/dev/null
				DOTFILES_DIR='$WORK/emptydot' DOTFILES_REVIEW_REPOS_FILE='$WLEDGER' \
				DOTFILES_REVIEW_REPOS='' DOTFILES_REPO_SLUG= DOTFILES_REVIEW_WAIVE= \
				gh pr merge 1 -R '$tgt'" 2>&1); rc=$?
			case "$want" in
				!*) grep -Eq "${want#!}" <<< "$out" && okre=0 ;;
				*)  grep -Eq "$want"     <<< "$out" || okre=0 ;;
			esac
			if [ "$rc" = "$exp" ] && [ "$okre" = 1 ]; then
				printf '  ok    %-44s rc=%s\n' "$label" "$rc"; PASSED=$((PASSED+1))
			else
				printf '  NOT OK %-43s rc=%s (wanted rc=%s + /%s/)\n' "$label" "$rc" "$exp" "$want"
				FAILED=$((FAILED+1)); printf '%s\n' "$out" | sed 's/^/          /' | head -4
			fi
		}
		armz "$WORK/zhome"      wrap/led   1  'BLOCKED — review gate not found' 'zsh trampoline: enrolled repo BLOCKS (bash parity)'
		armz "$WORK/zhome"      wrap/other 64 '!BLOCKED'                        'zsh trampoline: unenrolled falls through to the shim'
		armz "$WORK/zhome-bare" wrap/led   2  'did not load'                    'zsh trampoline: no ~/.bashrc -> VOID, never the shim'
	else
		SKIPPED=$((SKIPPED + 3))
		printf '  SKIP  3 zsh parity arms — zsh not installed here (ubuntu-latest and macOS run them)\n'
	fi

	# Ledger parity: BOTH gates must read the committed ledger through the same
	# override var, or one side of the fleet silently stops honouring enrollments.
	# Extracted textually (the list-parity idiom); an empty count is a FAILURE.
	wledge=$(grep -cF '_ledger="${DOTFILES_REVIEW_REPOS_FILE:-$_dotdir/gates/review-repos.txt}"' "$BASHRC")
	gledge=$(grep -cF 'RRFILE="${DOTFILES_REVIEW_REPOS_FILE:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/review-repos.txt}"' "$GUARD")
	if [ "$wledge" = "1" ] && [ "$gledge" = "1" ]; then
		printf '  ok    %-44s wrapper=1 guard=1\n' 'wrapper and guard BOTH read the ledger'; PASSED=$((PASSED+1))
	else
		printf '  NOT OK %-43s wrapper=%s guard=%s (expected exactly 1 each)\n' 'wrapper and guard BOTH read the ledger' "$wledge" "$gledge"
		FAILED=$((FAILED+1))
	fi

	# Hold the vendored branch's skip CONSTANT to the tail it stands for: every arm after
	# _RR_TAIL_START, whether it ran or SKIPPED here, plus this arm itself (+1). A consumer
	# copy skips all of them. PR #199 review: the constant read 25 against a real 35, then
	# 38, and nothing compared them.
	_rr_tail=$(( PASSED + FAILED - _RR_TAIL_START + SKIPPED + 1 ))
	if [ "$_rr_tail" = "$_RR_VENDORED_SKIP" ]; then
		printf '  ok    %-44s %s arms\n' 'vendored skip constant matches the real tail' "$_rr_tail"; PASSED=$((PASSED+1))
	else
		printf '  NOT OK %-43s const=%s real=%s — set _RR_VENDORED_SKIP to the real value\n' 'vendored skip constant matches the real tail' "$_RR_VENDORED_SKIP" "$_rr_tail"
		FAILED=$((FAILED+1))
	fi

	# skipped= is part of the verdict line (PR #199 review): 82/0 with 3 skipped and
	# 85/0 with none are different runs, and the line must say which this was.
	printf 'review-record-check selftest: passed=%s failed=%s skipped=%s\n' "$PASSED" "$FAILED" "$SKIPPED"
	[ "$FAILED" -eq 0 ] || exit 1
	exit 0
fi

# ── real run ─────────────────────────────────────────────────────────────────────────
REPO=""; PR=""; RESOLVE=0; RECORD_WAIVER=0; REASON=""; MERGE_ARGS=()
STATUS_VERDICT=0; HEAD=""; AWAIT_STATUS=0; TIMEOUT_S=90
# A value-taking flag as the FINAL token makes `shift 2` a silent no-op and the
# loop spins forever (#156 review, finding 9 — measured: `--head` with no value
# hung until an outer timeout killed it). need_val VOIDs instead.
need_val() { [ $# -ge 2 ] || void "flag $1 requires a value"; }
while [ $# -gt 0 ]; do
	case "$1" in
		--repo) need_val "$@"; REPO="$2"; shift 2 ;;
		--pr)   need_val "$@"; PR="$2"; shift 2 ;;
		--resolve-pr) RESOLVE=1; shift ;;
		--record-waiver) RECORD_WAIVER=1; shift ;;
		--reason) need_val "$@"; REASON="$2"; shift 2 ;;
		--status-verdict) STATUS_VERDICT=1; shift ;;
		--head) need_val "$@"; HEAD="$2"; shift 2 ;;
		--await-status) AWAIT_STATUS=1; shift ;;
		--timeout) need_val "$@"; TIMEOUT_S="$2"; shift 2 ;;
		--) shift; MERGE_ARGS=("$@"); break ;;
		-h|--help) sed -n '2,/^set -uo/p' "$0" | sed '$d'; exit 0 ;;   # marker range: header edits cannot silently truncate it (#154 review)
		*) void "unknown argument: $1" ;;
	esac
done
[ -n "$REPO" ] || void "--repo owner/name is required"
for t in gh jq; do command -v "$t" >/dev/null 2>&1 || void "$t not found on PATH"; done

# fetch() VOIDs on any call/shape failure and normalizes pagination: `gh api
# --paginate` emits ONE ARRAY PER PAGE concatenated, so >100 records used to make
# `jq length` multi-line and a PRESENT record class fell through toward STOP.
# Every page must be an array; they are then merged into OUT as a single array.
fetch() { # <label> <endpoint> -> sets OUT
	local label="$1" ep="$2" rc
	OUT=$(gh api "$ep" --paginate 2>/dev/null); rc=$?
	[ "$rc" -eq 0 ] || void "$label fetch failed (gh exit $rc) — cannot evaluate, NOT a pass"
	[ -n "$OUT" ]  || void "$label response was EMPTY — cannot evaluate, NOT a pass"
	printf '%s' "$OUT" | jq -es 'all(.[]; type == "array")' >/dev/null 2>&1 \
		|| void "$label response is not parseable JSON array(s) — cannot evaluate"
	OUT=$(printf '%s' "$OUT" | jq -cs 'add // []')
}

# ── --record-waiver: make a waive DURABLE — post it to the PR as a Review-Record ─────
# A waive whose only record is a terminal line is invisible after the scrollback dies:
# the 2026-08-30 rejudgment found four waived merges with zero trace on the forge,
# which also makes the waive RATE unmeasurable. The posted body deliberately starts
# `Review-Record: WAIVED` — which the gate EXCLUDES from R-3 (a waive authorizes one
# merge, never the PR; the #154 review showed the first cut let one waived attempt
# disarm the gate for that PR forever). Idempotent: an identical record already on
# the PR is reported, not re-posted, so a retried merge cannot inflate the waive-rate
# metric. rc 0 = recorded (or already recorded) and read back; rc 2 = could not
# CONFIRM — NEVER rc 1, because a waive must keep working exactly when the API is
# down; the caller warns and proceeds.
if [ "$RECORD_WAIVER" -eq 1 ]; then
	[ -n "$PR" ]     || void "--pr <number> is required to record a waiver"
	[ -n "$REASON" ] || void "--reason '<text>' is required to record a waiver"
	# The waiver is HEAD-SCOPED: it embeds the sha it authorizes, and the server
	# backstop (--status-verdict) honours it for that head only — a new push
	# needs a fresh waive, and sha-less records (backfills) stay audit-only.
	HEADSHA=$(gh pr view "$PR" -R "$REPO" --json headRefOid -q .headRefOid 2>/dev/null); rc=$?
	[ "$rc" -eq 0 ] && [ -n "$HEADSHA" ] || void "cannot read $REPO#$PR's head sha — a waiver must be scoped to the head it authorizes"
	BODY="Review-Record: WAIVED sha=${HEADSHA:0:12} — ${REASON} (merge waived via DOTFILES_REVIEW_WAIVE; this comment is the durable record)"
	fetch "waiver dedupe read" "repos/$REPO/issues/$PR/comments"
	NDUP=$(printf '%s' "$OUT" | jq --arg b "$BODY" '[.[] | select(.body == $b)] | length')
	if [ "$NDUP" -gt 0 ]; then
		DUP=$(printf '%s' "$OUT" | jq -r --arg b "$BODY" '[.[] | select(.body == $b)][0].id // "unknown"')
		printf 'waiver already recorded on %s#%s (comment id %s) — not re-posted\n' "$REPO" "$PR" "$DUP"
		exit 0
	fi
	OUT=$(gh api "repos/$REPO/issues/$PR/comments" -f body="$BODY" 2>/dev/null); rc=$?
	[ "$rc" -eq 0 ] || void "could not post the waiver record to $REPO#$PR (gh exit $rc)"
	printf '%s' "$OUT" | jq -e '.id' >/dev/null 2>&1 \
		|| void "could not CONFIRM the waiver posted to $REPO#$PR (response unparseable) — check the PR before assuming either way, and before posting a manual duplicate"
	printf 'recorded waiver on %s#%s (comment id %s)\n' "$REPO" "$PR" "$(printf '%s' "$OUT" | jq -r '.id')"
	exit 0
fi

# ── --status-verdict: the SERVER BACKSTOP's brain (called by review-record.yml) ──────
# Decides the commit-status state for one head: a REAL review record (R-1/R-2/
# non-waived R-3) is success; a WAIVED record is success ONLY when its embedded
# sha= names THIS head (head-scoped: a new push needs a fresh waive, and CI is
# still required — the deliberate alternative to an --admin bypass, which would
# skip CI too); anything else is failure. rc 0 = success, 1 = failure, 2 = VOID
# (the workflow job then FAILS, leaving no status — a missing required status
# blocks the merge, so the backstop fails closed).
if [ "$STATUS_VERDICT" -eq 1 ]; then
	[ -n "$PR" ]   || void "--pr <number> is required for a status verdict"
	[ -n "$HEAD" ] || void "--head <sha> is required for a status verdict"
	# A short --head would WIDEN the startswith waiver match to every head
	# sharing the prefix (#158 review, finding 3) — refuse anything but a
	# full-length sha; review-record.yml always passes headRefOid.
	[ "${#HEAD}" -ge 12 ] || void "--head must be a full sha, got '${HEAD}' — a short prefix would widen the waiver match"
	# ONE issues-comments read (fetch(): VOID on failure/empty/shape — never a
	# silent empty, #158 f2) feeds BOTH the waiver decision and the marker
	# freshness scan (#158 f8: no refetch). The waiver decision is computed
	# ONCE, upfront, and every failure exit goes through fail_unless_waived —
	# a future failure shape cannot forget the waiver by construction (#158
	# f6; the probe-T4 gap was exactly a hand-patched branch missing it).
	fetch "issue comments" "repos/$REPO/issues/$PR/comments"
	ISSUES_JSON="$OUT"
	WN=$(printf '%s' "$ISSUES_JSON" | jq --arg s "Review-Record: WAIVED sha=${HEAD:0:12}" \
		'[.[] | select(.author_association == "OWNER" or .author_association == "MEMBER")
		 | select(.body | startswith($s))] | length' 2>/dev/null) || void "waiver lookup unparseable"
	WAIVED_OK=0; [ "${WN:-0}" -gt 0 ] && WAIVED_OK=1
	fail_unless_waived() { # <success-msg> <failure-msg>
		if [ "$WAIVED_OK" -eq 1 ]; then printf 'success: %s\n' "$1"; exit 0; fi
		printf 'failure: %s\n' "$2"; exit 1
	}
	RECOUT=$(bash "${BASH_SOURCE[0]}" --repo "$REPO" --pr "$PR" 2>&1); rc=$?
	case "$rc" in
		0)
			# FRESHNESS (#156 review, finding 2): a record is only good for the head
			# it postdates — otherwise one review keeps the required status green
			# for every later unreviewed push, the exact door this backstop closes.
			# ISO-8601 strings compare lexically; rebases refresh committer dates.
			# Reviews and inline comments go through fetch() (VOID, never silently
			# empty); the marker scan is owner/member-filtered here too, so an
			# outsider's marker cannot freshen a stale verdict.
			HEAD_DATE=$(gh api "repos/$REPO/commits/$HEAD" 2>/dev/null | jq -r '.commit.committer.date // empty' 2>/dev/null)
			[ -n "$HEAD_DATE" ] || void "cannot read head $HEAD's commit date — cannot judge record freshness"
			fetch "reviews" "repos/$REPO/pulls/$PR/reviews"
			RTS=$(printf '%s' "$OUT" | jq -r '.[].submitted_at // empty')
			fetch "inline comments" "repos/$REPO/pulls/$PR/comments"
			ITS=$(printf '%s' "$OUT" | jq -r '.[].created_at // empty')
			MTS=$(printf '%s' "$ISSUES_JSON" | jq -r \
				'.[] | select(.author_association == "OWNER" or .author_association == "MEMBER")
				 | select(.body | test("(^|\n)Review-Record:")) | select(.body | test("(^|\n)Review-Record: WAIVED") | not) | .created_at // empty')
			NEWEST=$(printf '%s\n%s\n%s\n' "$RTS" "$ITS" "$MTS" | sed '/^$/d' | sort | tail -1)
			if [ -z "$NEWEST" ]; then
				# e.g. a PENDING review with no submitted_at (#158 f4): the waive
				# is the documented recourse, so it must be able to win here too;
				# without one this stays VOID (fail closed), not failure.
				[ "$WAIVED_OK" -eq 1 ] && { printf 'success: un-timestamped record superseded by an owner/member WAIVE for head %s\n' "${HEAD:0:12}"; exit 0; }
				void "records exist but none carries a timestamp — cannot judge freshness (waive this head to proceed)"
			fi
			if [ "$NEWEST" \< "$HEAD_DATE" ]; then
				fail_unless_waived \
					"stale record superseded by an owner/member WAIVE for head ${HEAD:0:12} (head-scoped)" \
					"the review record predates head ${HEAD:0:12} ($NEWEST < $HEAD_DATE) — re-review or waive this head"
			fi
			printf 'success: %s (record %s >= head %s)\n' "$(printf '%s' "$RECOUT" | tail -1)" "$NEWEST" "$HEAD_DATE"
			exit 0 ;;
		1)
			fail_unless_waived \
				"WAIVED for head ${HEAD:0:12} by an owner/member (head-scoped; a new push needs a fresh waive)" \
				"no review record for head ${HEAD:0:12} — /code-review --comment, a Review-Record: comment, or a waive" ;;
		*)
			void "record evaluation could not run (rc $rc): $(printf '%s' "$RECOUT" | tail -1)" ;;
	esac
fi

# ── --await-status: bounded poll for the backstop status to go green ─────────────────
# Used by the gh() wrapper after posting a waiver: the status flips only when the
# workflow reacts to the comment, so a CLI merge straight after the post would be
# refused by branch protection. Best-effort by design — rc 2 on timeout, and the
# CALLER proceeds (GitHub itself is the enforcer and will refuse if it cares).
if [ "$AWAIT_STATUS" -eq 1 ]; then
	[ -n "$PR" ] || void "--pr <number> is required to await the status"
	# Repos WITHOUT the backstop workflow have nothing to await — burning the
	# full timeout there (and then warning about a check that does not exist)
	# was #156's finding 10. One existence probe settles it.
	if ! gh api "repos/$REPO/contents/.github/workflows/review-record.yml" >/dev/null 2>&1; then
		printf 'backstop workflow not installed in %s — nothing to await\n' "$REPO"
		exit 0
	fi
	HEADSHA=$(gh pr view "$PR" -R "$REPO" --json headRefOid -q .headRefOid 2>/dev/null); rc=$?
	[ "$rc" -eq 0 ] && [ -n "$HEADSHA" ] || void "cannot read the head sha to await its status"
	DEADLINE=$(( SECONDS + ${TIMEOUT_S:-90} ))
	while :; do
		RAW=$(gh api "repos/$REPO/commits/$HEADSHA/status" 2>/dev/null)
		ST=$(printf '%s' "$RAW" | jq -r \
			'[.statuses[] | select(.context == "review-record")][0].state // "absent"' 2>/dev/null) \
			|| ST="unreadable"
		# Only success is terminal: a standing 'failure' is exactly the STALE
		# state right after a waiver posts (the pre-waive run's verdict), so it
		# must be waited THROUGH, not treated as the answer (#156, finding 4).
		if [ "$ST" = "success" ]; then
			printf 'review-record status is green for %s\n' "${HEADSHA:0:12}"; exit 0
		fi
		[ "$SECONDS" -ge "$DEADLINE" ] && void "review-record status still '$ST' after ${TIMEOUT_S:-90}s — proceeding is the caller's call; GitHub enforces"
		sleep "${REVIEW_STATUS_POLL_INTERVAL:-10}"
	done
fi

# ── --resolve-pr: turn a `gh pr merge ...` argv into ONE PR number, or VOID ──────────
# The wrapper used to scan argv for the first digit-leading token, which made flag
# VALUES (`--body "151"`) and branch selectors (`35-fix-widths`) into PR numbers —
# a wrong-PR check is a false GO. This resolver knows `gh pr merge`'s value-taking
# flags, extracts /pull/<n> from URLs without a network call, resolves a branch
# selector (or no selector: the cwd branch) through `gh pr view -R <repo>` so the
# answer always comes from the TARGET repo, and VOIDs when it cannot resolve.
# Prints the bare number on stdout; all diagnostics go to stderr.
if [ "$RESOLVE" -eq 1 ]; then
	SEL=""
	set -- "${MERGE_ARGS[@]:-}"
	[ "${1:-}" = "pr" ] && [ "${2:-}" = "merge" ] && shift 2
	while [ $# -gt 0 ]; do
		case "$1" in
			-b|--body|-F|--body-file|-t|--subject|-A|--author-email|--match-head-commit|-R|--repo)
				shift 2 || break ;;              # value-taking flag: its value is NOT a selector
				                                 # (a trailing value-flag would loop forever — #156 f9)
			--*) shift ;;                        # boolean long flag (and any --flag=value form)
			-?*)
				# A pflag shorthand BUNDLE, read as the wrapper's arity table reads it
				# (third review round): the FIRST value-taking letter ends the bundle —
				# with a rest attached (`-dRo/r`, `-R=o/r`) that rest IS the value, with
				# nothing attached the NEXT argv word is. This list knew `-dR` only as a
				# boolean, so `-dR o/r 151` VOIDed on 'o/r', and it did not know -A at
				# all, so `-A bot@x 151` VOIDed on 'bot@x' — a legal numeric merge refused,
				# and on the waive path the durable Review-Record comment skipped.
				_b="${1#-}"; _take=0
				while [ -n "$_b" ]; do
					_l="${_b:0:1}"; _rest="${_b:1}"
					case "$_l" in
						R|t|b|F|A) [ -n "$_rest" ] || _take=1; break ;;
						*)         _b="$_rest" ;;
					esac
				done
				if [ "$_take" -eq 1 ]; then shift 2 || break; else shift; fi ;;
			pr|merge) shift ;;                   # stray subcommand words, defensively
			*) SEL="$1"; break ;;
		esac
	done
	if [[ "$SEL" =~ ^[0-9]+$ ]]; then
		printf '%s\n' "$SEL"; exit 0
	elif [[ "$SEL" =~ /pull/([0-9]+) ]]; then    # any PR URL, trailing /files or / included
		printf '%s\n' "${BASH_REMATCH[1]}"; exit 0
	else
		# No selector means the cwd branch — but its NAME must be derived and passed
		# explicitly: `gh pr view -R <repo>` with no selector errors unconditionally
		# ("argument required when using the --repo flag", measured on gh 2.98.0 by
		# the #154 review; the branch-less form was dead code that silently VOIDed
		# every selector-less waive record).
		if [ -z "$SEL" ]; then
			SEL=$(git branch --show-current 2>/dev/null)
			[ -n "$SEL" ] || void "cannot resolve which PR of $REPO is being merged (no selector, and no current branch) — pass the PR number explicitly"
		fi
		NUM=$(gh pr view "$SEL" -R "$REPO" --json number -q .number 2>/dev/null); rc=$?
	fi
	[ "$rc" -eq 0 ] && [[ "${NUM:-}" =~ ^[0-9]+$ ]] \
		|| void "cannot resolve which PR of $REPO is being merged (selector: '${SEL:-<cwd branch>}') — pass the PR number explicitly"
	printf '%s\n' "$NUM"; exit 0
fi

[ -n "$PR" ] || void "--pr <number> is required"

printf 'review-record-check  %s#%s\n' "$REPO" "$PR"

fetch "reviews" "repos/$REPO/pulls/$PR/reviews"
N=$(printf '%s' "$OUT" | jq 'length')
if [ "$N" -gt 0 ]; then
	WHO=$(printf '%s' "$OUT" | jq -r '.[0].user.login // "unknown"')
	printf 'GO: R-1: PR review present (%s review(s), first by %s)\n' "$N" "$WHO"
	exit 0
fi

fetch "inline comments" "repos/$REPO/pulls/$PR/comments"
N=$(printf '%s' "$OUT" | jq 'length')
if [ "$N" -gt 0 ]; then
	WHO=$(printf '%s' "$OUT" | jq -r '.[0].user.login // "unknown"')
	printf 'GO: R-2: inline review comment(s) present (%s, first by %s)\n' "$N" "$WHO"
	exit 0
fi

fetch "issue comments" "repos/$REPO/issues/$PR/comments"
MARK='(^|\n)Review-Record:'          # the one place the marker convention is spelled
WAIVED='(^|\n)Review-Record: WAIVED' # a waive authorizes ONE merge, never the PR:
                                     # waived records are EXCLUDED from R-3 (the #154
                                     # review showed one waived attempt otherwise
                                     # disarmed the gate for that PR forever)
# OWNER/MEMBER only (#156 review, finding 6): a marker comment from any web
# passer-by must not read as a review — here NOR in the server verdict.
N=$(printf '%s' "$OUT" | jq --arg m "$MARK" --arg w "$WAIVED" \
	'[.[] | select(.author_association == "OWNER" or .author_association == "MEMBER")
	 | select(.body | test($m)) | select(.body | test($w) | not)] | length')
NW=$(printf '%s' "$OUT" | jq --arg w "$WAIVED" '[.[] | select(.body | test($w))] | length')
if [ "$N" -gt 0 ]; then
	WHO=$(printf '%s' "$OUT" | jq -r --arg m "$MARK" --arg w "$WAIVED" \
		'[.[] | select(.author_association == "OWNER" or .author_association == "MEMBER")
		 | select(.body | test($m)) | select(.body | test($w) | not)][0].user.login // "unknown"')
	printf 'GO: R-3: Review-Record comment present (%s, first by %s)\n' "$N" "$WHO"
	exit 0
fi
if [ "$NW" -gt 0 ]; then
	deny "only WAIVED record(s) on $REPO#$PR ($NW) — a past waive is an audit trail, not a review; review the PR or waive THIS merge with DOTFILES_REVIEW_WAIVE"
fi

deny "NO review record on $REPO#$PR — run /code-review --comment on it, or post a comment with a 'Review-Record: <what/where>' line"
