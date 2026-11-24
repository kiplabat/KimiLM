---
name: sample-account
description: Use for questions about the user's account on the sample service — their items, comments to answer, or the status of submissions.
---

# Working with a sample service account

Tools (connector "Sample service"): `whoami`, `list_items`, `list_comments`, `reply_to_comment`.

- `list_items` is paged; stop paging as soon as you have what you need.
- Comments: summarise who said what. Draft replies and show them; only call `reply_to_comment` after the user
  approves the wording (the app also asks before sending).
