# Repository workflow

For every code change in this repository:

1. Add or update regression tests that cover the changed behavior.
2. Run the relevant focused tests, then run the full local test suite.
3. Run applicable formatting, lint, and diff checks. Do not hide unrelated failures; report them clearly.
4. When the change and its tests pass, create a focused Git commit automatically without waiting for a separate request.
5. Push the commit to the current remote branch automatically.
6. Use `gh` to find every GitHub Actions run triggered by that commit. Watch all test and build workflows until each reaches a terminal state.
7. Report the final status and link any failed run. If a failure is caused by the change and can be fixed within the requested scope, fix it, retest, commit, push, and watch the new runs again.

Do not claim success while a required workflow is still queued or in progress. Never overwrite or include unrelated user changes in a commit.
