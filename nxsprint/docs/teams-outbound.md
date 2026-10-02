# Teams outbound (Power Automate Workflows webhook)

Office 365 connector webhooks are being retired, so NxSprint posts through a Power Automate flow with the trigger "When a Teams webhook request is received".

## What NxSprint sends
`POST <your flow URL>` with `Content-Type: application/json`:
```
{"channel": "teams_dm", "target": "<teams_user_id from config>", "text": "Hi Ravi, ..."}
```
`channel` is `teams_dm` for nudges or `owner_alert` for the budget alert. `target` is the member's `teams_user_id` (or the redirect marker during a test run). `text` is plain text.

## What the flow must do
1. Trigger: When a Teams webhook request is received. Use this body schema: an object with string properties `channel`, `target`, `text`.
2. Action: Post message in a chat or channel, with the text from the trigger. For the first live run, point it at one test channel and ignore `target`. For real DMs, post as the Flow bot to the user whose id equals `target`.
3. Respond with a success status. A non 2xx response makes NxSprint retry (up to 5 attempts).

I have not built or run this flow, and the exact Power Automate screens change, so treat these steps as the contract, not a click by click guide. Digests with an Adaptive Card come with Phase 6.
