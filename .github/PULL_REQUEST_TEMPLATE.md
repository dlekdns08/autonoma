<!--
Keep this short. The PR body is for reviewers, not future-you;
commit messages and CHANGELOG.md are the durable record.
-->

## Summary
<!-- What changed and why, in 1-3 bullet points. -->

-

## Test plan
<!-- How you verified this works. Bulleted checklist of what was actually run. -->

- [ ] `uv run pytest`
- [ ] `uv run ruff check src tests`
- [ ] `uv run ruff format --check src tests`
- [ ] (web changes) `npm run lint && npx tsc --noEmit && npm test`

## Risk / migration notes
<!-- Anything reviewers should look at twice: schema migrations, env var
changes, behaviour changes that could surprise existing users. Delete
this section if there's nothing notable. -->

-
