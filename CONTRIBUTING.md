# Contributing

## Getting started

```bash
git clone https://github.com/cocoleunghokei/nu-outbound-call-agent.git
cd nu-outbound-call-agent
```

## Branches

`main` is the shared branch everyone works from. Don't commit to it directly.

1. Update your local `main`:
   ```bash
   git checkout main
   git pull
   ```
2. Create a branch for your work:
   ```bash
   git checkout -b backend/short-description
   ```
3. Commit, push, and open a pull request into `main`:
   ```bash
   git push -u origin backend/short-description
   ```
4. Get at least one teammate to review it before merging.
5. After merging, delete the branch.

### Branch names

Start with the area you're working in:

- `backend/...`: server, telephony, call loop, API
- `frontend/...`: web app UI
- `docs/...`: research, specs, tech doc
- `fix/...`: bug fixes

Example: `backend/twilio-outbound-call`

## Commits

Keep commits small, with a message that says what changed, for example `Add endpoint to start a call`.

## Secrets

Never commit API keys (Twilio, voice AI platform, LLM provider). Put them in a local `.env` file, which git ignores. Once we pick the stack, we'll add a `.env.example` that lists the variable names.

## Personal data

This app will handle names, birthdays, addresses and insurance info. Use fake data for testing, and never commit real personal info or call recordings.
